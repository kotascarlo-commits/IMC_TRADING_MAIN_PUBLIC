"""Transactional execution ledger for the existing signal gateway.

This module never signs, submits or broadcasts. A ready outbox event is not a
sent order. Only an executor claim records a send, before any external I/O.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
import threading
import sqlite3
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType
from execution_lock import initialization_lock
import execution_machine as machine
from mykonos_meme_scout.execution_config import (ConfigurationError, load_execution_config,
                                                validate_execution_config)

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "config/imc-execution.json"
TERMINAL = {"CLOSED", "REJECTED", "FAILED"}
UNCERTAIN = {"TIMEOUT", "UNKNOWN_EXECUTION", "RECONCILIATION_REQUIRED"}
# Existing schema-1 state names remain readable. All mutations use this graph;
# recovery may resolve uncertainty only with the existing independent receipts.
TRANSITIONS = {
    "CANDIDATE": {"APPROVED", "REJECTED"},
    "APPROVED": {"BUY_SENT", "REJECTED"},
    "BUY_SENT": {"BUY_ACK", "BUY_FILLED"} | UNCERTAIN,
    "BUY_ACK": {"BUY_FILLED"} | UNCERTAIN,
    "BUY_FILLED": {"RECONCILIATION_REQUIRED"},
    "POSITION_OPEN": {"SELL_SENT", "EMERGENCY_EXIT", "RECONCILIATION_REQUIRED"},
    "EMERGENCY_EXIT": {"SELL_SENT", "RECONCILIATION_REQUIRED"},
    "SELL_SENT": {"SELL_ACK", "RECONCILIATION_REQUIRED"} | UNCERTAIN,
    "SELL_ACK": {"RECONCILIATION_REQUIRED"} | UNCERTAIN,
    "SELL_FILLED": {"CLOSED", "RECONCILIATION_REQUIRED"},
    **{state: UNCERTAIN | {"BUY_FILLED", "POSITION_OPEN", "SELL_FILLED", "CLOSED"}
       for state in UNCERTAIN},
    **{state: set() for state in TERMINAL},
}


class StateError(ValueError):
    pass


class LedgerConnection(sqlite3.Connection):
    """Commit guards belong to exactly one owned SQLite transaction."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.commit_guards = []
        self.after_commit = []
        self.safety_commit_deadline = None

    def arm_safety_commit(self, budget):
        if self.safety_commit_deadline is not None or not self.in_transaction:
            raise StateError('invalid_safety_transaction')
        self.safety_commit_deadline = time.monotonic() + budget
        self.set_progress_handler(lambda: int(time.monotonic() >= self.safety_commit_deadline), 1000)
        self.execute('PRAGMA busy_timeout=0')


EXIT_REASONS = ('emergency_exit', 'risk_exit', 'session_exit', 'recovery_exit',
                'stop_loss', 'trailing_stop', 'maximum_hold', 'take_profit', 'strategy_exit')


@dataclass(frozen=True)
class SellAnchor:
    evidence: tuple
    checked_at: str
    limit: float
    monotonic_deadline: float


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def age(value, current):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError()
        seconds = (datetime.fromisoformat(current) - parsed).total_seconds()
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError()
        return seconds
    except (ValueError, TypeError):
        raise StateError("invalid_timestamp") from None


def raw(value, *, signed=False):
    # API raw integers may exceed both float precision and SQLite int64.
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise StateError("invalid_raw_amount")
    text = str(value)
    if len(text) > 40 or not re.fullmatch(r"-?[0-9]+" if signed else r"[0-9]+", text):
        raise StateError("invalid_raw_amount")
    number = int(text)
    if not signed and number < 0:
        raise StateError("negative_raw_amount")
    return number


def decimal(value):
    try:
        result = Decimal(str(value))
        if isinstance(value, bool) or not result.is_finite() or result < 0:
            raise InvalidOperation()
        return result
    except (InvalidOperation, ValueError, TypeError):
        raise StateError("invalid_decimal") from None


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def identity(*parts):
    return hashlib.sha256(canonical(parts).encode()).hexdigest()


def load_config(path=CONFIG):
    try:
        return load_execution_config(path)
    except ConfigurationError as exc:
        raise StateError(str(exc)) from exc


class Ledger:
    @property
    def cfg(self):
        return self._cfg

    @cfg.setter
    def cfg(self, value):
        with self._config_lock:
            if getattr(self, '_config_pinned', False):
                raise StateError('execution_config_pinned')
            self._cfg = value

    def pin_config(self):
        """Risk construction pins a validated private copy, never a caller dict.

        Reconfiguration requires a new Ledger/Risk instance and cannot preserve
        RAM permits. Runtime authority is separate from this immutable policy.
        """
        with self._config_lock:
            if not getattr(self, '_config_pinned', False):
                try:
                    pinned = validate_execution_config(dict(self.cfg))
                except ConfigurationError as exc:
                    raise StateError('execution_config_invalid') from exc
                self._cfg = MappingProxyType(pinned)
                self._config_pinned = True

    def __init__(self, path, cfg=None, *, clock=timestamp, migrate_schema1=False):
        if type(migrate_schema1) is not bool:raise StateError('invalid_migration_request')
        self._config_lock = threading.RLock()
        self._valuation_lock = threading.RLock()
        self._valuation_generation = 0
        self._valuation_fault = False
        self.path = Path(path)
        try:
            self.cfg = validate_execution_config(cfg) if cfg is not None else load_config()
        except ConfigurationError as exc:
            raise StateError(str(exc)) from exc
        self.clock = clock
        self._schema_loaded = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with initialization_lock(self.path, self.cfg['ledger_lock_timeout_seconds']) as existed, self.transaction() as c:
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables or existed:
                self.validate_schema(c, tables)
                if self.meta(c,'schema_version') == 1:
                    if not migrate_schema1:raise StateError('schema1_migration_requires_quiesced_writers')
                    from execution_policy import owner_gate
                    gate=owner_gate()
                    if not gate['valid'] or gate['value']!='OFF':raise StateError('migration_requires_owner_gate_off')
                    self.migrate_machine(c)
                self.ensure_audit_index(c)
                self._schema_loaded = 2
                return
            schema = """
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS trades(id TEXT PRIMARY KEY, doc TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, doc TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS feedback(id TEXT PRIMARY KEY, doc TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS receipts(signature TEXT PRIMARY KEY, doc TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, at TEXT NOT NULL,
                                               trade_id TEXT, action TEXT NOT NULL, doc TEXT NOT NULL);
            """
            for statement in schema.split(";"):
                if statement.strip():
                    c.execute(statement)
            version = self.meta(c, "schema_version")
            if version not in (None, 1):
                raise StateError("unknown_ledger_schema")
            if version is None:
                self.set_meta(c, "schema_version", 2)
                self.set_meta(c, "session", identity(str(self.path), self.clock()))
                self.set_meta(c, "new_buys_enabled", True)
                self.set_meta(c, "kill_reason", None)
                self.set_meta(c, "kill_reasons", [])
                self.set_meta(c, "reconciliation_required", True)
                self.set_meta(c, "api_failures", 0)
                self.set_meta(c,'reconciliation_generation',0)
            self.install_write_guards(c)
            self._schema_loaded = 2
            from execution_positions import initialize_policy
            initialize_policy(self, c)
            self.ensure_audit_index(c)

    @staticmethod
    def ensure_audit_index(c):
        name = 'audit_action_trade_id'
        c.execute(f'CREATE INDEX IF NOT EXISTS {name} ON audit(action,trade_id,id)')
        definition = c.execute('SELECT tbl_name FROM sqlite_master WHERE type=? AND name=?',
                               ('index', name)).fetchone()
        entries = [r for r in c.execute('PRAGMA index_list(audit)') if r['name'] == name]
        columns = [r['name'] for r in c.execute(f'PRAGMA index_info({name})')]
        if (not definition or definition['tbl_name'] != 'audit' or len(entries) != 1
                or entries[0]['unique'] or entries[0]['partial']
                or columns != ['action', 'trade_id', 'id']):
            raise StateError('invalid_audit_index')

    def validate_schema(self, c, tables):
        contracts = {
            'meta': [('key','TEXT',1), ('value','TEXT',0)],
            'trades': [('id','TEXT',1), ('doc','TEXT',0)],
            'events': [('id','TEXT',1), ('doc','TEXT',0)],
            'feedback': [('id','TEXT',1), ('doc','TEXT',0)],
            'receipts': [('signature','TEXT',1), ('doc','TEXT',0)],
            'audit': [('id','INTEGER',1), ('at','TEXT',0), ('trade_id','TEXT',0),
                      ('action','TEXT',0), ('doc','TEXT',0)]}
        if not set(contracts).issubset(tables):
            raise StateError('incomplete_ledger_schema')
        for table, expected in contracts.items():
            columns = list(c.execute(f'PRAGMA table_info({table})'))
            actual = [(r['name'], r['type'].upper(), r['pk']) for r in columns]
            if actual != expected:
                raise StateError('invalid_ledger_columns:' + table)
            if any(not r['notnull'] for r in columns if r['name'] in {'doc','value','at','action'}):
                raise StateError('invalid_ledger_constraints:' + table)
            if table != 'audit' and not any(r['unique'] for r in c.execute(f'PRAGMA index_list({table})')):
                raise StateError('missing_ledger_uniqueness:' + table)
        version = self.meta(c, 'schema_version')
        if type(version) is not int or version not in (1,2):
            raise StateError('unknown_ledger_schema')
        if not isinstance(self.meta(c,'session'), str) or not self.meta(c,'session'):
            raise StateError('missing_ledger_session')
        for key in ('new_buys_enabled','reconciliation_required'):
            if type(self.meta(c,key)) is not bool:
                raise StateError('invalid_ledger_metadata:' + key)
        count = self.meta(c,'api_failures')
        reasons = self.meta(c,'kill_reasons')
        if type(count) is not int or count < 0 or not isinstance(reasons,list) or any(not isinstance(x,str) or not x for x in reasons):
            raise StateError('invalid_ledger_metadata')
        if self.meta(c,'kill_reason') != (reasons[0] if reasons else None):
            raise StateError('inconsistent_ledger_kill_reasons')
        if version==1:
            for row in c.execute('SELECT doc FROM trades'):
                self.validate_legacy('trades',json.loads(row[0]))
        else:
            self.validate_write_guards(c)
            generation=self.meta(c,'reconciliation_generation')
            if type(generation) is not int or generation<0:raise StateError('corrupt_reconciliation_generation')
            self.all(c,'trades')
            self.validate_links(c)
        self.all(c,'events')

    def migrate_machine(self,c):
        """Atomic schema-1 upgrade. Never derives terminality from quantity alone."""
        generation_row=c.execute("SELECT value FROM meta WHERE key='reconciliation_generation'").fetchone()
        migration_generation=json.loads(generation_row[0]) if generation_row else 0
        if type(migration_generation) is not int or migration_generation<0:
            raise StateError('corrupt_reconciliation_generation')
        events=self.all(c,'events')
        receipts=self.all(c,'receipts')
        updates=[]
        for row in c.execute('SELECT id,doc FROM trades ORDER BY rowid'):
            doc=json.loads(row['doc']);self.validate_legacy('trades',doc)
            if doc['trade_id']!=row['id'] or 'execution_state' in doc:
                raise StateError('ambiguous_machine_migration')
            attempts=[e for e in events if e['trade_id']==doc['trade_id']]
            sent=[e for e in attempts if e.get('sent_at')]
            outstanding=[e for e in sent if e['state'] in {'SENT','ACK','PARTIAL','UNKNOWN'}]
            if len(outstanding)>1:raise StateError('ambiguous_machine_migration')
            proofs=[r['proof'] for r in receipts if any(e['event_id']==r['event_id'] for e in attempts)]
            if proofs:
                bought=sum(raw(p['token_delta_raw'],signed=True) for p in proofs if p['side']=='BUY')
                sold=-sum(raw(p['token_delta_raw'],signed=True) for p in proofs if p['side']=='SELL')
                cash=sum(raw(p['wallet_delta_lamports'],signed=True) for p in proofs)
                fees=sum(raw(p['fees_lamports']) for p in proofs)
                if (bought!=raw(doc['buy_raw']) or sold!=raw(doc['sold_raw']) or
                        cash!=raw(doc['wallet_delta_lamports'],signed=True) or fees!=raw(doc['fees_lamports'])):
                    raise StateError('migration_receipt_quantity_disagreement')
            elif raw(doc['buy_raw']) or raw(doc['sold_raw']) or raw(doc['wallet_delta_lamports'],signed=True):
                raise StateError('migration_position_without_receipts')
            for event in sent:
                bound=[p for p in proofs if p['event_id']==event['event_id']]
                if (sum(raw(p['swap_input_raw']) for p in bound)!=raw(event['filled_input_raw']) or
                        sum(raw(p['swap_output_raw']) for p in bound)!=raw(event['filled_output_raw'])):
                    raise StateError('migration_event_receipt_disagreement')
                if event['state']=='FILLED' and (not event['order_id'] or not bound or
                        raw(event['filled_input_raw'])!=raw(event['actual_input_raw'])):
                    raise StateError('migration_terminal_order_without_evidence')
            old=doc['state'];active=outstanding[0] if outstanding else (sent[-1] if sent else None)
            if old in {'CANDIDATE','APPROVED','REJECTED','FAILED'}:
                if sent:raise StateError('migration_terminal_or_candidate_with_order')
                target={'APPROVED':'READY','FAILED':'REJECTED'}.get(old,old)
                active=next((e for e in reversed(attempts) if e['side']=='BUY'),None)
            elif outstanding:
                if old in {'CLOSED','SELL_FILLED','POSITION_OPEN','EMERGENCY_EXIT'}:
                    raise StateError('migration_unresolved_position_state')
                target=active['side']+({'BUY_SENT':'_REQUESTED','SELL_SENT':'_REQUESTED',
                    'BUY_ACK':'_ACKNOWLEDGED','SELL_ACK':'_ACKNOWLEDGED'}.get(old,
                    '_PARTIAL' if old in {'BUY_FILLED','RECONCILIATION_REQUIRED'} and active['state']=='PARTIAL' else '_UNKNOWN'))
            else:
                if not active or active['state']!='FILLED':raise StateError('migration_missing_terminal_order')
                target='CLOSED' if old=='CLOSED' else 'RECONCILING'
            machine.seed(doc,state=target,side=active['side'] if active else None,
                         event_id=active['event_id'] if active else None)
            doc['revision']=1
            doc['exit_requested']=old=='EMERGENCY_EXIT'
            if self.meta(c, 'position_exit:' + doc['trade_id']) is not None:
                if raw(doc['buy_raw']) <= 0 or not any(p['side']=='BUY' for p in proofs):
                    raise StateError('migration_exit_without_buy_evidence')
                doc['exit_requested']=True
                self.exit_reason(c, doc)
            doc['rejection_kind']='FAILED' if old=='FAILED' else None
            doc['unknown_reason']='TIMEOUT' if old=='TIMEOUT' else ('RECOVERY' if target.endswith('_UNKNOWN') else '')
            doc['terminal_unreconciled']=target=='RECONCILING' and old in {'POSITION_OPEN','SELL_FILLED'}
            if target=='RECONCILING':
                doc['reconciliation_after_generation']=migration_generation+1
                doc['position_reconciliation_revision']=None
            doc['state']=machine.projection(doc)
            self.validate('trades',doc)
            updates.append((canonical(machine.storage(doc,StateError)),row['id'],row['doc']))
        for text,tid,original in updates:
            if c.execute('UPDATE trades SET doc=? WHERE id=? AND doc=?',(text,tid,original)).rowcount!=1:
                raise StateError('migration_source_changed')
        self.set_meta(c,'schema_version',2)
        self.set_meta(c,'reconciliation_generation',migration_generation)
        self.install_write_guards(c)
        self.set_meta(c,'reconciliation_required',True)
        self.audit(c,'schema_migration',{'from':1,'to':2,'trade_count':len(updates),
                   'source_digest':identity([original for _,_,original in updates])})
        self.validate_links(c)

    def validate_links(self,c):
        """Validate persisted lifecycle/order/receipt identities as one ledger."""
        trades=self.all(c,'trades');events=self.all(c,'events');receipts=self.all(c,'receipts')
        by_trade={t['trade_id']:t for t in trades};by_event={e['event_id']:e for e in events}
        from execution_positions import require_risk_trigger, require_policy, require_tracking, recoveries
        recoveries(self, c)
        tracking_ids={row[0].removeprefix('position_track:')
                      for row in c.execute("SELECT key FROM meta WHERE key GLOB 'position_track:*'")}
        if not tracking_ids.issubset(by_trade):
            raise StateError('orphan_position_tracking')
        sent_buy_ids={e['trade_id'] for e in events if e['side']=='BUY' and e.get('sent_at')}
        if tracking_ids - sent_buy_ids:
            raise StateError('position_tracking_without_claim')
        if self.meta(c, 'position_policy') is not None or tracking_ids:
            require_policy(self, c)
            for tid in sent_buy_ids:
                require_tracking(self, c, by_trade[tid])
        if any(row[0].removeprefix('position_risk_exit:') not in by_trade
               for row in c.execute("SELECT key FROM meta WHERE key GLOB 'position_risk_exit:*'")):
            raise StateError('orphan_position_risk_trigger')
        if any(row[0].removeprefix('position_exit:') not in by_trade
               for row in c.execute("SELECT key FROM meta WHERE key GLOB 'position_exit:*'")):
            raise StateError('orphan_position_exit_intent')
        proof_groups={}
        for row in c.execute('SELECT signature,doc FROM receipts'):
            receipt=json.loads(row['doc']);proof=receipt.get('proof',{});event=by_event.get(receipt.get('event_id'))
            if (not event or not isinstance(proof,dict) or proof.get('signature')!=row['signature']
                    or any(proof.get(k)!=event.get(k) for k in ('event_id','mint','side','wallet','order_id'))):
                raise StateError('corrupt_receipt_identity')
            proof_groups.setdefault(event['event_id'],[]).append(proof)
        order_ids=set()
        for event in events:
            trade=by_trade.get(event['trade_id'])
            if not trade or event['mint']!=trade['mint']:raise StateError('corrupt_event_trade_binding')
            if event.get('provider')=='JUPITER_V2':
                from execution_jupiter import validate_persisted_event
                validate_persisted_event(self,c,event)
            if event['order_id']:
                if event['order_id'] in order_ids:raise StateError('corrupt_duplicate_order_binding')
                order_ids.add(event['order_id'])
            proofs=proof_groups.get(event['event_id'],[])
            if (sum(raw(p['swap_input_raw']) for p in proofs)!=raw(event['filled_input_raw']) or
                    sum(raw(p['swap_output_raw']) for p in proofs)!=raw(event['filled_output_raw'])):
                raise StateError('corrupt_event_receipt_amounts')
            if event['state']=='FILLED' and (not event['order_id'] or not proofs or
                    raw(event['actual_input_raw'])!=raw(event['filled_input_raw']) or
                    raw(event['filled_output_raw'])<raw(event['quote']['minimum_output_raw'])):
                raise StateError('corrupt_terminal_order_evidence')
            if event['state']=='FILLED':self.validate_terminal_evidence(event,proofs,current=self.clock())
        for trade in trades:
            self.exit_reason(c, trade)
            require_risk_trigger(self, c, trade)
            attempts=[e for e in events if e['trade_id']==trade['trade_id']]
            active=by_event.get(trade['execution_event_id'])
            if active and (active['trade_id']!=trade['trade_id'] or active['side']!=trade['execution_side']):
                raise StateError('corrupt_active_attempt_binding')
            phase=trade['execution_state']
            expected={'BUY_REQUESTED':'SENT','SELL_REQUESTED':'SENT',
                      'BUY_ACKNOWLEDGED':'ACK','SELL_ACKNOWLEDGED':'ACK',
                      'BUY_PARTIAL':'PARTIAL','SELL_PARTIAL':'PARTIAL',
                      'BUY_FILLED':'FILLED','SELL_FILLED':'FILLED','RECONCILING':'FILLED'}
            if phase in expected and (not active or active['state']!=expected[phase]):
                raise StateError('corrupt_execution_event_state')
            if phase.endswith('_UNKNOWN') and (not active or active['state'] not in {'SENT','ACK','PARTIAL','UNKNOWN'}):
                raise StateError('corrupt_unknown_order_binding')
            if phase in {'CANDIDATE','RISK_CHECK','READY','REJECTED'} and any(e.get('sent_at') for e in attempts):
                raise StateError('corrupt_candidate_with_sent_order')
            if phase in {'POSITION_OPEN','CLOSED','RECONCILING'} and any(
                    e['state'] in {'SENT','ACK','PARTIAL','UNKNOWN'} for e in attempts):
                raise StateError('unresolved_order_hidden_by_lifecycle')
            if phase in {'POSITION_OPEN','CLOSED'}:self.require_reconciliation_record(c,trade)
            proofs=[p for e in attempts for p in proof_groups.get(e['event_id'],[])]
            if (sum(raw(p['token_delta_raw'],signed=True) for p in proofs if p['side']=='BUY')!=raw(trade['buy_raw']) or
                    -sum(raw(p['token_delta_raw'],signed=True) for p in proofs if p['side']=='SELL')!=raw(trade['sold_raw']) or
                    sum(raw(p['wallet_delta_lamports'],signed=True) for p in proofs)!=raw(trade['wallet_delta_lamports'],signed=True) or
                    sum(raw(p['fees_lamports']) for p in proofs)!=raw(trade['fees_lamports'])):
                raise StateError('corrupt_trade_receipt_amounts')

    @staticmethod
    def require_reconciliation_record(c,trade):
        revision=trade.get('position_reconciliation_revision')
        if not isinstance(revision,str) or not revision:raise StateError('position_reconciliation_evidence_missing')
        records=[json.loads(r[0]) for r in c.execute("SELECT doc FROM audit WHERE action='reconciliation'")]
        record=next((r for r in records if r.get('revision')==revision),None)
        if (not record or type(record.get('generation')) is not int or
                record['generation']<trade['reconciliation_after_generation'] or
                record.get('trade_positions',{}).get(trade['trade_id'])!=trade['remaining_raw'] or
                record.get('observation',{}).get('wallet')!=trade['wallet']):
            raise StateError('position_reconciliation_evidence_mismatch')

    @staticmethod
    def install_write_guards(c):
        for name,sql in machine.WRITE_GUARDS.items():
            if not c.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND name=?",(name,)).fetchone():
                c.execute(sql)
        Ledger.validate_write_guards(c)

    @staticmethod
    def validate_write_guards(c):
        for name,expected in machine.WRITE_GUARDS.items():
            row=c.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?",(name,)).fetchone()
            if not row or row[0].strip()!=expected.strip():
                raise StateError('invalid_machine_write_guard')

    @staticmethod
    def validate_terminal_evidence(event,proofs,*,current):
        record=event.get('terminal_evidence')
        if not isinstance(record,dict) or type(record.get('contract_version')) is not int or record['contract_version']!=1:
            raise StateError('missing_persisted_terminal_evidence')
        if (record.get('order_terminal') is not True or record.get('commitment')!='finalized' or
                not isinstance(record.get('source'),str) or not record['source'] or
                record.get('receipt_signatures')!=sorted(p['signature'] for p in proofs) or
                any(record.get(k)!=event.get(k) for k in ('event_id','order_id','request_id','wallet','mint','side',
                                                         'filled_input_raw','filled_output_raw'))):
            raise StateError('corrupt_terminal_evidence')
        age(record.get('observed_at'),current)

    @contextmanager
    def transaction(self, *, deadline=None, before_commit=None):
        timeout = self.cfg['ledger_lock_timeout_seconds']
        if deadline is not None:
            if type(deadline) not in (int, float) or not math.isfinite(deadline):
                raise StateError('invalid_read_deadline')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise StateError('reconciliation_read_deadline')
            timeout = min(timeout, remaining)
        c = sqlite3.connect(self.path, timeout=timeout, isolation_level=None, factory=LedgerConnection)
        c.row_factory = sqlite3.Row
        try:
            c.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
            c.execute("PRAGMA synchronous=FULL")
            c.execute("BEGIN IMMEDIATE")
            if deadline is not None:
                c.execute('PRAGMA busy_timeout=0')
                c.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            if self._schema_loaded==2 and self.meta(c,'schema_version')!=2:
                raise StateError('unknown_ledger_schema')
            if self._schema_loaded==2:self.validate_write_guards(c)
            if self._schema_loaded==2:
                generation=self.meta(c,'reconciliation_generation')
                if type(generation) is not int or generation<0:raise StateError('corrupt_reconciliation_generation')
            yield c
            if deadline is None and before_commit is None and not c.commit_guards and c.safety_commit_deadline is None:
                c.commit()
            else:
                # A shared reader can block COMMIT after BEGIN succeeded.
                # Repeat only COMMIT, never application/feedback/order work.
                commit_deadline = (c.safety_commit_deadline if c.safety_commit_deadline is not None else
                                   deadline if deadline is not None else time.monotonic() + timeout)
                c.execute('PRAGMA busy_timeout=0')
                while True:
                    if before_commit is not None:
                        before_commit()
                    for guard in c.commit_guards:
                        guard()
                    if time.monotonic() >= commit_deadline:
                        raise StateError('reconciliation_read_deadline')
                    try:
                        c.commit()
                        break
                    except sqlite3.OperationalError as error:
                        if getattr(error, 'sqlite_errorcode', None) != sqlite3.SQLITE_BUSY or not c.in_transaction:
                            raise
                        time.sleep(min(.01, max(0, commit_deadline-time.monotonic())))
            for callback in c.after_commit:
                callback()
        except BaseException:
            c.set_progress_handler(None, 0)
            c.rollback()
            raise
        finally:
            c.commit_guards.clear()
            c.after_commit.clear()
            c.set_progress_handler(None, 0)
            c.close()

    @staticmethod
    def meta(c, key):
        r = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else None

    @staticmethod
    def set_meta(c, key, value):
        c.execute("INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (key, canonical(value)))

    @staticmethod
    def get(c, table, key):
        if table not in {"trades", "events", "feedback", "receipts"}:
            raise StateError("unknown_table")
        column = "signature" if table == "receipts" else "id"
        r = c.execute(f"SELECT doc FROM {table} WHERE {column}=?", (key,)).fetchone()
        if not r:return None
        doc=machine.read(r[0],StateError) if table=='trades' else json.loads(r[0])
        return Ledger.validate(table,doc)

    @staticmethod
    def validate(table, doc):
        if table=='trades':machine.validate(doc,StateError)
        Ledger.validate_legacy(table,doc)
        if table=='trades':
            phase=doc['execution_state'];bought=raw(doc['buy_raw']);remaining=raw(doc['remaining_raw'])
            if phase in {'POSITION_OPEN','SELL_REQUESTED','SELL_ACKNOWLEDGED'} and remaining<=0:
                raise StateError('execution_requires_confirmed_position')
            if phase in {'CLOSED','SELL_FILLED'} and (remaining or bought<=0):
                raise StateError('execution_requires_closed_quantity')
            if phase in {'BUY_FILLED','BUY_PARTIAL'} and bought<=0:
                raise StateError('execution_requires_confirmed_buy_quantity')
        return doc

    @staticmethod
    def validate_legacy(table, doc):
        if not isinstance(doc, dict):
            raise StateError("corrupt_ledger_document")
        if table == "trades":
            try:
                if doc["state"] not in TERMINAL | UNCERTAIN | {"CANDIDATE", "APPROVED", "BUY_SENT", "BUY_ACK", "BUY_FILLED", "POSITION_OPEN", "SELL_SENT", "SELL_ACK", "SELL_FILLED", "EMERGENCY_EXIT"}:
                    raise StateError("corrupt_trade_state")
                bought, sold, remaining = (raw(doc[k]) for k in ("buy_raw", "sold_raw", "remaining_raw"))
                if bought - sold != remaining or (doc["state"] in TERMINAL and remaining != 0):
                    raise StateError("corrupt_position_quantity")
                raw(doc["wallet_delta_lamports"], signed=True)
                if not doc["trade_id"] or not doc["mint"]:
                    raise StateError("corrupt_trade_identity")
            except KeyError:
                raise StateError("corrupt_trade_schema") from None
        if table == "events" and (doc.get("state") not in {"READY", "SENT", "ACK", "PARTIAL", "UNKNOWN", "FILLED", "REJECTED"} or doc.get("side") not in {"BUY", "SELL"}):
            raise StateError("corrupt_event_state")
        return doc

    @staticmethod
    def put(c, table, key, doc):
        if table not in {"trades", "events", "feedback", "receipts"}:
            raise StateError("unknown_table")
        column = "signature" if table == "receipts" else "id"
        if table=='trades':
            Ledger.validate(table,doc)
            if key!=doc['trade_id']:raise StateError('trade_identity_changed')
            current=c.execute('SELECT doc FROM trades WHERE id=?',(key,)).fetchone()
            if current:
                old=json.loads(current[0])
                phase=doc['execution_state'];prior=old['execution_state']
                if phase!=prior and phase not in machine.GRAPH[prior]:
                    raise StateError('illegal_execution_transition:'+prior+':'+phase)
                if phase!=prior and phase in {'POSITION_OPEN','CLOSED'}:
                    if (Ledger.meta(c,'reconciliation_required') is not False or
                            doc.get('position_reconciliation_revision')!=Ledger.meta(c,'reconciliation_revision')):
                        raise StateError('transition_requires_reconciliation_evidence')
                    Ledger.require_reconciliation_record(c,doc)
                if doc['execution_event_id']!=old['execution_event_id'] or doc['execution_side']!=old['execution_side']:
                    bind_buy=prior==phase=='READY' and old['execution_event_id'] is None and doc['execution_side']=='BUY'
                    bind_sell=prior=='POSITION_OPEN' and phase=='SELL_REQUESTED' and doc['execution_side']=='SELL'
                    if not (bind_buy or bind_sell):raise StateError('execution_attempt_changed')
                    event=Ledger.get(c,'events',doc['execution_event_id'])
                    expected='READY' if bind_buy else 'SENT'
                    if (not event or event['state']!=expected or event['trade_id']!=key or
                            event['mint']!=doc['mint'] or event['side']!=doc['execution_side']):
                        raise StateError('invalid_attempt_binding')
                if doc['reconciliation_after_generation']!=old['reconciliation_after_generation']:
                    raise StateError('reconciliation_generation_changed')
                if any(doc.get(k)!=old.get(k) for k in ('trade_id','mint','signal','session','created_at')):
                    raise StateError('trade_identity_changed')
                if old.get('wallet') and doc.get('wallet')!=old['wallet']:
                    raise StateError('trade_wallet_changed')
                if (machine.digest(current[0]) != doc.get('_original_doc_hash')
                        or old.get('revision') != doc['revision']):
                    raise StateError('stale_trade_document')
            elif doc['revision'] != 0 or doc.get('_original_doc_hash') is not None:
                raise StateError('missing_trade_document')
            if not current:
                if (doc['execution_state']!='CANDIDATE' or doc['execution_side'] is not None or
                        doc['execution_event_id'] is not None or doc['wallet'] is not None or
                        any(raw(doc[k],signed=k=='wallet_delta_lamports') for k in
                            ('buy_raw','sold_raw','remaining_raw','wallet_delta_lamports','fees_lamports'))):
                    raise StateError('new_trade_requires_empty_candidate')
            entering_reconciliation=current and old['execution_state']!='RECONCILING' and doc['execution_state']=='RECONCILING'
            if entering_reconciliation:
                generation=Ledger.meta(c,'reconciliation_generation') or 0
                if type(generation) is not int or generation<0:raise StateError('corrupt_reconciliation_generation')
                doc['reconciliation_after_generation']=generation+1
                doc['position_reconciliation_revision']=None
            stored=machine.storage(doc,StateError)
            stored['revision']=doc['revision']+1
            text=canonical(stored)
            if current:
                updated=c.execute('UPDATE trades SET doc=? WHERE id=? AND doc=?',(text,key,current[0]))
                if updated.rowcount!=1:raise StateError('stale_trade_document')
            else:c.execute('INSERT INTO trades VALUES(?,?)',(key,text))
            if entering_reconciliation:Ledger.set_meta(c,'reconciliation_required',True)
            doc['revision']=stored['revision']
            doc['_original_doc_hash']=machine.digest(text)
            if current and old['execution_state']!=doc['execution_state']:
                c.execute('INSERT INTO audit(at,trade_id,action,doc) VALUES(?,?,?,?)',
                          (doc['updated_at'],key,'execution_transition',canonical({'from':old['execution_state'],
                           'to':doc['execution_state'],'reason':doc.get('_transition_reason','central_trade_write'),
                           'event_id':doc['execution_event_id']})))
            doc.pop('_transition_reason',None)
            return
        c.execute(f"INSERT INTO {table} VALUES(?,?) ON CONFLICT({column}) DO UPDATE SET doc=excluded.doc",
                  (key, canonical(doc)))

    @staticmethod
    def all(c, table):
        if table not in {"trades", "events", "receipts"}:
            raise StateError("unknown_table")
        return [Ledger.validate(table,machine.read(r[0],StateError) if table=='trades' else json.loads(r[0]))
                for r in c.execute(f"SELECT doc FROM {table} ORDER BY rowid")]

    def audit(self, c, action, doc, trade_id=None):
        c.execute("INSERT INTO audit(at,trade_id,action,doc) VALUES(?,?,?,?)",
                  (self.clock(), trade_id, action, canonical(doc)))

    def transition(self, c, trade, state, reason):
        persisted = self.get(c, "trades", trade["trade_id"])
        if (not persisted or persisted["state"] != trade["state"]
                or persisted['_original_doc_hash']!=trade.get('_original_doc_hash')):
            raise StateError("transition_source_mismatch")
        origin = persisted["state"]
        if state not in TRANSITIONS or (state != origin and state not in TRANSITIONS[origin]):
            raise StateError("illegal_state_transition:" + origin + ":" + str(state))
        quantity = raw(trade["remaining_raw"])
        if state in {"POSITION_OPEN", "EMERGENCY_EXIT", "SELL_SENT", "SELL_ACK"} and quantity <= 0:
            raise StateError("transition_requires_confirmed_position")
        if state in {"CLOSED", "SELL_FILLED"} and (quantity or not raw(trade["buy_raw"])):
            raise StateError("transition_requires_closed_quantity")
        self.audit(c, "transition", {"from": trade["state"], "to": state, "reason": reason}, trade["trade_id"])
        target={'APPROVED':'READY','BUY_SENT':'BUY_REQUESTED','BUY_ACK':'BUY_ACKNOWLEDGED',
                'SELL_SENT':'SELL_REQUESTED','SELL_ACK':'SELL_ACKNOWLEDGED',
                'EMERGENCY_EXIT':'POSITION_OPEN'}.get(state,state)
        if state in UNCERTAIN:
            event=self.get(c,'events',trade['execution_event_id']) if trade['execution_event_id'] else None
            if event and event['state'] in {'SENT','ACK','PARTIAL','UNKNOWN'}:
                target=event['side']+'_UNKNOWN'
                unknown='TIMEOUT' if state=='TIMEOUT' else ('RECOVERY' if reason=='restart_with_execution_obligation' else reason)
            else:target='RECONCILING';unknown=''
        else:unknown=''
        if state=='EMERGENCY_EXIT':
            if trade['execution_state']!='POSITION_OPEN':raise StateError('exit_requires_reconciled_position')
            trade['exit_requested']=True
        self.machine_transition(c,trade,target,reason,unknown_reason=unknown)

    def machine_transition(self,c,trade,target,reason,*,unknown_reason='',terminal_unreconciled=False):
        origin=machine.move(trade,target,error=StateError,reason=unknown_reason,
                            terminal_unreconciled=terminal_unreconciled)
        if origin==target:
            self.audit(c,'execution_transition',{'from':origin,'to':target,'reason':reason,
                       'event_id':trade['execution_event_id']},trade['trade_id'])
        trade['_transition_reason']=reason
        trade["updated_at"] = self.clock()
        self.put(c, "trades", trade["trade_id"], trade)

    def halt(self, c, reason, *, abort_validation=True):
        if self.meta(c, 'validation_contract') is not None and abort_validation:
            self.set_meta(c, 'validation_abort', self.meta(c, 'validation_abort') or reason)
            self.set_meta(c, 'new_buys_enabled', False)
        reasons = self.meta(c, "kill_reasons") or []
        previous = self.meta(c, "kill_reason")
        if previous and previous not in reasons:
            reasons.append(previous)
        if reason not in reasons:
            reasons.append(reason)
        self.set_meta(c, "kill_reasons", reasons)
        self.set_meta(c, "kill_reason", reasons[0])
        # Financial limits block entry and capture exits without making an
        # otherwise proven inventory unknown. Never clear an existing fault.
        if reason not in {'session_loss_limit', 'max_drawdown_15pct'}:
            self.set_meta(c, "reconciliation_required", True)
        self.audit(c, "kill_switch", {"reason": reason})
        from execution_positions import capture_risk_exits
        capture_risk_exits(self, c, reason)

    def block(self, reason):
        with self.transaction() as c:
            self.halt(c, reason)

    def bootstrap(self, baseline, legacy_path=None):
        with self.transaction() as c:
            if self.meta(c, "baseline_decision_at") is not None:
                return
            age(baseline, self.clock())
            if legacy_path and Path(legacy_path).exists():
                try:
                    legacy = json.loads(Path(legacy_path).read_text(encoding="utf-8-sig"))
                    if not isinstance(legacy, dict) or legacy.get("pending") or legacy.get("buy_filled"):
                        raise StateError("legacy_execution_requires_manual_mapping")
                except (ValueError, TypeError, OSError):
                    self.halt(c, "legacy_state_requires_reconciliation")
            self.set_meta(c, "baseline_decision_at", baseline)
            self.audit(c, "bootstrap", {"baseline_decision_at": baseline})

    def recover(self):
        with self.transaction() as c:
            self.set_meta(c, "reconciliation_required", True)
            for ev in self.all(c, "events"):
                if ev["state"] in {"SENT", "ACK", "PARTIAL", "UNKNOWN"}:
                    from execution_positions import capture_recovery
                    capture_recovery(self, c, ev)
                    trade = self.get(c, "trades", ev["trade_id"])
                    self.transition(c, trade, "RECONCILIATION_REQUIRED", "restart_with_execution_obligation")
                    if raw(trade['remaining_raw']):
                        self.request_exit(c, trade, 'recovery_exit')
                    self.halt(c, "restart_with_execution_obligation")
            self.audit(c, "restart", {})

    def expire(self, c):
        for ev in self.all(c, "events"):
            if ev["state"] in {"SENT", "ACK", "PARTIAL"} and age(ev["sent_at"], self.clock()) > self.cfg["request_timeout_seconds"]:
                ev["state"] = "UNKNOWN"
                self.put(c, "events", ev["event_id"], ev)
                trade = self.get(c, "trades", ev["trade_id"])
                self.transition(c, trade, "TIMEOUT", "missing_terminal_execution_confirmation")
                self.halt(c, "execution_timeout")

    def candidate(self, signal):
        for key in ("source", "variant", "mint", "signal_at"):
            if not isinstance(signal.get(key), str) or not signal[key]:
                raise StateError("missing_candidate_identity")
        age(signal["signal_at"], self.clock())
        tid = identity(signal["source"], signal["variant"], signal["mint"], signal["signal_at"])
        with self.transaction() as c:
            existing = self.get(c, "trades", tid)
            if existing:
                if existing["signal"] != signal:
                    self.halt(c, "candidate_identity_conflict")
                    return None
                return tid
            trade = {"trade_id": tid, "mint": signal["mint"], "signal": signal, "state": "CANDIDATE",
                     "session": self.meta(c, "session"), "remaining_raw": "0", "buy_raw": "0",
                     "sold_raw": "0", "wallet_delta_lamports": "0", "fees_lamports": "0",
                     "created_at": self.clock(), "updated_at": self.clock(), "wallet": None,
                     "shadow": None, "comparison": None, "real_net_pnl_lamports": None,
                     "position_reconciliation_revision":None}
            machine.seed(trade)
            self.put(c, "trades", tid, trade)
            self.audit(c, "candidate", signal, tid)
        return tid

    def buy_allowed(self, c):
        from execution_validation import buy_guard
        try:
            buy_guard(self, c)
        except StateError:
            return False
        if self.valuation_fault()[0]:
            return False
        if self.exit_pending(c):
            return False
        if self.meta(c, "kill_reason") or not self.meta(c, "new_buys_enabled"):
            return False
        if self.meta(c, "reconciliation_required") and not self.signals_only_safe(c):
            return False
        reconciled = self.meta(c, "reconciled_at")
        if (not reconciled or age(reconciled, self.clock()) > self.cfg["reconciliation_max_age_seconds"]) and not self.signals_only_safe(c):
            self.set_meta(c, "reconciliation_required", True)
            return False
        trades = self.all(c, "trades")
        active = [t for t in trades if t["state"] not in TERMINAL | {"CANDIDATE"}]
        if any(t["state"] in UNCERTAIN for t in trades):
            return False
        return len(active) < self.cfg["max_open_positions"] and sum(
            t["session"] == self.meta(c, "session") and raw(t["buy_raw"]) > 0 for t in trades
        ) < self.cfg["max_session_trades"]

    def signals_only_safe(self, c):
        # Preserve passive SAFE100/outbox operation without pretending to know a
        # real wallet. Such intents cannot pass claim or the disabled HTTP gate.
        return (self.cfg["execution_authority_pct"] == 0
                and not self.meta(c, "kill_reason")
                and all(e["state"] in {"READY", "REJECTED"} for e in self.all(c, "events"))
                and all(raw(t["buy_raw"]) == 0 and t["state"] not in UNCERTAIN for t in self.all(c, "trades")))

    def prepare_buy(self, tid):
        with self.transaction() as c:
            migration = self.meta(c, 'validation_wallet_migration')
            if migration and tid in migration['retired_trade_ids']:
                return None
            self.expire(c)
            trade = self.get(c, "trades", tid)
            if not trade or trade["state"] != "CANDIDATE" or not self.buy_allowed(c):
                return None
            if trade["session"] != self.meta(c, "session"):
                self.transition(c, trade, "REJECTED", "candidate_from_previous_session")
                return None
            if trade['execution_state']=='CANDIDATE':
                self.machine_transition(c,trade,'RISK_CHECK','candidate_risk_checks')
            sig = trade["signal"]
            from execution_validation import require as validation_contract
            validation = validation_contract(self,c)
            from mykonos_meme_scout.jupiter_prepare import USDC
            technical = (validation is not None and sig.get('source')=='THREE_TRADE_VALIDATION_FIXED_PAIR'
                         and sig.get('validation_contract_identity')==validation['identity'] and sig.get('mint')==USDC)
            if sig["variant"] != self.cfg["variant"] or not (sig.get("safe100_approved") or technical):
                self.transition(c, trade, "REJECTED", "safe100_not_approved")
                return None
            if age(sig["signal_at"], self.clock()) > self.cfg["signal_max_age_seconds"]:
                self.transition(c, trade, "REJECTED", "stale_signal")
                return None
            if decimal(sig.get("reference_liquidity_usd")) < decimal(self.cfg["min_liquidity_usd"]):
                self.transition(c, trade, "REJECTED", "insufficient_liquidity")
                return None
            self.transition(c, trade, "APPROVED", "safe100_and_session_guards")
            return self.new_event(c, trade, "BUY")

    def new_event(self, c, trade, side, *, reason=None):
        anchor = None
        if side == 'SELL':
            from execution_positions import require_risk_trigger
            if require_risk_trigger(self, c, trade):
                self.request_exit(c, trade, 'risk_exit')
            if not trade['exit_requested']:
                raise StateError('sell_requires_persistent_exit_intent')
            reason = self.exit_reason(c, trade)
            anchor = self.sell_anchor(c, trade)
            if anchor is None:
                return None
        eid = side + ":" + trade["trade_id"]
        attempts = [e for e in self.all(c, "events")
                    if e["trade_id"] == trade["trade_id"] and e["side"] == side]
        existing = attempts[-1] if attempts else None
        if existing:
            # Only a rejection BEFORE claim proves no order was submitted.
            # SENT/UNKNOWN/PARTIAL can never generate a second attempt.
            if side != "SELL" or existing["state"] != "REJECTED" or existing.get("sent_at") or existing.get("order_id"):
                if side == 'SELL':
                    if existing['state'] != 'READY' or not self.sell_event_matches(existing, trade):
                        return None
                    self.guard_sell_commit(c, trade['trade_id'], existing['event_id'], anchor)
                return existing
            eid += ":attempt:" + str(len(attempts) + 1)
        ev = {"event_id": eid, "trade_id": trade["trade_id"], "mint": trade["mint"], "side": side,
              "state": "READY", "created_at": self.clock(), "signal_at": trade["signal"]["signal_at"],
              "reference_price": trade["signal"].get("reference_price"),
              "reference_liquidity_usd": trade["signal"].get("reference_liquidity_usd"),
              "position_fraction": self.cfg["position_fraction"], "actual_input_raw": None,
              "order_id": None, "wallet": trade["wallet"], "exit_reason": reason,
              "filled_input_raw": "0", "filled_output_raw": "0",
              "execution": False, "risk_status": "PENDING_EXECUTION_CHECKS",
              "request_id": identity("execution-request", eid),
              "amount_raw": trade["remaining_raw"] if side == "SELL" else None}
        self.put(c, "events", eid, ev)
        if side=='BUY':
            trade.update(execution_event_id=eid,execution_side=side)
            self.put(c,'trades',trade['trade_id'],trade)
        self.audit(c, "event_ready", ev, trade["trade_id"])
        if side == 'SELL':
            self.guard_sell_commit(c, trade['trade_id'], eid, anchor)
        return ev

    def exit_reason(self, c, trade):
        intent = self.meta(c, 'position_exit:' + trade['trade_id'])
        if intent is None:
            # Schema-2's historical bool-only flag specifically meant emergency.
            return 'emergency_exit' if trade['exit_requested'] else None
        if (not isinstance(intent, dict) or set(intent) != {'trade_id','wallet','mint','reason','requested_at'}
                or any(intent[k] != trade[k] for k in ('trade_id','wallet','mint'))
                or intent['reason'] not in EXIT_REASONS or not trade['exit_requested']
                or not isinstance(intent['requested_at'], str) or not 1 <= len(intent['requested_at']) <= 64):
            raise StateError('corrupt_position_exit_intent')
        age(intent['requested_at'], self.clock())
        return intent['reason']

    def request_exit(self, c, trade, reason):
        if reason not in EXIT_REASONS:
            raise StateError('invalid_exit_reason')
        if raw(trade['remaining_raw']) <= 0 or not trade['wallet']:
            raise StateError('exit_requires_confirmed_inventory')
        old = self.meta(c, 'position_exit:' + trade['trade_id'])
        previous = self.exit_reason(c, trade)
        chosen = min((reason, previous), key=EXIT_REASONS.index) if previous is not None else reason
        if old is not None and chosen == previous:
            return
        intent = {k: trade[k] for k in ('trade_id','wallet','mint')}
        intent.update(reason=chosen, requested_at=old['requested_at'] if old is not None else self.clock())
        trade['exit_requested'] = True
        trade['state'] = machine.projection(trade)
        self.put(c, 'trades', trade['trade_id'], trade)
        self.set_meta(c, 'position_exit:' + trade['trade_id'], intent)
        self.audit(c, 'exit_requested', intent, trade['trade_id'])

    @staticmethod
    def sell_event_matches(event, trade):
        return (event['side'] == 'SELL' and event['trade_id'] == trade['trade_id']
                and event['mint'] == trade['mint'] and event['wallet'] == trade['wallet']
                and event['amount_raw'] == trade['remaining_raw'])

    def sell_anchor(self, c, trade, *, claimed_event=None):
        """Current coherent audit inventory, never a historical position alone."""
        # Capture time before metadata/audit I/O. Query latency and a rollback
        # above observed_at must not enlarge the original validity interval.
        monotonic_start, checked_at = time.monotonic(), self.clock()
        limit = self.cfg['reconciliation_max_age_seconds']
        expected_phase = 'SELL_REQUESTED' if claimed_event is not None else 'POSITION_OPEN'
        if (trade['execution_state'] != expected_phase or raw(trade['remaining_raw']) <= 0
                or self.meta(c, 'reconciliation_required') or not trade['wallet']
                or trade['wallet'] != self.meta(c, 'wallet')):
            return None
        if claimed_event is not None and (trade['execution_event_id'] != claimed_event
                                         or trade['execution_side'] != 'SELL'):
            return None
        observed = self.meta(c, 'reconciled_at')
        if not observed or age(observed, checked_at) > limit:
            return None
        monotonic_deadline = monotonic_start + limit-age(observed, checked_at)
        events = self.all(c, 'events')
        if any(e['state'] in {'SENT','ACK','PARTIAL','UNKNOWN'} and e['event_id'] != claimed_event for e in events):
            return None
        revision = self.meta(c, 'reconciliation_revision')
        generation = self.meta(c, 'reconciliation_generation')
        records = c.execute("SELECT at,doc FROM audit WHERE action='reconciliation' AND json_extract(doc,'$.revision')=?",
                            (revision,)).fetchall()
        if len(records) != 1:
            raise StateError('sell_reconciliation_anchor_invalid')
        audit_at, body = records[0]
        record = json.loads(body)
        observation = record.get('observation', {})
        trades = self.all(c, 'trades')
        active = {t['trade_id']: t['remaining_raw'] for t in trades if raw(t['remaining_raw'])}
        positions = record.get('trade_positions')
        if (not isinstance(positions, dict) or not isinstance(observation, dict)
                or record.get('generation') != generation or type(generation) is not int
                or observation.get('observed_at') != observed or observation.get('wallet') != trade['wallet']):
            raise StateError('sell_reconciliation_anchor_invalid')
        age(observed, audit_at)
        age(audit_at, self.clock())
        if {k: v for k, v in positions.items() if raw(v)} != active:
            raise StateError('sell_reconciliation_inventory_invalid')
        from execution_validation import foreign_inventory
        inventory = foreign_inventory(self, c)
        for position in trades:
            if raw(position['remaining_raw']):
                inventory[position['mint']] = str(raw(inventory.get(position['mint'], '0'))+raw(position['remaining_raw']))
        if observation.get('chain_positions') != inventory or observation.get('executor_positions') != inventory:
            raise StateError('sell_reconciliation_inventory_invalid')
        orders = observation.get('orders')
        by_event = {e['event_id']: e for e in events}
        if not isinstance(orders, dict) or any(not isinstance(o, dict) or eid not in by_event
                or o.get('state') != 'FILLED' or by_event[eid]['state'] != 'FILLED'
                or o.get('order_id') != by_event[eid]['order_id'] for eid, o in orders.items()):
            return None
        accounted = sum(raw(t['wallet_delta_lamports'], signed=True) for t in trades)
        if revision != identity('reconciliation', generation, observation, accounted):
            raise StateError('sell_reconciliation_anchor_invalid')
        self.require_reconciliation_record(c, trade)
        anchor = SellAnchor((revision, generation, observed, audit_at, trade['wallet'], trade['remaining_raw']),
                            checked_at, limit, monotonic_deadline)
        self.sell_time_fresh(anchor)
        return anchor

    def sell_time_fresh(self, anchor):
        current = self.clock()
        age(anchor.checked_at, current)
        if (self.cfg['reconciliation_max_age_seconds'] != anchor.limit
                or age(anchor.evidence[2], current) > anchor.limit
                or time.monotonic() >= anchor.monotonic_deadline):
            raise StateError('sell_reconciliation_expired')
        return current

    def guard_sell_commit(self, c, tid, eid, anchor, *, claimed=False):
        if not isinstance(c, LedgerConnection):
            raise StateError('sell_transaction_not_owned')
        self.sell_time_fresh(anchor)
        last_wall = anchor.checked_at
        def guard():
            nonlocal last_wall
            current = self.sell_time_fresh(anchor)
            age(last_wall, current)
            last_wall = current
            trade, event = self.get(c, 'trades', tid), self.get(c, 'events', eid)
            observed = self.sell_anchor(c, trade, claimed_event=eid if claimed else None) if trade else None
            if (not trade or not event or event['state'] != ('SENT' if claimed else 'READY')
                    or not self.sell_event_matches(event, trade)
                    or observed is None or observed.evidence != anchor.evidence):
                raise StateError('sell_reconciliation_changed')
            current = self.sell_time_fresh(anchor)
            age(last_wall, current)
            last_wall = current
        c.commit_guards.append(guard)

    def exit_pending(self, c):
        from execution_positions import buy_exit_pending
        return buy_exit_pending(self, c)

    def materialize_exit(self, tid, *, deadline=None):
        try:
            with self.transaction(deadline=deadline) as c:
                trade = self.get(c, 'trades', tid)
                if trade and raw(trade['remaining_raw']):
                    from execution_positions import require_risk_trigger
                    if require_risk_trigger(self, c, trade):
                        self.request_exit(c, trade, 'risk_exit')
                return self.new_event(c, trade, 'SELL') if trade and trade['exit_requested'] else None
        except StateError as error:
            if str(error) not in {'sell_reconciliation_expired','sell_reconciliation_changed'}:
                raise
            return None  # The separately committed intent remains durable.

    def prepare_sell(self, tid, *, reason="strategy_exit", generation=None):
        with self.transaction() as c:
            trade = self.get(c, "trades", tid)
            if not trade or raw(trade["remaining_raw"]) <= 0:
                return None
            if generation is not None and generation != trade["signal"]["signal_at"]:
                self.halt(c, "exit_candidate_generation_mismatch")
                return None
            self.request_exit(c, trade, reason)
        return self.materialize_exit(tid)

    def pending(self):
        with self.transaction() as c:
            self.expire(c)
            events = self.all(c, "events")
            for ev in sorted(events, key=lambda e: e["side"] != "SELL"):
                if ev["state"] != "READY":
                    continue
                if ev["side"] == "BUY":
                    if self.valuation_fault()[0]:
                        continue
                    if self.exit_pending(c):
                        continue
                    if self.meta(c, "kill_reason") or not self.meta(c, "new_buys_enabled") or (self.meta(c, "reconciliation_required") and not self.signals_only_safe(c)):
                        continue
                    if age(ev["signal_at"], self.clock()) > self.cfg["signal_max_age_seconds"]:
                        ev["state"] = "REJECTED"
                        self.put(c, "events", ev["event_id"], ev)
                        self.transition(c, self.get(c, "trades", ev["trade_id"]), "REJECTED", "stale_queued_signal")
                        continue
                else:
                    trade = self.get(c, "trades", ev["trade_id"])
                    anchor = self.sell_anchor(c, trade)
                    if anchor is None or not self.sell_event_matches(ev, trade):
                        return None
                    self.guard_sell_commit(c, trade['trade_id'], ev['event_id'], anchor)
                return ev
        return None

    def claim(self, eid, quote, *, payload=None, provider='GMGN'):
        """Reserve exactly one send. Caller must never retry the external swap.

        Internal contract for the future authorized adapter and isolated fakes.
        HTTP live claims remain disabled by configuration.
        """
        with self.transaction() as c:
            self.expire(c)
            self.validate_links(c)
            ev = self.get(c, "events", eid)
            if not ev or ev["state"] != "READY":
                raise StateError("already_claimed_or_missing")
            trade = self.get(c, "trades", ev["trade_id"])
            if provider == 'JUPITER_V2':
                from execution_validation import require, buy_guard
                contract = require(self, c)
                if (not contract or quote.get('source') != 'JUPITER_V2_ORDER_QUOTE_OBSERVATION'
                        or quote.get('route_verified') is not True):
                    raise StateError('jupiter_claim_route_or_mode_unknown')
                if ev['side'] == 'BUY':
                    buy_guard(self, c, ev['trade_id'])
                    from execution_validation import foreign_inventory
                    if ev['mint'] in foreign_inventory(self, c):
                        raise StateError('validation_cannot_trade_foreign_inventory')
            elif provider != 'GMGN':
                raise StateError('execution_provider_unknown')
            elif self.meta(c,'validation_contract') is not None:
                raise StateError('validation_requires_jupiter_provider')
            if trade["state"] in UNCERTAIN:
                raise StateError("reconciliation_required")
            if ev["side"] == "BUY":
                if self.valuation_fault()[0]:
                    raise StateError('equity_valuation_unknown')
                if self.exit_pending(c):
                    raise StateError('position_exit_pending')
                if trade["session"] != self.meta(c, "session"):
                    raise StateError("candidate_from_previous_session")
                if self.meta(c, "kill_reason") or not self.meta(c, "new_buys_enabled") or self.meta(c, "reconciliation_required"):
                    raise StateError("buys_blocked")
                if age(ev["signal_at"], self.clock()) > self.cfg["signal_max_age_seconds"]:
                    raise StateError("stale_signal")
                if not self.meta(c, "reconciled_at") or age(self.meta(c, "reconciled_at"), self.clock()) > self.cfg["reconciliation_max_age_seconds"]:
                    raise StateError("stale_reconciliation")
                trades = self.all(c, "trades")
                events = self.all(c, "events")
                obligations = [t for t in trades if t["trade_id"] != trade["trade_id"]
                               and t["state"] not in TERMINAL | {"CANDIDATE"}]
                if len(obligations) >= self.cfg["max_open_positions"]:
                    raise StateError("max_open_positions")
                sent_buys = {e["trade_id"] for e in events if e["side"] == "BUY"
                             and e.get("sent_at") and self.get(c, "trades", e["trade_id"])["session"] == self.meta(c, "session")}
                if len(sent_buys) >= self.cfg["max_session_trades"]:
                    raise StateError("max_session_trades")
                from execution_positions import bind_trade
                bind_trade(self, c, trade)
            else:
                sell_anchor = self.sell_anchor(c, trade)
                if sell_anchor is None or not self.sell_event_matches(ev, trade):
                    raise StateError("sell_without_current_reconciliation")
            if age(quote.get("quoted_at"), self.clock()) > self.cfg["quote_max_age_seconds"]:
                raise StateError("stale_quote")
            expected_input = "So11111111111111111111111111111111111111112" if ev["side"] == "BUY" else ev["mint"]
            expected_output = ev["mint"] if ev["side"] == "BUY" else "So11111111111111111111111111111111111111112"
            if quote.get("event_id") != eid or quote.get("input_token") != expected_input or quote.get("output_token") != expected_output:
                raise StateError("quote_identity_mismatch")
            for key, limit in (("slippage_bps", "max_slippage_bps"), ("price_impact_bps", "max_price_impact_bps"),
                               ("price_deviation_bps", "max_price_deviation_bps")):
                if decimal(quote.get(key)) > self.cfg[limit]:
                    raise StateError(key + "_exceeded")
            if decimal(quote.get("liquidity_usd")) < decimal(self.cfg["min_liquidity_usd"]):
                raise StateError("insufficient_liquidity")
            wallet = quote.get("wallet")
            if not isinstance(wallet, str) or not wallet or wallet != self.meta(c, "wallet"):
                raise StateError("wallet_not_reconciled")
            amount = raw(quote.get("input_raw"))
            if amount <= 0 or raw(quote.get("minimum_output_raw")) <= 0:
                raise StateError("zero_order_amount")
            fee_budget = raw(self.cfg['max_order_fee_lamports'])
            balance = raw(self.meta(c, "wallet_balance_lamports"))
            reserved = sum((raw(e['actual_input_raw']) if e['side'] == 'BUY' else 0)
                           + raw(e.get('fee_budget_lamports', self.cfg['max_order_fee_lamports']))
                           for e in self.all(c, 'events') if e['state'] in {'SENT', 'ACK', 'PARTIAL', 'UNKNOWN'})
            if ev["side"] == "SELL":
                if wallet != trade["wallet"] or amount != raw(trade["remaining_raw"]):
                    raise StateError("sell_amount_mismatch")
                if reserved + fee_budget + raw(self.cfg['reserve_lamports']) > balance:
                    raise StateError('sell_fee_reserve')
            else:
                max_amount = int(Decimal(balance) * decimal(self.cfg["position_fraction"]))
                if amount > max_amount or amount + reserved + fee_budget + raw(self.cfg["reserve_lamports"]) > balance:
                    raise StateError("capital_limit")
                trade["wallet"] = wallet
            ev.update(state="SENT", wallet=wallet, sent_at=self.clock(), quote=quote, actual_input_raw=str(amount),
                      request_id=ev.get("request_id") or identity("execution-request", eid), fee_budget_lamports=str(fee_budget))
            if provider == 'JUPITER_V2':
                intent = {'provider': provider, 'contract_version': 2, 'wallet': wallet,
                          'input_token': expected_input, 'output_token': expected_output,
                          'input_raw': str(amount), 'minimum_output_raw': quote['minimum_output_raw']}
                encoded = canonical(intent)
                ev.update(provider=provider, adapter_contract_version=2, send_started_at=None,
                          evidence_domain=contract['domain'], claim_id=identity('execution-claim', eid),
                          request_payload_json=encoded, payload_hash=hashlib.sha256(encoded.encode()).hexdigest())
            if payload is not None:
                expected = {'chain': 'sol', 'from_address': wallet, 'input_token': expected_input,
                            'output_token': expected_output, 'input_amount': str(amount),
                            'min_output_amount': str(raw(quote['minimum_output_raw'])), 'auto_slippage': False}
                allowed = set(expected) | {'slippage', 'priority_fee', 'tip_fee', 'is_anti_mev'}
                if (not isinstance(payload, dict) or set(payload) != allowed or any(payload.get(k) != v for k, v in expected.items())
                        or payload['auto_slippage'] is not False or payload['is_anti_mev'] is not True
                        or decimal(payload.get('slippage')) * 100 != decimal(quote['slippage_bps'])
                        or quote.get('source') != 'GMGN_ORDER_QUOTE'):
                    raise StateError('request_payload_quote_mismatch')
                fees = (decimal(payload['priority_fee']) + decimal(payload['tip_fee'])) * 1_000_000_000
                if fees != fees.to_integral_value() or fees > fee_budget:
                    raise StateError('request_payload_fee_budget_exceeded')
                encoded = canonical(payload)
                ev.update(request_payload_json=encoded, payload_hash=hashlib.sha256(encoded.encode()).hexdigest(),
                          adapter_contract_version=1, send_started_at=None)
            self.put(c, "events", eid, ev)
            trade.update(execution_event_id=eid,execution_side=ev['side'])
            self.transition(c, trade, ev["side"] + "_SENT", "executor_claim_before_external_io")
            if ev['side'] == 'SELL':
                self.guard_sell_commit(c, trade['trade_id'], eid, sell_anchor, claimed=True)
            return ev

    def guard_revision(self, c):
        """Guard state and obligations, excluding audit-only activity."""
        meta = {row[0]: json.loads(row[1]) for row in c.execute('SELECT key,value FROM meta ORDER BY key')}
        return identity('guard-revision', canonical({'config': dict(self.cfg), 'meta': meta, 'trades': self.all(c, 'trades'),
                                                     'events': self.all(c, 'events'),
                                                     'valuation_fault': self.valuation_fault()}))

    def valuation_fault(self):
        with self._valuation_lock:
            return self._valuation_fault, self._valuation_generation

    def invalidate_valuation(self):
        # Same singleton as Risk/Adapter/Monitor. This only blocks execution;
        # durable truth remains SQLite. Startup always requires reconciliation.
        with self._valuation_lock:
            self._valuation_generation += 1
            self._valuation_fault = True

    def clear_valuation_after_commit(self, generation):
        with self._valuation_lock:
            if self._valuation_generation == generation:
                self._valuation_fault = False

    def authorize_send(self, eid, request_id, payload_hash, guard_revision, *,
                       valid_from, valid_until, monotonic_deadline, gate_path=None):
        """Internal compare-and-set; never reconstructs permission on restart."""
        from execution_policy import owner_gate, OWNER_GATE
        active_connection = None
        def fresh():
            now = self.clock()
            age(valid_from, now)
            age(now, valid_until)
            if type(monotonic_deadline) not in (int, float) or not math.isfinite(monotonic_deadline) or time.monotonic() >= monotonic_deadline:
                raise StateError('dispatch_evidence_expired')
            gate = owner_gate(OWNER_GATE if gate_path is None else gate_path)
            if not self.owner_send_allowed(eid, gate, connection=active_connection, deadline=monotonic_deadline):
                raise StateError('owner_gate_off_before_send')
        fresh()
        with self.transaction(deadline=monotonic_deadline, before_commit=fresh) as c:
            active_connection = c
            fresh()
            self.expire(c)
            ev = self.get(c, 'events', eid)
            if (not ev or ev['state'] != 'SENT' or ev.get('adapter_contract_version') not in (1, 2)
                    or ev.get('send_started_at') is not None or ev.get('request_id') != request_id
                    or ev.get('payload_hash') != payload_hash or self.guard_revision(c) != guard_revision
                    or hashlib.sha256(ev.get('request_payload_json', '').encode()).hexdigest() != payload_hash):
                raise StateError('send_claim_changed_or_consumed')
            fresh()
            if ev.get('provider') == 'JUPITER_V2':
                from execution_jupiter import require_pre_send
                require_pre_send(self, c, ev)
            ev['send_started_at'] = self.clock()
            ev['dispatch_valid_until'] = valid_until
            if ev['side'] == 'BUY' and ev.get('evidence_domain') != 'ISOLATED':
                ev['owner_entry_authorization'] = self.entry_authorization(ev)
            if ev.get('provider') == 'JUPITER_V2' and ev.get('evidence_domain') == 'FINALIZED_CHAIN':
                from execution_validation import require as validation_contract
                contract=validation_contract(self,c)
                ev['validation_owner_authorization']={'owner_gate':'ON','wallet':ev['wallet'],
                    'contract_identity':contract['identity'],'claim_id':ev['claim_id'],'request_id':ev['request_id'],
                    'message_sha256':ev['jupiter_prepared']['message_sha256'],'side':ev['side']}
            self.put(c, 'events', eid, ev)
            self.audit(c, 'single_send_started', {'event_id': eid, 'request_id': request_id,
                                                'payload_hash': payload_hash}, ev['trade_id'])
            result = ev, self.guard_revision(c)
        active_connection = None
        fresh()
        return result

    @staticmethod
    def entry_authorization(event):
        return {'owner_gate': 'ON', **{key: event[key] for key in
            ('event_id', 'trade_id', 'request_id', 'payload_hash', 'wallet', 'mint')}}

    def owner_exit_allowed(self, c, eid):
        """Only a reconciled remainder of our terminal owner-authorized entry."""
        event = self.get(c, 'events', eid)
        if event and event.get('evidence_domain') == 'ISOLATED':
            return False
        if not event or event['side'] != 'SELL' or event['state'] not in {'READY', 'SENT'}:
            return False
        trade = self.get(c, 'trades', event['trade_id'])
        if not trade or not self.sell_event_matches(event, trade):
            return False
        buys = [e for e in self.all(c, 'events') if e['trade_id'] == trade['trade_id'] and e['side'] == 'BUY']
        if len(buys) != 1:
            return False
        buy = buys[0]
        if (buy['state'] != 'FILLED' or not buy.get('send_started_at') or not buy.get('order_id')
                or buy.get('owner_entry_authorization') != self.entry_authorization(buy)
                or buy['wallet'] != trade['wallet'] or buy['mint'] != trade['mint']
                or hashlib.sha256(buy.get('request_payload_json', '').encode()).hexdigest() != buy['payload_hash']
                or not any(r['event_id'] == buy['event_id'] for r in self.all(c, 'receipts'))):
            return False
        return self.sell_anchor(c, trade, claimed_event=eid if event['state'] == 'SENT' else None) is not None

    def owner_send_allowed(self, eid, gate, *, connection=None, deadline=None):
        if gate.get('valid') is not True:
            return False
        # Preserve the legacy ON fast path without adding a new write/CAS
        # boundary. Jupiter's persistent mode always takes the stricter path.
        if gate.get('value') == 'ON':
            if connection is not None:
                validation = self.meta(connection, 'validation_contract')
            else:
                with sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro', uri=True,
                                     timeout=self.cfg['ledger_lock_timeout_seconds']) as read:
                    validation = self.meta(read, 'validation_contract')
            if validation is None:
                return True
        def current():
            if deadline is not None and time.monotonic() >= deadline:
                raise StateError('dispatch_evidence_expired')
        context = self.transaction(deadline=deadline, before_commit=current) if connection is None else nullcontext(connection)
        with context as c:
            event = self.get(c, 'events', eid)
            if event and event.get('provider') == 'JUPITER_V2':
                from execution_validation import require
                contract = require(self, c)
                if not contract or contract['domain'] != event.get('evidence_domain'):
                    return False
                if contract['domain'] == 'ISOLATED':
                    # Independent test permit domain; never Owner ON authority.
                    return (gate['value'] == 'OFF' and getattr(self, '_isolated_transport', None) is not None
                            and all(self.cfg[k] is False for k in ('signing', 'broadcast_enabled', 'live_trading_enabled')))
                if (self.meta(c,'validation_started') is not True or not all(self.cfg[k] is True for k in
                        ('signing', 'broadcast_enabled', 'live_trading_enabled'))):
                    return False
        if gate.get('value') == 'ON':
            return True
        if gate.get('value') != 'OFF' or not eid:
            return False
        context = self.transaction(deadline=deadline) if connection is None else nullcontext(connection)
        with context as c:
            return self.owner_exit_allowed(c, eid)

    def bind_order(self, eid, order_id):
        """Trusted executor response/recovery adapter only, never HTTP ACK input."""
        if not isinstance(order_id, str) or not order_id or len(order_id) > 256:
            raise StateError("invalid_order_id")
        with self.transaction() as c:
            ev = self.get(c, "events", eid)
            if not ev or ev["state"] not in {"SENT", "ACK", "UNKNOWN"}:
                raise StateError("order_without_claim")
            if ev["order_id"] and ev["order_id"] != order_id:
                self.halt(c, "order_identity_conflict")
                return False
            if any(e["event_id"] != eid and e["order_id"] == order_id for e in self.all(c, "events")):
                self.halt(c, "order_id_reused")
                return False
            ev["order_id"] = order_id
            self.put(c, "events", eid, ev)
            self.audit(c, "trusted_order_binding", {"event_id": eid, "order_id": order_id}, ev["trade_id"])
            return True

    def feedback(self, data, evidence=None):
        """Trusted evidence only; share the atomic reconciliation application."""
        with self.transaction() as c:
            return self._feedback(c, data, evidence)

    def _feedback(self, c, data, evidence=None):
        for key in ("feedback_id", "event_id", "trade_id", "mint", "side", "status"):
            if not isinstance(data.get(key), str) or not data[key] or len(data[key]) > 256:
                raise StateError("invalid_feedback_identity")
        status = data["status"]
        if status not in {"ACK", "ORDER_ACCEPTED", "FILLED", "PARTIAL", "REJECTED", "DEFERRED", "TIMEOUT", "FAILURE", "UNKNOWN_EXECUTION"}:
            raise StateError("unsupported_feedback_status")
        previous = self.get(c, "feedback", data["feedback_id"])
        if previous:
            if previous != data:
                self.halt(c, "feedback_identity_conflict")
                return False
            return True
        ev = self.get(c, "events", data["event_id"])
        if not ev or any(data[k] != ev[k] for k in ("trade_id", "mint", "side")):
            self.halt(c, "feedback_event_mismatch")
            return False
        trade = self.get(c, "trades", ev["trade_id"])
        if ev["state"] == "FILLED" and status in {"DEFERRED", "TIMEOUT", "FAILURE", "REJECTED", "UNKNOWN_EXECUTION"}:
            self.put(c, "feedback", data["feedback_id"], data)
            self.audit(c, "ignored_stale_terminal_feedback", data, trade["trade_id"])
            return True
        if status in {"FILLED", "PARTIAL"}:
            if ev["state"] not in {"SENT", "ACK", "PARTIAL", "UNKNOWN", "FILLED"}:
                raise StateError("fill_without_sent_order")
            if evidence is None:
                self.transition(c, trade, "UNKNOWN_EXECUTION", "unverified_fill")
                self.halt(c, "unverified_fill")
                return False
            order_id = data.get("order_id")
            if not ev["order_id"] or order_id != ev["order_id"]:
                self.halt(c, "fill_order_identity_mismatch")
                return False
            if not self.apply_fill(c, ev, trade, data, evidence):
                return False
        elif status in {"ACK", "ORDER_ACCEPTED"}:
            order_id = data.get("order_id")
            if not isinstance(order_id, str) or not order_id or len(order_id) > 256:
                raise StateError("missing_order_id")
            if not ev["order_id"] or ev["order_id"] != order_id:
                self.halt(c, "order_identity_conflict")
                return False
            if ev["state"] in {"SENT", "ACK"}:
                ev.update(state="ACK")
                if trade["state"] not in UNCERTAIN:
                    self.transition(c, trade, ev["side"] + "_ACK", "executor_ack_not_a_fill")
            elif ev["state"] != "FILLED":
                raise StateError("ack_without_sent_order")
        elif status == "DEFERRED":
            # A deferral is never proof that an already sent order did not execute.
            if ev["state"] != "READY":
                ev["state"] = "UNKNOWN"
                self.transition(c, trade, "UNKNOWN_EXECUTION", "deferred_after_send")
                self.halt(c, "deferred_after_send")
        elif status == "REJECTED" and ev["state"] == "READY":
            ev["state"] = "REJECTED"
            if ev["side"] == "BUY":
                self.transition(c, trade, "REJECTED", "executor_rejected_before_send")
            else:
                self.transition(c, trade, "EMERGENCY_EXIT", "sell_rejected")
                self.halt(c, "sell_rejected")
        else:
            if ev["state"] == "READY":
                raise StateError("unknown_feedback_without_send")
            if ev["state"] == "FILLED":
                self.halt(c, "terminal_execution_conflict")
                return False
            ev["state"] = "UNKNOWN"
            self.transition(c, trade, "TIMEOUT" if status == "TIMEOUT" else "UNKNOWN_EXECUTION", status)
            self.halt(c, "execution_" + status.lower())
        self.put(c, "events", ev["event_id"], ev)
        self.put(c, "feedback", data["feedback_id"], data)
        self.audit(c, "feedback", data, trade["trade_id"])
        return True

    def apply_fill(self, c, ev, trade, data, proof):
        # A Jupiter signature receipt is not a GMGN order report. Until its V2
        # claim/send integration is accepted, no legacy event may consume it.
        if ev.get('provider') == 'JUPITER_V2' or proof.get('provider') == 'JUPITER_V2':
            from execution_jupiter_evidence import binding, SOURCE
            bound = binding(ev)
            if (proof.get('provider') != 'JUPITER_V2' or proof.get('contract_version') != 2
                    or proof.get('source') != SOURCE
                    or any(proof.get(k) != bound[k] for k in
                           ('message_sha256', 'account_keys_sha256', 'signature'))):
                raise StateError('jupiter_receipt_provider_binding_mismatch')
            if proof.get('evidence_domain', 'FINALIZED_CHAIN') != ev.get('evidence_domain', 'FINALIZED_CHAIN'):
                raise StateError('jupiter_receipt_domain_mismatch')
            if ev.get('claim_id') and (proof.get('claim_id') != ev['claim_id'] or proof.get('request_id') != ev['request_id']
                    or proof.get('route_identity') != ev['jupiter_prepared']['route_identity']
                    or proof.get('receipt_identity') != ev['order_id']):
                raise StateError('jupiter_receipt_lifecycle_identity_mismatch')
        for key in ("signature", "wallet", "mint", "side", "slot", "token_delta_raw", "wallet_delta_lamports", "fees_lamports", "block_time",
                    "event_id", "order_id", "swap_input_raw", "swap_output_raw", "order_terminal"):
            if key not in proof:
                raise StateError("incomplete_rpc_evidence")
        if any(proof[k] != ev[k] for k in ("mint", "side", "wallet", "event_id", "order_id")) or proof["signature"] != data.get("signature"):
            self.halt(c, "fill_evidence_identity_mismatch")
            return False
        signature = proof["signature"]
        old = self.get(c, "receipts", signature)
        receipt = {"event_id": ev["event_id"], "proof": {k: v for k, v in proof.items() if k != "order_terminal"}}
        if old:
            if old != receipt:
                self.halt(c, "signature_reuse_conflict")
                return False
            if data["status"] == "FILLED" and proof["order_terminal"] is True and ev["state"] in {"PARTIAL", "UNKNOWN"}:
                return self.complete_fill(c, ev, trade,proof)
            return True
        if ev["state"] == "FILLED":
            self.halt(c, "extra_fill_after_terminal")
            return False
        delta = raw(proof["token_delta_raw"], signed=True)
        cash = raw(proof["wallet_delta_lamports"], signed=True)
        fee = raw(proof["fees_lamports"])
        swap_input = raw(proof["swap_input_raw"])
        swap_output = raw(proof["swap_output_raw"])
        if swap_input <= 0 or swap_output <= 0 or raw(ev["filled_input_raw"]) + swap_input > raw(ev["actual_input_raw"]):
            self.halt(c, "actual_fill_exceeds_order")
            return False
        if (ev["side"] == "BUY" and delta != swap_output) or (ev["side"] == "SELL" and -delta != swap_input):
            self.halt(c, "gmgn_chain_amount_disagreement")
            return False
        if (ev["side"] == "BUY" and (delta <= 0 or cash >= 0)) or (ev["side"] == "SELL" and delta >= 0):
            self.halt(c, "invalid_fill_direction")
            return False
        # Timestamp and signature association protect against an unrelated historical swap.
        sent = datetime.fromisoformat(ev["sent_at"])
        block = datetime.fromisoformat(proof["block_time"].replace("Z", "+00:00"))
        if block.tzinfo is None or (sent - block).total_seconds() > self.cfg["request_timeout_seconds"]:
            self.halt(c, "fill_predates_order")
            return False
        age(proof["block_time"], self.clock())
        ev["filled_input_raw"] = str(raw(ev["filled_input_raw"]) + swap_input)
        ev["filled_output_raw"] = str(raw(ev["filled_output_raw"]) + swap_output)
        if ev["side"] == "BUY":
            trade["buy_raw"] = str(raw(trade["buy_raw"]) + delta)
            trade["remaining_raw"] = str(raw(trade["remaining_raw"]) + delta)
            ev["state"] = "PARTIAL"
            self.machine_transition(c,trade,'BUY_PARTIAL','finalized_chain_receipt_pending_terminal_order')
        else:
            sold = -delta
            if sold > raw(trade["remaining_raw"]) or sold > raw(ev["actual_input_raw"]):
                self.halt(c, "sell_exceeds_position")
                return False
            trade["sold_raw"] = str(raw(trade["sold_raw"]) + sold)
            trade["remaining_raw"] = str(raw(trade["remaining_raw"]) - sold)
            ev["state"] = "PARTIAL"
            self.machine_transition(c,trade,'SELL_PARTIAL','finalized_chain_receipt_pending_terminal_order')
        trade["wallet_delta_lamports"] = str(raw(trade["wallet_delta_lamports"], signed=True) + cash)
        trade["fees_lamports"] = str(raw(trade["fees_lamports"]) + fee)
        self.put(c, "trades", trade["trade_id"], trade)
        self.put(c, "receipts", signature, receipt)
        self.set_meta(c, "reconciliation_required", True)
        if data["status"] == "FILLED" and proof["order_terminal"] is True:
            self.complete_fill(c, ev, trade,proof)
        if ev["state"] == "PARTIAL":
            self.halt(c, "partial_execution")
        self.audit(c, "chain_receipt", receipt, trade["trade_id"])
        from execution_positions import recoveries
        if raw(trade['remaining_raw']) and any(r['trade_id'] == trade['trade_id'] for r in recoveries(self, c)):
            self.request_exit(c, trade, 'recovery_exit')
        return True

    def complete_fill(self, c, ev, trade,proof):
        if raw(ev["filled_input_raw"]) != raw(ev["actual_input_raw"]):
            self.halt(c, "partial_execution")
            return False
        if raw(ev["filled_output_raw"]) < raw(ev["quote"]["minimum_output_raw"]):
            self.halt(c, "fill_below_quote_minimum")
            return False
        if ev["side"] == "SELL" and raw(trade["remaining_raw"]) != 0:
            self.halt(c, "sell_remainder_after_terminal_order")
            return False
        if proof.get('order_terminal') is not True or proof.get('commitment')!='finalized' or not proof.get('source'):
            raise StateError('terminal_observation_unverified')
        proofs=[r['proof'] for r in self.all(c,'receipts') if r['event_id']==ev['event_id']]
        ev['terminal_evidence']={'contract_version':1,'order_terminal':True,'commitment':'finalized',
            'source':proof['source'],'observed_at':self.clock(),'receipt_signatures':sorted(p['signature'] for p in proofs),
            **{k:ev[k] for k in ('event_id','order_id','request_id','wallet','mint','side','filled_input_raw','filled_output_raw')}}
        self.validate_terminal_evidence(ev,proofs,current=self.clock())
        ev["state"] = "FILLED"
        self.put(c,'events',ev['event_id'],ev)
        self.machine_transition(c,trade,ev['side']+'_FILLED','trusted_terminal_order_and_finalized_receipts')
        self.machine_transition(c,trade,'RECONCILING','terminal_order_pending_wallet_reconciliation',terminal_unreconciled=True)
        return True

    def reconcile(self, observation, *, deadline=None, read_guard=None):
        """Internal trusted-adapter input; includes both executor and chain views.

        HTTP never accepts a caller assertion of an entire wallet snapshot.
        Full inventories and terminal executor states are required to unblock.
        """
        for k in ("wallet", "observed_at", "chain_positions", "executor_positions", "orders", "balance_lamports"):
            if k not in observation:
                raise StateError("incomplete_reconciliation")
        if age(observation["observed_at"], self.clock()) > self.cfg["reconciliation_max_age_seconds"]:
            raise StateError("stale_reconciliation")
        chain = {k: str(raw(v)) for k, v in observation["chain_positions"].items() if raw(v)}
        executor = {k: str(raw(v)) for k, v in observation["executor_positions"].items() if raw(v)}
        balance = raw(observation["balance_lamports"])
        def fresh():
            self.reconciliation_fresh(observation, deadline)
            if read_guard is not None:
                read_guard()
        with self.transaction(deadline=deadline, before_commit=fresh) as c:
            self.reconciliation_fresh(observation, deadline)
            if not self.wallet_binding(c, observation['wallet']):
                return False
            from execution_jupiter import record_failed_receipt
            for proof in observation.get('failed_receipts',[]):
                record_failed_receipt(self,c,proof)
            recovered = observation.get('recovered_fills', [])
            if not isinstance(recovered, list):
                raise StateError('invalid_recovered_fills')
            for fill in recovered:
                if not self._feedback(c, fill['data'], fill['proof']):
                    return False
            self.reconciliation_fresh(observation, deadline)
            self.expire(c)
            trades = self.all(c, "trades")
            from execution_validation import foreign_inventory
            expected = foreign_inventory(self, c)
            for trade in trades:
                if trade["wallet"] and trade["wallet"] != observation["wallet"]:
                    self.halt(c, "reconciliation_wallet_mismatch")
                    return False
                if raw(trade["remaining_raw"]):
                    expected[trade["mint"]] = str(raw(expected.get(trade["mint"], "0")) + raw(trade["remaining_raw"]))
            if chain != executor or chain != expected:
                self.halt(c, "position_balance_disagreement")
                for trade in trades:
                    if trade["state"] not in TERMINAL | {"CANDIDATE", "APPROVED"}:
                        self.transition(c, trade, "RECONCILIATION_REQUIRED", "position_balance_disagreement")
                return False
            for ev in self.all(c, "events"):
                if self.get(c, "trades", ev["trade_id"])["state"] == "CLOSED":
                    continue
                if ev["state"] in {"SENT", "ACK", "PARTIAL", "UNKNOWN", "FILLED"}:
                    remote = observation["orders"].get(ev["event_id"])
                    if not remote or remote.get("state") != "FILLED" or ev["state"] != "FILLED" or not ev["order_id"] or remote.get("order_id") != ev["order_id"]:
                        self.halt(c, "unresolved_executor_order")
                        return False
            previous = self.meta(c, "wallet_balance_lamports")
            accounted = sum(raw(t["wallet_delta_lamports"], signed=True) for t in trades)
            base = self.meta(c, "initial_balance_lamports")
            if base is not None and balance != raw(base) + accounted:
                self.halt(c, "native_balance_disagreement")
                return False
            if previous is None:
                self.set_meta(c, "initial_balance_lamports", str(balance - accounted))
            generation=(self.meta(c,'reconciliation_generation') or 0)+1
            revision=identity('reconciliation',generation,observation,accounted)
            self.set_meta(c,'reconciliation_generation',generation)
            self.set_meta(c, "wallet", observation["wallet"])
            self.set_meta(c, "wallet_balance_lamports", str(balance))
            self.set_meta(c, "reconciled_at", observation["observed_at"])
            self.set_meta(c, "reconciliation_required", False)
            self.set_meta(c, "reconciliation_revision",revision)
            self.audit(c,'reconciliation',{'revision':revision,'generation':generation,'observation':observation,
                       'trade_positions':{t['trade_id']:t['remaining_raw'] for t in trades}})
            for trade in trades:
                if trade["state"] == "SELL_FILLED" or (trade["state"] == "RECONCILIATION_REQUIRED" and raw(trade["buy_raw"]) and raw(trade["remaining_raw"]) == 0):
                    trade["real_net_pnl_lamports"] = trade["wallet_delta_lamports"]
                    trade['position_reconciliation_revision']=revision
                    self.compare(trade)
                    self.transition(c, trade, "CLOSED", "chain_and_executor_and_balance_reconciled")
                elif trade['execution_state']=='RECONCILING' and raw(trade["remaining_raw"]) > 0:
                    trade['position_reconciliation_revision']=revision
                    self.transition(c, trade, "POSITION_OPEN", "chain_and_executor_reconciled")
                from execution_positions import require_risk_trigger
                if trade['execution_state']=='POSITION_OPEN' and (trade['exit_requested'] or require_risk_trigger(self, c, trade)):
                    self.new_event(c,trade,'SELL',reason=self.exit_reason(c,trade))
            # Bind valuations to the exact reconciled inventory, cash and
            # receipt generation. A new observation always invalidates old marks.
            self.set_meta(c, "api_failures", 0)
            # Legacy/identity/corruption conflicts require a separately reviewed repair.
            recoverable = {"restart_with_execution_obligation", "execution_timeout", "partial_execution",
                           "position_balance_disagreement", "native_balance_disagreement", "unresolved_executor_order",
                           "execution_timeout", "execution_failure", "execution_unknown_execution", "deferred_after_send",
                           "unverified_fill", "api_failure_limit"}
            reasons = self.meta(c, "kill_reasons") or ([self.meta(c, "kill_reason")] if self.meta(c, "kill_reason") else [])
            reasons = [reason for reason in reasons if reason not in recoverable]
            self.set_meta(c, "kill_reasons", reasons)
            self.set_meta(c, "kill_reason", reasons[0] if reasons else None)
            # Entry cash is invested capital, not a realized loss. Only closed,
            # reconciled round trips contribute to this realized session limit.
            net = sum(raw(t["real_net_pnl_lamports"], signed=True) for t in self.all(c, "trades")
                      if t["session"] == self.meta(c, "session") and t["state"] == "CLOSED")
            if net <= -raw(self.cfg["max_session_loss_lamports"]):
                self.halt(c, "session_loss_limit")
            from execution_validation import reconciled
            reconciled(self, c)
            self.reconciliation_fresh(observation, deadline)
            return not self.meta(c, "kill_reason")

    def reconciliation_fresh(self, observation, deadline):
        if deadline is not None and time.monotonic() >= deadline:
            raise StateError('reconciliation_read_deadline')
        if age(observation['observed_at'], self.clock()) > self.cfg['reconciliation_max_age_seconds']:
            raise StateError('stale_reconciliation')

    def reconciliation_matches(self, c, observation):
        """One transaction's current inventory, bound to its latest audit."""
        if self.meta(c, 'reconciliation_required'):
            return False
        row = c.execute("SELECT at,doc FROM audit WHERE action='reconciliation' ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            return False
        record = json.loads(row['doc'])
        generation, revision = (self.meta(c, key) for key in
                                ('reconciliation_generation', 'reconciliation_revision'))
        trades = self.all(c, 'trades')
        accounted = sum(raw(t['wallet_delta_lamports'], signed=True) for t in trades)
        positions = {t['trade_id']: t['remaining_raw'] for t in trades}
        from execution_validation import foreign_inventory
        inventory = foreign_inventory(self, c)
        for trade in trades:
            if raw(trade['remaining_raw']):
                inventory[trade['mint']] = str(raw(inventory.get(trade['mint'], '0')) + raw(trade['remaining_raw']))
        if (set(record) != {'revision', 'generation', 'observation', 'trade_positions'}
                or canonical(record['observation']) != canonical(observation)
                or canonical(record['trade_positions']) != canonical(positions)
                or type(generation) is not int or generation < 1
                or type(record['generation']) is not int or record['generation'] != generation
                or record['revision'] != revision
                or revision != identity('reconciliation', generation, observation, accounted)
                or self.meta(c, 'reconciled_at') != observation['observed_at']
                or self.meta(c, 'wallet') != observation['wallet']
                or self.meta(c, 'wallet_balance_lamports') != str(raw(observation['balance_lamports']))
                or raw(self.meta(c, 'initial_balance_lamports')) + accounted != raw(observation['balance_lamports'])
                or {k: str(raw(v)) for k, v in observation['chain_positions'].items() if raw(v)} != inventory
                or {k: str(raw(v)) for k, v in observation['executor_positions'].items() if raw(v)} != inventory):
            return False
        if c.execute("SELECT count(*) FROM audit WHERE action='reconciliation' AND json_extract(doc,'$.revision')=?",
                     (revision,)).fetchone()[0] != 1:
            return False
        age(observation['observed_at'], row['at'])
        age(row['at'], self.clock())
        for event in self.all(c, 'events'):
            if event['state'] in {'SENT', 'ACK', 'PARTIAL', 'UNKNOWN'}:
                return False
            if event['state'] == 'FILLED' and self.get(c, 'trades', event['trade_id'])['state'] != 'CLOSED':
                remote = observation['orders'].get(event['event_id'])
                if (not isinstance(remote, dict) or remote.get('state') != 'FILLED'
                        or not event['order_id'] or remote.get('order_id') != event['order_id']):
                    return False
        self.reconciliation_fresh(observation, None)
        return True

    def wallet_binding(self, c, wallet):
        """Commit a permanent guard; never replace an established anchor."""
        previous = self.meta(c, 'wallet')
        history = (self.meta(c, 'reconciliation_generation') != 0
                   or any(self.meta(c, key) is not None for key in
                          ('initial_balance_lamports', 'reconciled_at', 'reconciliation_revision'))
                   or c.execute("SELECT 1 FROM audit WHERE action='reconciliation' LIMIT 1").fetchone())
        if (not isinstance(wallet, str) or not wallet.strip() or len(wallet) > 128
                or (previous is not None and (not isinstance(previous, str) or not previous.strip() or len(previous) > 128))
                or (previous is None and history)):
            self.halt(c, 'reconciliation_wallet_anchor_invalid')
            return False
        if previous is not None and previous != wallet:
            self.halt(c, 'reconciliation_wallet_mismatch')
            return False
        return True

    def mark_equity(self, observation, *, deadline=None, read_guard=None):
        """Trusted reader contract: executable SOL liquidation marks, never fills.

        The audit table is the durable time series. All calculations, the peak
        and a permanent >15% drawdown kill are committed in one transaction.
        Fees/rent already in wallet cash deltas are not charged a second time.
        Future exit fee/rent budgets must be included in each conservative mark.
        """
        started_mono, started_wall = time.monotonic(), self.clock()
        with self.transaction(deadline=deadline) as c:
            if deadline is not None and time.monotonic() >= deadline:
                raise StateError('reconciliation_read_deadline')
            self.expire(c)
            observed = observation.get('observed_at')
            if self.meta(c, 'reconciliation_required') or not self.meta(c, 'reconciled_at'):
                raise StateError('equity_requires_reconciliation')
            for at in (observed, self.meta(c, 'reconciled_at')):
                if age(at, self.clock()) > self.cfg['reconciliation_max_age_seconds']:
                    raise StateError('stale_equity_observation')
            revision = self.meta(c, 'reconciliation_revision')
            if not revision or observation.get('reconciliation_revision') != revision:
                raise StateError('equity_inventory_revision_mismatch')
            wallet = self.meta(c, 'wallet')
            cash = raw(self.meta(c, 'wallet_balance_lamports'))
            if observation.get('wallet') != wallet or raw(observation.get('balance_lamports')) != cash:
                raise StateError('equity_wallet_or_cash_mismatch')
            active = {t['trade_id']: t for t in self.all(c, 'trades') if raw(t['remaining_raw'])}
            marks = observation.get('marks')
            if not isinstance(marks, dict) or set(marks) != set(active):
                raise StateError('equity_incomplete_marks')
            value = 0
            for tid, trade in active.items():
                mark = marks[tid]
                from execution_validation import require as validation_contract
                source = 'JUPITER_V2_ORDER_QUOTE_OBSERVATION' if validation_contract(self, c) else 'GMGN_ORDER_QUOTE'
                if mark.get('wallet') != wallet or mark.get('mint') != trade['mint'] or mark.get('source') != source:
                    raise StateError('equity_mark_identity_mismatch')
                if raw(mark.get('remaining_raw')) != raw(trade['remaining_raw']):
                    raise StateError('equity_mark_quantity_mismatch')
                if age(mark.get('quoted_at'), self.clock()) > self.cfg['quote_max_age_seconds']:
                    raise StateError('stale_equity_quote')
                if not (datetime.fromisoformat(self.meta(c, 'reconciled_at').replace('Z', '+00:00'))
                        <= datetime.fromisoformat(mark['quoted_at'].replace('Z', '+00:00'))
                        <= datetime.fromisoformat(observed.replace('Z', '+00:00'))):
                    raise StateError('equity_mark_outside_observation')
                gross = raw(mark.get('minimum_output_lamports'))
                costs = raw(mark.get('exit_cost_budget_lamports'))
                if gross <= 0:
                    raise StateError('equity_missing_executable_quote')
                value += gross - costs
            start = raw(self.meta(c, 'initial_balance_lamports'))
            if start <= 0:
                raise StateError('equity_missing_positive_baseline')
            previous = self.meta(c, 'equity')
            if previous is not None:
                self.validate_equity(previous)
            invalid = self.equity_invalidation(c)
            current = cash + value
            if current < 0:
                self.invalidate_valuation()
                # Independent, bounded persistence of UNKNOWN only. Arm before
                # any further SQL; an expired read lease cannot revive old EQ.
                c.arm_safety_commit(min(30, max(2.5, self.cfg['ledger_lock_timeout_seconds'],
                                               self.cfg['quote_max_age_seconds'] + .5)))
            peak = max(start, current, raw(previous['peak_lamports']) if previous else start)
            dd = Decimal(peak - current) / peak
            max_dd = max(dd, decimal(previous['max_drawdown']) if previous else Decimal(0),
                         decimal(invalid['max_drawdown']) if invalid else Decimal(0))
            if current < 0:
                # Commit UNKNOWN and the hard guard; raising inside this TX
                # would restore a previously fresh positive valuation.
                invalid = {'reason': 'negative_liquidation_equity', 'wallet': wallet,
                    'observed_at': observed, 'reconciliation_revision': revision,
                    'projected_lamports': str(current), 'peak_lamports': str(peak),
                    'max_drawdown': str(max_dd)}
                self.set_meta(c, 'equity_invalid', invalid)
                self.audit(c, 'equity_invalid', invalid)
                self.halt(c, 'max_drawdown_15pct')
                return None
            # Original observation/quote budgets and nondecreasing wall time
            # apply to every positive COMMIT attempt, not just SQL entry.
            evidence = [(observed, self.cfg['reconciliation_max_age_seconds']),
                        (self.meta(c, 'reconciled_at'), self.cfg['reconciliation_max_age_seconds'])]
            evidence.extend((mark['quoted_at'], self.cfg['quote_max_age_seconds']) for mark in marks.values())
            original_deadline = started_mono + min(limit-age(at, started_wall) for at, limit in evidence)
            last_wall = started_wall
            def positive_fresh():
                nonlocal last_wall
                wall = self.clock()
                age(last_wall, wall)
                last_wall = wall
                if time.monotonic() >= original_deadline or any(age(at, wall) > limit for at, limit in evidence):
                    raise StateError('stale_equity_observation')
                if read_guard is not None:
                    read_guard()
            c.commit_guards.append(positive_fresh)
            positive_fresh()
            generation = self.valuation_fault()[1]
            realized = sum(raw(t['real_net_pnl_lamports'], signed=True) for t in self.all(c, 'trades')
                           if t['state'] == 'CLOSED')
            equity = {'wallet': wallet, 'observed_at': observed, 'reconciliation_revision': revision,
                      'start_lamports': str(start), 'current_lamports': str(current), 'peak_lamports': str(peak),
                      'realized_pnl_lamports': str(realized), 'unrealized_pnl_lamports': str(current - start - realized),
                      'current_drawdown': str(dd), 'max_drawdown': str(max_dd),
                      'source': 'RECONCILED_CASH_AND_EXECUTABLE_SELL_QUOTES', 'marks': marks}
            self.set_meta(c, 'equity', equity)
            self.set_meta(c, 'equity_invalid', None)
            self.audit(c, 'equity_mark', equity)
            if 100 * (peak - current) > 15 * peak:
                self.halt(c, 'max_drawdown_15pct')
            if deadline is not None and time.monotonic() >= deadline:
                raise StateError('reconciliation_read_deadline')
            c.after_commit.append(lambda: self.clear_valuation_after_commit(generation))
            return equity

    @staticmethod
    def validate_equity(equity):
        try:
            start, current, peak = (raw(equity[k]) for k in ('start_lamports', 'current_lamports', 'peak_lamports'))
            dd, maximum = (decimal(equity[k]) for k in ('current_drawdown', 'max_drawdown'))
            if start <= 0 or peak < max(start, current) or dd != Decimal(peak - current) / peak or not dd <= maximum:
                raise StateError('corrupt_equity')
        except (KeyError, TypeError):
            raise StateError('corrupt_equity') from None

    def equity_invalidation(self, c):
        invalid = self.meta(c, 'equity_invalid')
        row = c.execute("SELECT action,doc FROM audit WHERE action IN ('equity_mark','equity_invalid','equity_baseline_reset') ORDER BY id DESC LIMIT 1").fetchone()
        if invalid is None:
            if row and row['action'] == 'equity_invalid':
                raise StateError('equity_invalidation_lost')
            return None
        fields = {'reason','wallet','observed_at','reconciliation_revision','projected_lamports','peak_lamports','max_drawdown'}
        if (not isinstance(invalid, dict) or set(invalid) != fields
                or invalid['reason'] != 'negative_liquidation_equity'
                or invalid['wallet'] != self.meta(c, 'wallet')
                or not row or row['action'] != 'equity_invalid' or json.loads(row['doc']) != invalid
                or raw(invalid['projected_lamports'], signed=True) >= 0
                or raw(invalid['peak_lamports']) <= 0 or decimal(invalid['max_drawdown']) <= 1):
            raise StateError('equity_invalidation_unknown')
        age(invalid['observed_at'], self.clock())
        return invalid

    def equity_status(self, c):
        invalid = self.equity_invalidation(c)
        equity = self.meta(c, 'equity')
        if equity is None:
            return None
        self.validate_equity(equity)
        result = dict(equity)
        fault = self.valuation_fault()[0]
        result['fresh'] = (not fault and invalid is None and not self.meta(c, 'reconciliation_required')
            and equity['reconciliation_revision'] == self.meta(c, 'reconciliation_revision')
            and bool(self.meta(c, 'reconciled_at'))
            and age(self.meta(c, 'reconciled_at'), self.clock()) <= self.cfg['reconciliation_max_age_seconds']
            and age(equity['observed_at'], self.clock()) <= self.cfg['reconciliation_max_age_seconds']
            and all(age(mark['quoted_at'], self.clock()) <= self.cfg['quote_max_age_seconds']
                    for mark in equity['marks'].values()))
        if invalid is not None:
            result.update(valuation_state='UNKNOWN', invalid_reason=invalid['reason'],
                          max_drawdown=invalid['max_drawdown'])
        elif fault:
            result.update(valuation_state='UNKNOWN', invalid_reason='valuation_persistence_unknown')
        return result

    @staticmethod
    def compare(trade):
        if trade["shadow"] and trade["real_net_pnl_lamports"] is not None:
            shadow = trade["shadow"]
            trade["comparison"] = {"evidence_class": "FINALIZED_CHAIN_VS_SHADOW",
                "sim_net_pnl_lamports": shadow["net_pnl_lamports"],
                "real_net_pnl_lamports": trade["real_net_pnl_lamports"],
                "pnl_delta_lamports": str(raw(trade["real_net_pnl_lamports"], signed=True) - raw(shadow["net_pnl_lamports"], signed=True)),
                "shadow": shadow, "real_fees_lamports": trade["fees_lamports"]}

    def shadow(self, tid, result):
        raw(result.get("net_pnl_lamports"), signed=True)
        if not result.get("source") or result.get("evidence_class") != "SIMULATION":
            raise StateError("invalid_shadow_provenance")
        with self.transaction() as c:
            trade = self.get(c, "trades", tid)
            if not trade:
                raise StateError("unknown_trade")
            if trade["shadow"] is not None and trade["shadow"] != result:
                raise StateError("shadow_identity_conflict")
            trade["shadow"] = result
            self.compare(trade)
            self.put(c, "trades", tid, trade)
            self.audit(c, "shadow", result, tid)

    def api_failure(self, reason, *, deadline=None, details=None, temporary=False):
        if type(temporary) is not bool:raise StateError('invalid_read_failure_classification')
        if details is not None:
            limits = {'rpc_code': (-(2**31), 2**31-1), 'rpc_context_slot': (0, 2**64-1), 'http_status': (100, 599)}
            if (not isinstance(details, dict) or set(details) != set(limits)
                    or any(value is not None and (type(value) is not int or not limits[key][0] <= value <= limits[key][1])
                           for key, value in details.items())):
                raise StateError('invalid_read_failure_details')
        with self.transaction(deadline=deadline) as c:
            count = self.meta(c, "api_failures") + 1
            self.set_meta(c, "api_failures", count)
            self.audit(c, "api_failure", {"reason": reason, "count": count,
                                         **({'details': details} if details is not None else {})})
            from execution_validation import require as validation_contract
            validation=validation_contract(self,c)
            active=bool(validation and (validation['domain']=='ISOLATED' or self.meta(c,'validation_started') is True
                        or any(e.get('sent_at') for e in self.all(c,'events'))))
            if validation and (active or not temporary):
                self.halt(c, 'validation_read_failure')
            elif count >= self.cfg["max_api_failures"]:
                self.halt(c, "api_failure_limit",abort_validation=not validation)

    def control(self, action):
        materialize = []
        with self.transaction() as c:
            self.expire(c)
            if action in {"stop", "pause_buys"}:
                self.set_meta(c, "new_buys_enabled", False)
            elif action=='start_three_trade_validation':
                from execution_validation import status as validation_status
                from execution_policy import owner_gate
                validation=validation_status(self,c)
                if (not validation or validation['domain']!='FINALIZED_CHAIN' or validation['stopped'] or validation['aborted']
                        or owner_gate()!={'value':'ON','valid':True}
                        or not all(self.cfg[k] is True for k in ('signing','broadcast_enabled','live_trading_enabled'))
                        or self.meta(c,'reconciliation_required') or self.meta(c,'kill_reason')):
                    return False
                self.set_meta(c,'validation_started',True)
                self.set_meta(c,'new_buys_enabled',True)
            elif action == "resume_buys":
                from execution_validation import status as validation_status
                validation = validation_status(self, c)
                if validation and (validation['stopped'] or validation['aborted']):
                    return False
                if self.meta(c, "kill_reason") or self.meta(c, "reconciliation_required"):
                    return False
                self.set_meta(c, "new_buys_enabled", True)
            elif action == "reset_session":
                # Never discard positions, events, receipts or baseline on restart.
                if any(t["state"] not in TERMINAL | {"CANDIDATE"} for t in self.all(c, "trades")) or self.meta(c, "kill_reason") or self.meta(c, "reconciliation_required"):
                    return False
                self.set_meta(c, "session", identity(self.clock(), self.meta(c, "session")))
            elif action in {"emergency_exit", "session_exit"}:
                for trade in self.all(c, "trades"):
                    if raw(trade['remaining_raw'])>0:
                        self.request_exit(c, trade, action)
                        materialize.append(trade['trade_id'])
                self.set_meta(c, "new_buys_enabled", False)
            else:
                return False
            self.audit(c, "control", {"action": action})
        for tid in materialize:
            self.materialize_exit(tid)
        return True

    def status(self, *, connection=None, expire=True, deadline=None):
        context = (self.transaction() if deadline is None else self.transaction(deadline=deadline)) if connection is None else nullcontext(connection)
        with context as c:
            if expire:
                self.expire(c)
            self.validate_links(c)
            trades = self.all(c, "trades")
            isolated_ids = {e['trade_id'] for e in self.all(c, 'events') if e.get('evidence_domain') == 'ISOLATED'}
            closed = [t for t in trades if t['state'] == 'CLOSED' and t['real_net_pnl_lamports'] is not None
                      and t['trade_id'] not in isolated_ids]
            profits = [raw(t['real_net_pnl_lamports'], signed=True) for t in closed]
            positive = sum(p for p in profits if p > 0)
            negative = -sum(p for p in profits if p < 0)
            equity = self.equity_status(c)
            from execution_validation import status as validation_status
            validation = validation_status(self, c)
            performance = {'evidence_class':'FINALIZED_CHAIN_RECONCILED', 'source':str(self.path)+':trades/receipts/audit',
                           'unit':'lamports', 'scope':'closed reconciled trades in this ledger', 'count':len(closed),
                           'net_pnl_lamports':str(sum(profits)) if profits else None,
                           'win_rate':str(Decimal(sum(p > 0 for p in profits))/len(profits)) if profits else None,
                           'profit_factor':str(Decimal(positive)/negative) if negative else None,
                           'top3_positive_profit_share':str(Decimal(sum(sorted([p for p in profits if p>0],reverse=True)[:3]))/positive) if positive else None,
                           'top3_net_profit_share':str(Decimal(sum(sorted([p for p in profits if p>0],reverse=True)[:3]))/sum(profits)) if sum(profits) > 0 else None,
                           'first_trade_at':closed[0]['created_at'] if closed else None,
                           'last_closed_at':closed[-1]['updated_at'] if closed else None,
                           'wins':sum(p > 0 for p in profits), 'losses':sum(p < 0 for p in profits),
                           'gross_profit_lamports':str(positive), 'gross_loss_lamports':str(negative),
                           'drawdown':equity['max_drawdown'] if equity else None,
                           'drawdown_note':'conservative executable liquidation equity' if equity else 'wallet mark-to-market history not yet available'}
            return {"version": "imc-execution-ledger", "updated": self.clock(),
                    "schema_version": self.meta(c,'schema_version'),
                    "control_state": 'KILLED' if self.meta(c,'kill_reason') else ('PAUSED' if not self.meta(c,'new_buys_enabled') else 'RUNNING'),
                    "session_active": bool(self.meta(c, "new_buys_enabled")),
                    "new_buys_enabled": not self.valuation_fault()[0] and bool(self.meta(c, "new_buys_enabled")) and not self.meta(c, "kill_reason") and not self.meta(c, "reconciliation_required"),
                    "signals_enabled": not self.valuation_fault()[0] and bool(self.meta(c, "new_buys_enabled")) and (self.signals_only_safe(c) or (not self.meta(c, "kill_reason") and not self.meta(c, "reconciliation_required"))),
                    "kill_reason": self.meta(c, "kill_reason"),
                    "kill_reasons": self.meta(c, "kill_reasons"),
                    "reconciliation_required": self.meta(c, "reconciliation_required"),
                    "baseline_decision_at": self.meta(c, "baseline_decision_at"),
                    "open_positions": sum(raw(t["remaining_raw"]) > 0 for t in trades),
                    "buy_filled": sum(raw(t["buy_raw"]) > 0 for t in trades),
                    "sell_filled": sum(t["state"] == "CLOSED" for t in trades),
                    "max_session_trades": self.cfg["max_session_trades"],
                    "trades": trades, "events": self.all(c, "events"),
                    "performance": performance,
                    "equity": equity,
                    "equity_invalid": self.equity_invalidation(c),
                    "reconciliation_revision": self.meta(c, 'reconciliation_revision'),
                    "wallet": self.meta(c, 'wallet'),
                    "wallet_balance_lamports": self.meta(c, 'wallet_balance_lamports'),
                    "wallet_access": self.cfg['wallet_access'], "signing": self.cfg['signing'],
                    "broadcast": self.cfg['broadcast_enabled'], "live_trading": self.cfg['live_trading_enabled'],
                    "execution_authority_pct": self.cfg['execution_authority_pct'],
                    "execution_mode": validation['mode'] if validation else "SIGNAL_ONLY",
                    "validation": validation,
                    "real_performance_source": str(self.path) + ":trades/receipts/audit"}
