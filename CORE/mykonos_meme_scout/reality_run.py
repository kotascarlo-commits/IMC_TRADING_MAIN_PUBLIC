"""Durable run-scoped no-send ledger; view projections are never authoritative."""
from contextlib import contextmanager
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time
import uuid

from . import reality_accounting as accounting
from .execution_rules import number as D

ROOT = Path(__file__).resolve().parents[2]
BRIDGE = ROOT/'RUNTIME/EXECUTOR_GATEWAY/GMGN_BRIDGE'
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))
import execution_machine as machine

DB = ROOT/'RUNTIME/reality-run.db'
ACTIVE = ('STARTING', 'RUNNING', 'FINISHING', 'BLOCKED')
DOMAIN = 'FORWARD_SHADOW_CONSERVATIVE_BOUND_NOT_CHAIN'


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


class RealityBook:
    def __init__(self, path=DB, *, clock=time.time):
        self.clock, self.path = clock, Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.c = sqlite3.connect(self.path, isolation_level=None, timeout=5)
        self.c.row_factory = sqlite3.Row
        self.c.execute('PRAGMA synchronous=FULL')
        self.c.executescript('''
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, doc TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS trades(id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
                candidate_id TEXT NOT NULL, doc TEXT NOT NULL, UNIQUE(run_id,candidate_id));
            CREATE TABLE IF NOT EXISTS claims(id TEXT PRIMARY KEY, trade_id TEXT NOT NULL,
                side TEXT NOT NULL, doc TEXT NOT NULL, UNIQUE(trade_id,side));
            CREATE TABLE IF NOT EXISTS equity(id INTEGER PRIMARY KEY,run_id TEXT NOT NULL,
                at REAL NOT NULL,equity_eur TEXT);
            CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,run_id TEXT NOT NULL,
                at REAL NOT NULL,kind TEXT NOT NULL,doc TEXT NOT NULL);
        ''')

    def close(self):
        self.c.close()

    @contextmanager
    def transaction(self):
        self.c.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.c.execute('COMMIT')
        except BaseException:
            self.c.execute('ROLLBACK')
            raise

    def get(self, run_id=None):
        row = self.c.execute('SELECT doc FROM runs WHERE id=?', (run_id,)).fetchone() if run_id else \
            self.c.execute('SELECT doc FROM runs ORDER BY rowid DESC LIMIT 1').fetchone()
        return json.loads(row[0]) if row else None

    def save(self, run):
        run['revision'] += 1
        self.c.execute('UPDATE runs SET doc=? WHERE id=?', (encoded(run), run['run_id']))

    def audit(self, run, kind, value):
        self.c.execute('INSERT INTO audit(run_id,at,kind,doc) VALUES(?,?,?,?)',
                       (run['run_id'], self.clock(), kind, encoded(value)))

    def trades(self, run_id):
        return [machine.read(r[0]) for r in self.c.execute(
            'SELECT doc FROM trades WHERE run_id=? ORDER BY rowid', (run_id,))]

    def trade(self, trade_id):
        row = self.c.execute('SELECT doc FROM trades WHERE id=?', (trade_id,)).fetchone()
        if not row:
            raise ValueError('trade_missing')
        return machine.read(row[0])

    def save_trade(self, trade):
        revision = trade['revision']
        trade['revision'] += 1
        count = self.c.execute('UPDATE trades SET doc=? WHERE id=? AND json_extract(doc,\'$.revision\')=?',
            (encoded(machine.storage(trade)), trade['trade_id'], revision)).rowcount
        if count != 1:
            raise ValueError('stale_trade')

    def start(self, chosen_duration, policy):
        seconds = accounting.duration(chosen_duration)
        with self.transaction():
            if any(json.loads(r[0])['status'] in ACTIVE for r in self.c.execute('SELECT doc FROM runs')):
                raise ValueError('active_or_unresolved_run_exists')
            at = self.clock()
            run = {'schema_version':2,'run_id': uuid.uuid4().hex, 'revision': 1, 'status': 'STARTING',
                'evidence_domain': DOMAIN, 'duration': chosen_duration, 'duration_seconds': seconds,
                'started_at': at, 'planned_end': at+seconds, 'ended_at': None,
                'start_equity_eur': '1000.00', 'cash_eur': '1000.00', 'equity_eur': '1000.00',
                'heartbeat': at, 'stop_reason': None, 'stop_requested': False,
                'policy': policy, 'policy_hash': digest(policy), 'checkpoint': 0,
                'recovery_events': 0, 'runtime_errors': 0, 'unknown': 0, 'rejected': 0,
                'valuation_complete': True, 'cleanup_confirmed': False,
                'owner_gate': 'OFF', 'broadcast': False, 'signing': False, 'live': False}
            self.c.execute('INSERT INTO runs VALUES(?,?)', (run['run_id'], encoded(run)))
            self.record_equity(run, Decimal(1000),source={'kind':'INITIAL'})
            self.audit(run, 'run_frozen', run)
            return run

    def record_equity(self, run, equity, *,source=None,pending=False):
        run['equity_eur'] = None if equity is None else str(equity)
        if pending:
            run['valuation_pending_since']=self.clock()
            return
        pending_at=run.get('valuation_pending_since')
        if pending_at is not None:
            if self.clock()-pending_at>30:
                run['valuation_complete']=False
            run['valuation_pending_since']=None
        if equity is None:
            run['valuation_complete'] = False
        at=self.clock()
        cursor=self.c.execute('INSERT INTO equity(run_id,at,equity_eur) VALUES(?,?,?)',
                       (run['run_id'], at, run['equity_eur']))
        included=[json.loads(r[0])['receipt_identity'] for r in self.c.execute(
            "SELECT doc FROM claims WHERE json_extract(doc,'$.run_id')=? AND json_extract(doc,'$.state')='RECONCILED'",(run['run_id'],))]
        source=source or {'kind':'UNKNOWN'}
        if source.get('receipt_identity'):
            included.append(source['receipt_identity'])
        self.audit(run,'equity_point',{'id':cursor.lastrowid,'at':at,'equity_eur':run['equity_eur'],
            'cash_eur':run['cash_eur'],'included_receipts':included,'source':source})

    def control(self, command, run_id=None):
        with self.transaction():
            run = self.get(run_id)
            if run is None:
                raise ValueError('no_run')
            if command == 'stop' and run['status'] in ACTIVE:
                run.update(stop_requested=True, stop_reason=run['stop_reason'] or 'MANUAL_STOP')
                if run['status'] != 'BLOCKED':
                    run['status'] = 'FINISHING'
                self.save(run)
                self.audit(run, 'controlled_stop_requested', {})
            return run

    def tick(self, run_id):
        with self.transaction():
            run = self.get(run_id)
            current = self.clock()
            if current < run['heartbeat']:
                run.update(status='BLOCKED', stop_reason='CLOCK_ROLLBACK', stop_requested=True)
            elif run['status'] == 'STARTING':
                run['status'] = 'RUNNING'
            if run['status'] == 'RUNNING' and current >= run['planned_end']:
                run.update(status='FINISHING', stop_reason='DURATION_ELAPSED', stop_requested=True)
            run['heartbeat'] = current
            self.save(run)
            return run

    def claim(self, run_id, candidate, snapshot_id, *, side='BUY', trade_id=None, input_raw=None, reserve_eur=None):
        with self.transaction():
            run = self.get(run_id)
            self.verify(run)
            if side not in ('BUY', 'SELL'):
                raise ValueError('side_invalid')
            if side == 'BUY':
                if (run['status'] != 'RUNNING' or run['stop_requested'] or self.clock() >= run['planned_end']
                        or run['equity_eur'] is None or self.open_trade(run_id)):
                    raise ValueError('entry_not_allowed')
                if len([t for t in self.trades(run_id) if t['execution_state'] != 'REJECTED']) >= run['policy']['max_session_trades']:
                    raise ValueError('session_trade_limit')
                stake = accounting.size(run['equity_eur'], run['cash_eur'],
                    run['policy']['position_fraction'], run['policy']['reserve_eur'] if reserve_eur is None else reserve_eur)
                trade_id = digest({'run': run_id, 'snapshot': snapshot_id, 'mint': candidate['address']})
                trade = {'trade_id': trade_id, 'run_id': run_id, 'mint': candidate['address'],
                    'symbol': candidate.get('symbol', 'UNKNOWN'), 'candidate': candidate,
                    'candidate_id': str(snapshot_id), 'stake_eur': str(stake),
                    'entry_at': None, 'exit_at': None, 'remaining_raw': '0',
                    'reconciled': False, 'performance_qualified': False, 'receipts': [],
                    'last_mark': None, 'peak_liquidation_eur': None, 'exit_reason': None}
                machine.seed(trade)
                machine.move(trade, 'RISK_CHECK')
                machine.move(trade, 'READY')
                trade['execution_side'] = 'BUY'
                trade['execution_event_id'] = digest({'trade': trade_id, 'side': side})
                machine.move(trade, 'BUY_REQUESTED')
                trade['revision'] = 1
                self.c.execute('INSERT INTO trades VALUES(?,?,?,?)',
                    (trade_id, run_id, str(snapshot_id), encoded(machine.storage(trade))))
            else:
                trade = self.trade(trade_id)
                if trade['run_id'] != run_id or trade['execution_state'] != 'POSITION_OPEN' or int(trade['remaining_raw']) <= 0:
                    raise ValueError('sell_requires_verified_rest_position')
                trade['execution_side'] = 'SELL'
                trade['execution_event_id'] = digest({'trade': trade_id, 'side': side})
                machine.move(trade, 'SELL_REQUESTED')
                self.save_trade(trade)
            claim_id = digest({'trade': trade_id, 'side': side, 'kind': 'shadow_claim'})
            claim = {'claim_id': claim_id, 'request_id': digest({'claim': claim_id, 'kind': 'request'}),
                'trade_id': trade_id, 'run_id': run_id, 'side': side, 'state': 'CLAIMED',
                'claimed_at': self.clock(), 'evidence_domain': DOMAIN, 'mint': trade['mint'],
                'wallet': run['policy']['wallet']}
            if input_raw is None or int(input_raw) <= 0:
                raise ValueError('claim_input_required')
            claim['input_raw'] = str(input_raw)
            claim.update(stake_eur=trade['stake_eur'] if side=='BUY' else None,
                equity_eur_at_claim=run['equity_eur'],cash_eur_at_claim=run['cash_eur'],
                reserve_eur_at_claim=str(run['policy']['reserve_eur'] if reserve_eur is None else reserve_eur),
                fee_limit_lamports=str(run['policy'].get('config',run['policy'])['max_order_fee_lamports']))
            if side == 'SELL' and claim['input_raw'] != trade['remaining_raw']:
                raise ValueError('sell_claim_quantity_mismatch')
            self.c.execute('INSERT INTO claims VALUES(?,?,?,?)', (claim_id, trade_id, side, encoded(claim)))
            self.audit(run, 'claim', claim)
            return trade, claim

    def open_trade(self, run_id):
        return next((t for t in self.trades(run_id) if t['execution_state'] not in ('CLOSED', 'REJECTED')), None)

    def failure(self, run_id, reason, *, trade_id=None, verified_rejection=False):
        with self.transaction():
            run = self.get(run_id)
            run['rejected' if verified_rejection else 'unknown'] += 1
            if trade_id:
                trade = self.trade(trade_id)
                # A pre-fill shadow attempt cannot be retried on a crash/unknown.
                target = ('SELL_UNKNOWN' if trade['execution_side'] == 'SELL' else 'BUY_UNKNOWN')
                if trade['execution_state'] != target:
                    machine.move(trade, target, reason='RECOVERY')
                trade['unknown_reason'] = 'RECOVERY'
                self.save_trade(trade)
                self.record_equity(run, None)
                run.update(status='BLOCKED', stop_reason=reason, stop_requested=True)
            self.audit(run, 'rejected' if verified_rejection else 'unknown', {'reason': reason, 'trade_id': trade_id})
            self.save(run)

    def fill(self, claim, evidence, *, mid_eur, output_eur=None):
        from .reality_transport import validate_evidence
        validate_evidence(claim, evidence, self.clock())
        with self.transaction():
            row = self.c.execute('SELECT doc FROM claims WHERE id=?', (claim['claim_id'],)).fetchone()
            if not row or json.loads(row[0]) != claim or claim['state'] != 'CLAIMED':
                raise ValueError('duplicate_or_changed_claim')
            run = self.get(claim['run_id'])
            self.verify(run)
            trade = self.trade(claim['trade_id'])
            if run['status'] == 'BLOCKED':
                raise ValueError('run_blocked')
            if claim['side'] == 'BUY' and (run['stop_requested'] or self.clock() >= run['planned_end']):
                raise ValueError('entry_expired_before_fill')
            mid = D(mid_eur)
            if mid <= 0:
                raise ValueError('mid_price_unknown')
            evidence['independent_mid_eur']=str(mid)
            # Both cost components refer to the same unsigned simulation message.
            network = D(evidence['network_eur'])
            other = D(evidence['other_eur'])
            quantity = Decimal(int(evidence['output_raw']))/(Decimal(10)**evidence['output_decimals'])
            if claim['side'] == 'BUY':
                expected = int(D(trade['stake_eur'])/D(evidence['fx']['sol_eur_ask'])*10**9)
                if int(evidence['input_raw']) != expected:
                    raise ValueError('equity_sizing_input_mismatch')
                spend = D(trade['stake_eur']) + network + other
                if spend+D(claim['reserve_eur_at_claim']) > D(run['cash_eur']):
                    raise ValueError('virtual_cash_or_reserve_insufficient')
                run['cash_eur'] = str(D(run['cash_eur'])-spend)
                trade.update(entry_at=self.clock(), remaining_raw=evidence['output_raw'], entry_mid_eur=str(mid),
                    entry_price=str(D(trade['stake_eur'])/quantity), entry_network_eur=str(network),
                    entry_other_eur=str(other), entry_execution_eur=str(D(trade['stake_eur'])-quantity*mid),
                    entry_slippage_eur=evidence['slippage_eur'], entry_decimals=evidence['output_decimals'],
                    peak_liquidation_eur=None)
                machine.move(trade, 'BUY_FILLED')
                machine.move(trade, 'RECONCILING')
                machine.move(trade, 'POSITION_OPEN')
                # Future executable liquidation/exit costs are not known from a
                # BUY simulation. Publish UNKNOWN until a verified exit mark.
                self.record_equity(run,None,pending=True)
            else:
                if evidence['input_raw'] != trade['remaining_raw']:
                    raise ValueError('sell_rest_position_mismatch')
                returned = D(output_eur)
                qty = D(trade['remaining_raw'])/(Decimal(10)**trade['entry_decimals'])
                gross = qty*(mid-D(trade['entry_mid_eur']))
                execution = D(trade['entry_execution_eur']) + qty*mid-returned
                network += D(trade['entry_network_eur'])
                other += D(trade['entry_other_eur'])
                costs = execution+network+other
                net = returned-D(trade['stake_eur'])-network-other
                if gross-costs != net:
                    # Decimal division can round at the last digit; never hide a cent mismatch.
                    if abs(gross-costs-net) > Decimal('.000000001'):
                        raise ValueError('ledger_pnl_mismatch')
                remaining_cash=D(run['cash_eur'])+returned-D(evidence['network_eur'])-D(evidence['other_eur'])
                if remaining_cash<D(claim['reserve_eur_at_claim']):
                    raise ValueError('virtual_exit_cash_or_reserve_insufficient')
                run['cash_eur'] = str(remaining_cash)
                trade.update(exit_at=self.clock(), exit_price=str(returned/qty), remaining_raw='0',
                    exit_returned_eur=str(returned),
                    gross_eur=str(gross), net_eur=str(net), costs_eur=str(costs),
                    gross_pct=str(100*gross/D(trade['stake_eur'])), net_pct=str(100*net/D(trade['stake_eur'])),
                    execution_eur=str(execution), network_eur=str(network), other_eur=str(other),
                    slippage_eur=str(D(trade['entry_slippage_eur'])+D(evidence['slippage_eur'])),
                    reconciled=True, performance_qualified=True)
                machine.move(trade, 'SELL_FILLED')
                machine.move(trade, 'RECONCILING')
                machine.move(trade, 'CLOSED')
                self.record_equity(run,D(run['cash_eur']),source={'kind':'CLOSED',
                    'trade_id':trade['trade_id'],'receipt_identity':digest(evidence)})
            trade['receipts'].append(evidence)
            self.save_trade(trade)
            done = dict(claim, state='RECONCILED', receipt_identity=digest(evidence))
            self.c.execute('UPDATE claims SET doc=? WHERE id=?', (encoded(done), claim['claim_id']))
            self.audit(run, 'shadow_reconciled_not_chain', {'claim': done, 'evidence': evidence})
            self.save(run)

    def mark(self, run_id, value, *, mid, exit_reason=None,evidence=None):
        with self.transaction():
            run = self.get(run_id)
            trade = self.open_trade(run_id)
            if not trade or trade['execution_state'] != 'POSITION_OPEN':
                raise ValueError('mark_requires_verified_position')
            if value is not None:
                from .reality_transport import validate_mark
                validate_mark(trade,evidence,self.clock())
                evidence['independent_mid_eur']=str(D(mid))
                expected=D(evidence['output_raw'])/10**9*D(evidence['fx']['sol_eur_bid'])-D(evidence['network_eur'])-D(evidence['other_eur'])
                if expected!=D(value) or D(mid)<=0:
                    raise ValueError('shadow_net_mark_mismatch')
            trade['last_mark'] = {'at': self.clock(), 'value_eur': None if value is None else str(value),
                                  'mid_eur': None if mid is None else str(mid)}
            if value is not None:
                trade['peak_liquidation_eur'] = str(max(D(trade['peak_liquidation_eur']),D(value)) if trade['peak_liquidation_eur'] is not None else D(value))
                qty=D(trade['remaining_raw'])/(Decimal(10)**trade['entry_decimals'])
                trade['last_mark'].update(gross_eur=str(qty*(D(mid)-D(trade['entry_mid_eur']))),
                    net_eur=str(D(value)-D(trade['stake_eur'])-D(trade['entry_network_eur'])-D(trade['entry_other_eur'])),
                    estimated_exit_costs_eur=str(qty*D(mid)-D(evidence['output_raw'])/10**9*
                        D(evidence['fx']['sol_eur_bid'])+D(evidence['network_eur'])+D(evidence['other_eur'])),
                    evidence_identity=digest(evidence))
                self.audit(run,'net_position_mark',{'trade_id':trade['trade_id'],
                    'mark':trade['last_mark'],'peak':trade['peak_liquidation_eur'],'evidence':evidence})
            if exit_reason:
                trade['exit_reason'] = exit_reason
            self.save_trade(trade)
            self.record_equity(run,None if value is None else D(run['cash_eur'])+D(value),
                source={'kind':'UNKNOWN' if value is None else 'MARK','trade_id':trade['trade_id'],
                    'evidence_identity':digest(evidence) if evidence is not None else None})
            self.save(run)

    def recover(self, run_id):
        with self.transaction():
            run = self.get(run_id)
            self.verify(run)
            trade = self.open_trade(run_id)
            run['recovery_events'] += 1
            run.update(stop_requested=True, stop_reason='RESTART_RECOVERY', status='FINISHING')
            if trade and trade['execution_state'] in machine.UNRESOLVED:
                if not trade['execution_state'].endswith('_UNKNOWN'):
                    machine.move(trade, trade['execution_side']+'_UNKNOWN', reason='RECOVERY')
                trade['unknown_reason'] = 'RECOVERY'
                self.save_trade(trade)
                run['status'] = 'BLOCKED'
                self.record_equity(run, None)
            self.audit(run, 'recovery_no_resend', {'trade': trade['trade_id'] if trade else None})
            self.save(run)
            return run

    def abort(self, run_id, reason):
        with self.transaction():
            run=self.get(run_id)
            run.update(status='BLOCKED',stop_requested=True,stop_reason=reason)
            run['runtime_errors']+=1
            self.audit(run,'runtime_error',{'reason':reason})
            self.save(run)

    def finish(self, run_id, *, cleanup=False):
        with self.transaction():
            run = self.get(run_id)
            self.verify(run)
            if run['status'] not in ('FINISHING', 'BLOCKED'):
                raise ValueError('finish_requires_stop')
            trade = self.open_trade(run_id)
            pending = self.c.execute("SELECT 1 FROM claims WHERE json_extract(doc,'$.run_id')=? AND json_extract(doc,'$.state')='CLAIMED'", (run_id,)).fetchone()
            run['status'] = 'BLOCKED' if trade or pending or run['status']=='BLOCKED' else 'COMPLETED'
            run['ended_at'] = self.clock()
            run['cleanup_confirmed'] = cleanup
            self.save(run)
            self.audit(run, 'run_finalized', {'status': run['status'], 'open_obligation': bool(trade or pending)})
            return run

    def verify(self, run):
        """Restart/view checks the durable cash, quantities and receipt graph."""
        from .reality_transport import validate_evidence
        if digest(run['policy']) != run['policy_hash'] or run['start_equity_eur']!='1000.00':
            raise ValueError('reality_policy_or_capital_corrupt')
        frozen=self.c.execute("SELECT doc FROM audit WHERE run_id=? AND kind='run_frozen'", (run['run_id'],)).fetchall()
        if len(frozen)!=1:
            raise ValueError('reality_frozen_audit_missing')
        baseline=json.loads(frozen[0][0])
        if any(baseline[k]!=run[k] for k in ('policy','policy_hash','started_at','duration','planned_end')):
            raise ValueError('reality_frozen_contract_changed')
        cash=Decimal(1000)
        positions=0
        receipts={}
        for trade in self.trades(run['run_id']):
            sides=[]
            for receipt in trade['receipts']:
                row=self.c.execute('SELECT doc FROM claims WHERE id=?',(receipt['claim_id'],)).fetchone()
                if not row:
                    raise ValueError('shadow_receipt_claim_missing')
                claim=json.loads(row[0])
                validate_evidence(claim,receipt,receipt['observed_at'])
                if claim['state']!='RECONCILED' or claim['receipt_identity']!=digest(receipt):
                    raise ValueError('shadow_receipt_identity_corrupt')
                receipts[claim['receipt_identity']]=(receipt,trade)
                original=self.c.execute("SELECT doc FROM audit WHERE run_id=? AND kind='claim' AND json_extract(doc,'$.claim_id')=?",
                    (run['run_id'],claim['claim_id'])).fetchall()
                if len(original)!=1 or any(claim.get(k)!=v for k,v in json.loads(original[0][0]).items() if k!='state'):
                    raise ValueError('shadow_claim_audit_corrupt')
                if claim['side']=='BUY':
                    allocated=accounting.size(claim['equity_eur_at_claim'],claim['cash_eur_at_claim'],
                        run['policy']['position_fraction'],claim['reserve_eur_at_claim'])
                    if allocated!=D(trade['stake_eur']) or D(claim['stake_eur'])!=allocated:
                        raise ValueError('shadow_claim_sizing_corrupt')
                audit=self.c.execute("SELECT doc FROM audit WHERE run_id=? AND kind='shadow_reconciled_not_chain' AND json_extract(doc,'$.claim.claim_id')=?",
                    (run['run_id'],claim['claim_id'])).fetchall()
                if len(audit)!=1 or json.loads(audit[0][0])!={'claim':claim,'evidence':receipt}:
                    raise ValueError('shadow_receipt_audit_corrupt')
                sides.append(claim['side'])
                if claim['side']=='BUY':
                    cash-=D(trade['stake_eur'])+D(receipt['network_eur'])+D(receipt['other_eur'])
                else:
                    returned=D(receipt['output_raw'])/10**9*D(receipt['fx']['sol_eur_bid'])
                    if abs(returned-D(trade['exit_returned_eur']))>Decimal('.000000001'):
                        raise ValueError('shadow_exit_return_corrupt')
                    cash+=D(trade['exit_returned_eur'])-D(receipt['network_eur'])-D(receipt['other_eur'])
            if sides not in ([],['BUY'],['BUY','SELL']):
                raise ValueError('shadow_duplicate_fill')
            if trade['execution_state']=='CLOSED' and (sides!=['BUY','SELL'] or not trade['reconciled'] or not trade['performance_qualified']):
                raise ValueError('shadow_phantom_closed')
            if sides:
                remaining=int(trade['receipts'][0]['output_raw'])
                entry=trade['receipts'][0]
                qty=D(entry['output_raw'])/(Decimal(10)**entry['output_decimals'])
                mid=D(entry['independent_mid_eur'])
                expected_entry={'entry_mid_eur':mid,'entry_price':D(trade['stake_eur'])/qty,
                    'entry_network_eur':D(entry['network_eur']),'entry_other_eur':D(entry['other_eur']),
                    'entry_execution_eur':D(trade['stake_eur'])-qty*mid,'entry_slippage_eur':D(entry['slippage_eur'])}
                if any(abs(D(trade[k])-v)>Decimal('.000000001') for k,v in expected_entry.items()):
                    raise ValueError('shadow_entry_arithmetic_corrupt')
                if len(sides)==2:
                    remaining-=int(trade['receipts'][1]['input_raw'])
                    exit=trade['receipts'][1]
                    returned=D(exit['output_raw'])/10**9*D(exit['fx']['sol_eur_bid'])
                    gross=qty*(D(exit['independent_mid_eur'])-mid)
                    execution=D(trade['stake_eur'])-qty*mid+qty*D(exit['independent_mid_eur'])-returned
                    network=D(entry['network_eur'])+D(exit['network_eur'])
                    other=D(entry['other_eur'])+D(exit['other_eur'])
                    net=returned-D(trade['stake_eur'])-network-other
                    expected={'gross_eur':gross,'execution_eur':execution,'network_eur':network,'other_eur':other,
                        'net_eur':net,'costs_eur':execution+network+other,
                        'slippage_eur':D(entry['slippage_eur'])+D(exit['slippage_eur']),
                        'gross_pct':100*gross/D(trade['stake_eur']),'net_pct':100*net/D(trade['stake_eur']),
                        'exit_price':returned/qty}
                    if any(abs(D(trade[k])-v)>Decimal('.000000001') for k,v in expected.items()):
                        raise ValueError('shadow_closed_arithmetic_corrupt')
                if remaining!=int(trade['remaining_raw']) or remaining<0:
                    raise ValueError('shadow_position_quantity_corrupt')
            elif int(trade['remaining_raw']):
                raise ValueError('shadow_phantom_position')
            if int(trade['remaining_raw']):
                positions+=1
            marks=[json.loads(r[0]) for r in self.c.execute(
                "SELECT doc FROM audit WHERE run_id=? AND kind='net_position_mark' AND json_extract(doc,'$.trade_id')=? ORDER BY id",(run['run_id'],trade['trade_id']))]
            if marks:
                expected_peak=max(D(m['mark']['value_eur']) for m in marks)
                if D(trade['peak_liquidation_eur'])!=expected_peak or any(D(m['peak'])!=max(D(p['mark']['value_eur']) for p in marks[:i+1]) for i,m in enumerate(marks)):
                    raise ValueError('shadow_trailing_peak_corrupt')
                if trade['last_mark'] and trade['last_mark']['value_eur'] is not None and trade['last_mark']!=marks[-1]['mark']:
                    raise ValueError('shadow_latest_mark_corrupt')
            elif trade['peak_liquidation_eur'] is not None:
                raise ValueError('shadow_peak_without_mark')
        if positions>1 or abs(cash-D(run['cash_eur']))>Decimal('.000000001'):
            raise ValueError('shadow_cash_or_position_reconciliation_mismatch')
        curve=[dict(r) for r in self.c.execute('SELECT id,at,equity_eur FROM equity WHERE run_id=? ORDER BY id',(run['run_id'],))]
        points=[json.loads(r[0]) for r in self.c.execute("SELECT doc FROM audit WHERE run_id=? AND kind='equity_point' ORDER BY id",(run['run_id'],))]
        if not points and run.get('schema_version',1)==1 and not self.trades(run['run_id']) and len(curve)==1 and curve[0]['equity_eur']=='1000':
            return True # preserved pre-schema-2 zero-trade terminal control evidence
        if len(curve)!=len(points):
            raise ValueError('shadow_equity_curve_audit_missing')
        for row,point in zip(curve,points):
            if any(row[k]!=point[k] for k in ('id','at','equity_eur')) or len(point['included_receipts'])!=len(set(point['included_receipts'])):
                raise ValueError('shadow_equity_curve_corrupt')
            point_cash=Decimal(1000)
            for rid in point['included_receipts']:
                if rid not in receipts:
                    raise ValueError('shadow_equity_receipt_missing')
                receipt,trade=receipts[rid]
                point_cash-=D(receipt['network_eur'])+D(receipt['other_eur'])
                point_cash+=-D(trade['stake_eur']) if receipt['side']=='BUY' else D(receipt['output_raw'])/10**9*D(receipt['fx']['sol_eur_bid'])
            if abs(point_cash-D(point['cash_eur']))>Decimal('.000000001'):
                raise ValueError('shadow_equity_cash_corrupt')
            kind=point['source']['kind']
            expected=point_cash
            if kind=='MARK':
                marks=self.c.execute("SELECT doc FROM audit WHERE run_id=? AND kind='net_position_mark' AND json_extract(doc,'$.mark.evidence_identity')=?",
                    (run['run_id'],point['source']['evidence_identity'])).fetchall()
                if len(marks)!=1:
                    raise ValueError('shadow_equity_mark_missing')
                mark=json.loads(marks[0][0]);evidence=mark['evidence']
                trade=self.trade(mark['trade_id'])
                original=dict(trade,remaining_raw=evidence['input_raw'])
                from .reality_transport import validate_mark
                validate_mark(original,evidence,evidence['observed_at'])
                value=D(evidence['output_raw'])/10**9*D(evidence['fx']['sol_eur_bid'])-D(evidence['network_eur'])-D(evidence['other_eur'])
                if D(mark['mark']['value_eur'])!=value or digest(evidence)!=point['source']['evidence_identity']:
                    raise ValueError('shadow_equity_net_mark_corrupt')
                qty=D(trade['receipts'][0]['output_raw'])/(Decimal(10)**trade['entry_decimals'])
                expected_mark={'mid_eur':D(evidence['independent_mid_eur']),
                    'gross_eur':qty*(D(evidence['independent_mid_eur'])-D(trade['entry_mid_eur'])),
                    'net_eur':value-D(trade['stake_eur'])-D(trade['entry_network_eur'])-D(trade['entry_other_eur']),
                    'estimated_exit_costs_eur':qty*D(evidence['independent_mid_eur'])-
                        D(evidence['output_raw'])/10**9*D(evidence['fx']['sol_eur_bid'])+
                        D(evidence['network_eur'])+D(evidence['other_eur'])}
                if any(abs(D(mark['mark'][k])-v)>Decimal('.000000001') for k,v in expected_mark.items()):
                    raise ValueError('shadow_mark_arithmetic_corrupt')
                expected+=value
            elif kind=='UNKNOWN':
                if row['equity_eur'] is not None:
                    raise ValueError('shadow_unknown_equity_fabricated')
                continue
            elif kind not in ('INITIAL','CLOSED'):
                raise ValueError('shadow_equity_source_unknown')
            if row['equity_eur'] is None or abs(expected-D(row['equity_eur']))>Decimal('.000000001'):
                raise ValueError('shadow_equity_arithmetic_corrupt')
        # The mutable projection also feeds the next claim's equity sizing.
        # A valid historical curve cannot authorize a forged current equity.
        if run.get('valuation_pending_since') is not None:
            if run['equity_eur'] is not None or positions!=1:
                raise ValueError('shadow_pending_valuation_corrupt')
        elif (not curve or (run['equity_eur'] is None)!=(curve[-1]['equity_eur'] is None)
                or run['equity_eur'] is not None and D(run['equity_eur'])!=D(curve[-1]['equity_eur'])):
            raise ValueError('shadow_current_equity_corrupt')
        return True

    def status(self, run_id=None):
        self.c.execute('BEGIN')
        try:
            result=self._status(run_id)
            self.c.execute('COMMIT')
            return result
        except BaseException:
            self.c.execute('ROLLBACK')
            raise

    def _status(self, run_id=None):
        run = self.get(run_id)
        if not run:
            return {'status': 'IDLE', 'start_equity_eur': '1000.00', 'broadcast': False,
                    'signing': False, 'live': False, 'owner_gate': 'OFF', 'trades': [], 'open_position': None}
        self.verify(run)
        trades = self.trades(run['run_id'])
        curve = [dict(r) for r in self.c.execute('SELECT at,equity_eur FROM equity WHERE run_id=? ORDER BY id', (run['run_id'],))]
        result = dict(run, metrics=accounting.metrics(trades, curve, valuation_complete=run['valuation_complete'] and run['equity_eur'] is not None))
        result['elapsed_seconds'] = max(0, (run['ended_at'] or self.clock())-run['started_at'])
        result['heartbeat_age_seconds']=max(0,self.clock()-run['heartbeat'])
        result['runtime_health']='UNKNOWN' if run['status'] in ACTIVE and result['heartbeat_age_seconds']>30 else 'CURRENT' if run['status'] in ACTIVE else 'STOPPED'
        result['remaining_seconds'] = max(0, run['planned_end']-self.clock()) if run['status'] in ('STARTING','RUNNING') else 0
        result['trades'] = [machine.storage(t) for t in trades if t['execution_state'] == 'CLOSED']
        result['open_position'] = next((machine.storage(t) for t in trades if int(t['remaining_raw']) > 0), None)
        result['claims'] = [json.loads(r[0]) for r in self.c.execute('SELECT doc FROM claims WHERE json_extract(doc,\'$.run_id\')=?', (run['run_id'],))]
        result['last_diagnostics'] = [json.loads(r[0]) for r in self.c.execute(
            "SELECT doc FROM audit WHERE run_id=? AND kind IN ('unknown','runtime_error') ORDER BY id DESC LIMIT 5", (run['run_id'],))]
        result['actual_duration_seconds'] = result['elapsed_seconds']
        return result
