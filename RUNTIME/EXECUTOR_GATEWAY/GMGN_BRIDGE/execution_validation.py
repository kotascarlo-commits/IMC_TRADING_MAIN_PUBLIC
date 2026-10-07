"""Persistent technical validation budget; uses the canonical execution ledger.

ISOLATED evidence cannot authorize a production transport or qualify strategy
performance. No session reset replenishes this budget.
"""
from execution_state import StateError, identity, raw

MODE = 'THREE_TRADE_VALIDATION'
DOMAINS = {'FINALIZED_CHAIN', 'ISOLATED'}


def startup_state(path=None):
    """Inspect the existing canonical ledger without initializing a writer."""
    import sqlite3
    import threading
    from pathlib import Path
    from execution_state import Ledger, ROOT, timestamp, load_config
    path=Path(path) if path is not None else ROOT/'RUNTIME/EXECUTOR_GATEWAY/GMGN_BRIDGE/execution-ledger.db'
    cfg=load_config()
    result={'mode':'SIGNAL_ONLY','broadcast':cfg['broadcast_enabled'],'signing':cfg['signing'],
            'live_trading':cfg['live_trading_enabled'],'config_identity':identity('runtime-config',cfg),
            'contract_identity':None,'wallet':None}
    if not path.exists():
        if cfg.get('schema_version')!=1:raise StateError('validation_existing_ledger_required')
        return result
    store=Ledger.__new__(Ledger)
    store.path=path;store.clock=timestamp;store._schema_loaded=2
    store._config_lock=threading.RLock();store._cfg=cfg
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=2) as c:
        c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
        store.validate_schema(c,{r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")})
        store.validate_links(c)
        contract=require(store,c)
        if contract:
            from mykonos_meme_scout.jupiter_read import public_wallet
            migration=store.meta(c,'validation_wallet_migration')
            wallet=store.meta(c,'wallet')
            if (contract['domain']!='FINALIZED_CHAIN' or not migration or migration['wallet']!=wallet
                    or wallet!=public_wallet()):raise StateError('validation_startup_wallet_evidence_required')
            foreign_inventory(store,c)
            if cfg.get('schema_version')==2 and (cfg['execution_mode']!=MODE or cfg['execution_provider']!='JUPITER_V2'):
                raise StateError('validation_startup_provider_mismatch')
            result.update(mode=MODE,wallet=wallet,contract_identity=contract['identity'])
        elif cfg.get('schema_version')!=1:raise StateError('validation_startup_contract_required')
        c.rollback()
    return result


def initialize(store, *, domain='FINALIZED_CHAIN', connection=None):
    if domain not in DOMAINS:
        raise StateError('validation_domain_unknown')
    if domain == 'ISOLATED':
        from execution_state import ROOT
        if not store.path.resolve().is_relative_to((ROOT / '.local-tools').resolve()):
            raise StateError('isolated_ledger_requires_development_directory')
    from contextlib import nullcontext
    with store.transaction() if connection is None else nullcontext(connection) as c:
        previous = store.meta(c, 'validation_contract')
        if previous is not None:
            record = require(store, c)
            if record['domain'] != domain:
                raise StateError('validation_domain_immutable')
            return record
        if (store.all(c, 'receipts') or any(e.get('sent_at') or e.get('order_id') or e['state'] != 'REJECTED'
                for e in store.all(c, 'events')) or any(
                raw(t['buy_raw']) or raw(t['remaining_raw']) for t in store.all(c, 'trades'))):
            raise StateError('validation_requires_unexecuted_ledger')
        record = {'mode': MODE, 'provider': 'JUPITER_V2', 'domain': domain,
                  'max_closed': 3, 'max_positions': 1, 'frozen_at': store.clock()}
        record['identity'] = identity('validation-contract', record)
        store.set_meta(c, 'validation_contract', record)
        store.audit(c, 'validation_contract', record)
        return record


def require(store, c):
    record = store.meta(c, 'validation_contract')
    if record is None:
        return None
    body = {k: v for k, v in record.items() if k != 'identity'}
    audits = c.execute("SELECT doc FROM audit WHERE action='validation_contract'").fetchall()
    from execution_state import canonical
    if (set(record) != {'mode', 'provider', 'domain', 'max_closed', 'max_positions', 'frozen_at', 'identity'}
            or record['mode'] != MODE or record['provider'] != 'JUPITER_V2'
            or record['domain'] not in DOMAINS or type(record['max_closed']) is not int
            or record['max_closed'] != 3 or record['max_positions'] != 1
            or record['identity'] != identity('validation-contract', body)
            or len(audits) != 1 or audits[0][0] != canonical(record)):
        raise StateError('validation_contract_corrupt')
    migration=store.meta(c,'validation_wallet_migration')
    if migration is not None:
        rows=c.execute("SELECT doc FROM audit WHERE action='validation_wallet_migration'").fetchall()
        if (len(rows)!=1 or rows[0][0]!=canonical(migration) or migration.get('contract_identity')!=record['identity']
                or migration.get('wallet')!=store.meta(c,'wallet')
                or migration.get('initial_balance_lamports')!=store.meta(c,'initial_balance_lamports')):
            raise StateError('validation_migration_evidence_corrupt')
    return record


def status(store, c):
    record = require(store, c)
    if record is None:
        return None
    trades = store.all(c, 'trades')
    events = store.all(c, 'events')
    receipts = store.all(c, 'receipts')
    closed = []
    for trade in trades:
        if trade['state'] != 'CLOSED':
            continue
        store.require_reconciliation_record(c, trade)
        legs = [e for e in events if e['trade_id'] == trade['trade_id']]
        if (len(legs) != 2 or {e['side'] for e in legs} != {'BUY', 'SELL'}
                or any(e['state'] != 'FILLED' or e.get('provider') != 'JUPITER_V2'
                       or e.get('evidence_domain') != record['domain'] for e in legs)
                or any(not any(r['event_id'] == e['event_id'] for r in receipts) for e in legs)
                or raw(trade['remaining_raw']) != 0):
            raise StateError('validation_closed_evidence_incomplete')
        closed.append(trade['trade_id'])
    if len(closed) > 3:
        raise StateError('validation_budget_exceeded')
    attempted = {e['trade_id'] for e in events if e['side'] == 'BUY' and e.get('sent_at')}
    return {**record, 'closed_count': len(closed), 'closed_trade_ids': sorted(closed),
            'attempted_count': len(attempted), 'stopped': len(closed) == 3,
            'aborted': bool(store.meta(c, 'validation_abort')),
            'started': store.meta(c, 'validation_started') is True}


def buy_guard(store, c, trade_id=None):
    value = status(store, c)
    if value is None:
        return
    if value['stopped'] or value['aborted']:
        raise StateError('validation_stopped')
    if value['attempted_count'] >= 3:
        raise StateError('validation_attempt_budget')
    if any(t['trade_id'] != trade_id and t['state'] not in {'CLOSED', 'CANDIDATE', 'REJECTED', 'FAILED'}
           for t in store.all(c, 'trades')):
        raise StateError('validation_previous_trade_not_closed')


def reconciled(store, c):
    value = status(store, c)
    if value is not None and value['stopped']:
        store.set_meta(c, 'new_buys_enabled', False)
        store.set_meta(c, 'validation_started', False)
        if store.meta(c, 'validation_completed') is None:
            store.set_meta(c, 'validation_completed', value['closed_trade_ids'])
            store.audit(c, 'validation_auto_stop', {'closed_trade_ids': value['closed_trade_ids']})


def foreign_inventory(store, c):
    record = store.meta(c, 'validation_foreign_inventory')
    if record is None:
        return {}
    from execution_state import canonical
    rows = c.execute("SELECT doc FROM audit WHERE action='validation_wallet_migration'").fetchall()
    import json
    if (len(rows) != 1 or json.loads(rows[0][0]).get('foreign_inventory') != record
            or not isinstance(record, dict) or any(raw(v) <= 0 for v in record.values())):
        raise StateError('validation_foreign_inventory_corrupt')
    return dict(record)


def migrate_unexecuted_wallet(store, snapshot, *, maintenance_guard):
    """One reviewed initialization exception; generic wallet binding stays strict.

    Caller holds the existing native runtime/maintenance fence throughout.
    No deletes, ledger clone, secret reads or wallet movements occur.
    """
    from execution_policy import owner_gate
    from execution_state import age
    from mykonos_meme_scout.jupiter_read import public_wallet
    def fresh():
        maintenance_guard()
        gate = owner_gate()
        if (gate != {'value': 'OFF', 'valid': True} or not all(store.cfg[k] is False for k in
                ('signing', 'broadcast_enabled', 'live_trading_enabled'))
                or age(snapshot['observed_at'], store.clock()) > store.cfg['reconciliation_max_age_seconds']):
            raise StateError('validation_migration_requires_fresh_read_and_all_gates_off')
    fresh()
    with store.transaction(before_commit=fresh) as c:
        contract = require(store, c)
        if contract is None:
            contract=initialize(store,domain='FINALIZED_CHAIN',connection=c)
        if not contract or contract['domain'] != 'FINALIZED_CHAIN':
            raise StateError('validation_migration_contract_required')
        if store.meta(c, 'validation_wallet_migration') is not None:
            if store.meta(c, 'wallet') != snapshot['wallet']:
                raise StateError('validation_migration_is_one_time')
            return False
        store.validate_links(c)
        trades, events = store.all(c, 'trades'), store.all(c, 'events')
        if (store.all(c, 'receipts') or any(e.get('sent_at') or e.get('order_id') or e['state'] != 'REJECTED' for e in events)
                or any(raw(t[k], signed=k == 'wallet_delta_lamports') for t in trades
                       for k in ('buy_raw', 'sold_raw', 'remaining_raw', 'wallet_delta_lamports', 'fees_lamports'))
                or store.meta(c, 'kill_reason') or snapshot['wallet'] != public_wallet()
                or snapshot.get('source') != 'LOCAL_PUBLIC_WALLET_AND_FINALIZED_RPC'
                or snapshot.get('commitment') != 'finalized' or type(snapshot.get('chain_slot')) is not int
                or snapshot['chain_slot'] <= 0 or raw(snapshot['balance_lamports']) <= raw(store.cfg['reserve_lamports'])):
            raise StateError('validation_migration_execution_or_source_conflict')
        foreign = {mint: str(raw(amount)) for mint, amount in snapshot['chain_positions'].items() if raw(amount)}
        from mykonos_meme_scout.jupiter_prepare import SOL, USDC
        if SOL in foreign or USDC in foreign:
            raise StateError('validation_migration_pair_inventory_must_be_empty')
        before = {key: store.meta(c, key) for key in ('wallet', 'wallet_balance_lamports', 'initial_balance_lamports',
            'reconciled_at', 'reconciliation_revision', 'equity', 'equity_invalid', 'new_buys_enabled')}
        record = {'contract_identity': contract['identity'], 'before': before, 'wallet': snapshot['wallet'],
                  'initial_balance_lamports': snapshot['balance_lamports'], 'foreign_inventory': foreign,
                  'snapshot_slot': snapshot['chain_slot'], 'retired_trade_ids': sorted(t['trade_id'] for t in trades)}
        store.audit(c, 'validation_wallet_migration', record)
        store.audit(c, 'equity_baseline_reset', record)
        store.set_meta(c, 'validation_wallet_migration', record)
        store.set_meta(c, 'validation_foreign_inventory', foreign)
        store.set_meta(c, 'wallet', snapshot['wallet'])
        store.set_meta(c, 'initial_balance_lamports', snapshot['balance_lamports'])
        store.set_meta(c, 'wallet_balance_lamports', snapshot['balance_lamports'])
        for key in ('reconciled_at', 'reconciliation_revision', 'equity', 'equity_invalid'):
            store.set_meta(c, key, None)
        store.set_meta(c, 'reconciliation_required', True)
        store.set_meta(c, 'new_buys_enabled', False)
        store.invalidate_valuation()
        return True


def recover_prestart_read_abort(store,snapshot,*,maintenance_guard):
    """Reviewed correction of an unstarted, wholly unexecuted read abort only."""
    from execution_policy import owner_gate
    from execution_state import age
    def fresh():
        maintenance_guard()
        if (owner_gate()!={'value':'OFF','valid':True} or not all(store.cfg[k] is False for k in
                ('signing','broadcast_enabled','live_trading_enabled'))
                or age(snapshot['observed_at'],store.clock())>store.cfg['reconciliation_max_age_seconds']):
            raise StateError('validation_prestart_recovery_not_quiescent')
    fresh()
    with store.transaction(before_commit=fresh) as c:
        contract=require(store,c)
        if store.meta(c,'validation_abort') is None:return False
        store.validate_links(c)
        events=store.all(c,'events');migration=store.meta(c,'validation_wallet_migration')
        if (not contract or contract['domain']!='FINALIZED_CHAIN' or not migration
                or store.meta(c,'validation_started') is True or store.meta(c,'validation_abort')!='validation_read_failure'
                or store.meta(c,'kill_reasons')!=['validation_read_failure'] or store.meta(c,'kill_reason')!='validation_read_failure'
                or store.all(c,'receipts') or any(e.get('sent_at') or e.get('order_id') or e.get('claim_id')
                    or e.get('send_started_at') or e.get('owner_entry_authorization') or e['state']!='REJECTED' for e in events)
                or any(raw(t[k],signed=k=='wallet_delta_lamports') for t in store.all(c,'trades')
                    for k in ('buy_raw','sold_raw','remaining_raw','wallet_delta_lamports','fees_lamports'))
                or snapshot.get('source')!='LOCAL_PUBLIC_WALLET_AND_FINALIZED_RPC' or snapshot.get('commitment')!='finalized'
                or type(snapshot.get('chain_slot')) is not int or snapshot['chain_slot']<=0
                or snapshot['wallet']!=migration['wallet'] or snapshot['balance_lamports']!=migration['initial_balance_lamports']
                or snapshot['chain_positions']!=foreign_inventory(store,c)):
            raise StateError('validation_prestart_abort_not_repairable')
        if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise StateError('validation_ledger_integrity_failed')
        failures=c.execute("SELECT id,doc FROM audit WHERE action='api_failure'").fetchall()
        if not failures:raise StateError('validation_prestart_read_audit_missing')
        store.audit(c,'validation_prestart_read_recovery',{'contract_identity':contract['identity'],
            'wallet':snapshot['wallet'],'snapshot_identity':identity(snapshot),'api_failure_audit_ids':[r['id'] for r in failures],
            'attempted_count':0,'before_abort':'validation_read_failure','second_reconciliation_required':True})
        store.set_meta(c,'validation_abort',None)
        store.set_meta(c,'kill_reason',None);store.set_meta(c,'kill_reasons',[])
        store.set_meta(c,'api_failures',0);store.set_meta(c,'validation_started',False)
        store.set_meta(c,'new_buys_enabled',False);store.set_meta(c,'reconciliation_required',True)
        store.set_meta(c,'validation_prestart_recovery_pending',True)
        return True
