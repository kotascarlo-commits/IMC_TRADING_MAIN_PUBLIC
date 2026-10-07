"""Existing signal-only API backed by the durable execution ledger."""
import hmac
import json
import os
import sqlite3
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from execution_state import Ledger, StateError, load_config, timestamp
from execution_evidence import SolanaEvidence, GMGNOrderEvidence, ReconciliationReader
from execution_monitor import ReadMonitor, runtime_health
from execution_positions import PositionManager
from execution_adapter import SingleSendAdapter
from execution_policy import RiskEngine, CRITICAL, CAPABILITIES
from execution_transport import SDKContractTransport
from execution_state import age, decimal, raw
from execution_adapter import SOL
from mykonos_meme_scout.paper_forward import GMGNReader, GMGNReadError
from datetime import datetime, timezone
from mykonos_meme_scout.projection_io import write_json_projection
import threading
import time

HERE = Path(__file__).resolve().parent
RUNTIME = HERE.parents[1]
DB = RUNTIME / 'watch-breadth-v425-shadow.db'
STATE = HERE / 'pure_signal_state.json'  # legacy evidence, never overwritten
STATUS = HERE / 'pure_signal_status.json'
LEDGER_PATH = HERE / 'execution-ledger.db'
_LEDGER = None
_MONITOR = None
_POSITIONS = None
_EXECUTION = None
_EXECUTION_LOCK = threading.Lock()
_STATUS_LOCK = threading.Lock()
_DISPATCH_LOCK = threading.Lock()
_QUOTE_RETRY_AT = 0.0
_VALIDATION_QUOTE = None
_JUPITER_PROBE = None


def conn():
    c = sqlite3.connect(DB.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA query_only=ON')
    return c


def latest_decision_at():
    if not DB.exists():
        return timestamp()
    c = conn()
    try:
        row = c.execute('SELECT max(decision_at) FROM picks WHERE variant=?',
                        (load_config()['variant'],)).fetchone()
        return row[0] if row and row[0] else timestamp()
    finally:
        c.close()


def ledger():
    global _LEDGER
    if _LEDGER is None:
        # Initialization runs before the HTTP threads start.
        store = Ledger(LEDGER_PATH)
        # Existing baselines are unchanged; first start admits only future
        # generations. Discovery cannot prevent reconstruction of positions.
        store.bootstrap(timestamp(), STATE)
        _LEDGER = store
    return _LEDGER


def write_status(store, *, deadline=None):
    remaining = None if deadline is None else max(0, deadline-time.monotonic())
    acquired = _STATUS_LOCK.acquire() if remaining is None else _STATUS_LOCK.acquire(timeout=remaining)
    if not acquired:
        raise StateError('status_publication_busy')
    try:
        status = store.status(expire=False, deadline=deadline)
        status['pending'] = next((e for e in status['events'] if e['state'] == 'READY'), None)
        if deadline is not None and time.monotonic() >= deadline:
            raise StateError('reconciliation_read_deadline')
        write_json_projection(STATUS, status, retry_seconds=.25 if deadline is None else min(.25, deadline-time.monotonic()))
    finally:
        _STATUS_LOCK.release()
    return status


def read_monitor():
    global _MONITOR
    store = ledger()
    if _MONITOR is None or _MONITOR.store is not store:
        _MONITOR = ReadMonitor(store, store.path.with_name('read_monitor_status.json'),
                              reader_factory=lambda cfg: reconciliation_reader(store,cfg), publisher=write_status,
                              health_reader=runtime_health)
    return _MONITOR


def validation_contract(store):
    from execution_validation import require
    with store.transaction() as c:return require(store,c)


def reconciliation_reader(store,cfg):
    if validation_contract(store):
        from execution_jupiter_monitor import JupiterReconciliationReader
        return JupiterReconciliationReader(cfg)
    return ReconciliationReader(cfg)


def next_event():
    global _VALIDATION_QUOTE
    store = ledger()
    position_manager().require_current()
    pending = store.pending()
    if pending and pending['side'] == 'SELL':
        return pending
    mode=validation_contract(store)
    if mode:
        with store.transaction() as transaction:
            if not store.meta(transaction,'validation_started') or store.meta(transaction,'validation_abort'):
                return None
        status=store.status()
        for trade in status['trades']:
            if trade['execution_state']=='POSITION_OPEN' and int(trade['remaining_raw']):
                return store.prepare_sell(trade['trade_id'],reason='session_exit')
        if pending:return pending
        if not status['new_buys_enabled']:return None
        from execution_jupiter_read import JupiterExecutionReader
        from mykonos_meme_scout.jupiter_prepare import USDC
        # Fresh route inventory supplies the technical candidate evidence.
        # The same observation is rebound to the newly persisted event; no
        # second API quote, alternate transaction, or strategy PF is invented.
        try:
            observed=JupiterExecutionReader(store).quote({'event_id':'validation-candidate','mint':USDC,'side':'BUY'})
        except Exception:
            store.api_failure('validation_candidate_route_unavailable')
            raise
        tid=store.candidate({'source':'THREE_TRADE_VALIDATION_FIXED_PAIR','variant':store.cfg['variant'],
            'mint':USDC,'signal_at':store.clock(),'reference_price':'1',
            'reference_liquidity_usd':observed['liquidity_usd'],'validation_contract_identity':mode['identity']})
        event=store.prepare_buy(tid)
        if event:_VALIDATION_QUOTE=dict(observed,event_id=event['event_id'])
        return event
    if not DB.exists():
        return pending
    c = conn()
    try:
        # Sell only the exact candidate generation that opened the real position.
        for trade in store.status()['trades']:
            if int(trade['remaining_raw']) <= 0:
                continue
            row = c.execute('SELECT status,decision_at,exit_reason FROM picks WHERE address=? AND variant=?',
                            (trade['mint'], store.cfg['variant'])).fetchone()
            if row and row['status'] == 'CLOSED':
                ev = store.prepare_sell(trade['trade_id'], reason='strategy_exit', generation=row['decision_at'])
                if ev:
                    return ev
        if pending:
            return pending
        status = store.status()
        if not status['signals_enabled']:
            return None
        rows = c.execute('''SELECT address,decision_at,entry_at,entry_market,entry_fill,entry_liq,status
                            FROM picks WHERE variant=? AND accepted=1 AND decision_at>?
                            ORDER BY decision_at DESC LIMIT 100''',
                         (store.cfg['variant'], status['baseline_decision_at'])).fetchall()
        for row in reversed(rows):
            if row['status'] != 'OPEN':
                continue
            signal = {'source': 'watch-breadth-v425-shadow.db:picks', 'variant': store.cfg['variant'],
                      'mint': row['address'], 'signal_at': row['decision_at'], 'entry_at': row['entry_at'],
                      'reference_price': row['entry_market'], 'expected_entry': row['entry_fill'],
                      'reference_liquidity_usd': row['entry_liq'], 'safe100_approved': True}
            tid = store.candidate(signal)
            if tid:
                ev = store.prepare_buy(tid)
                if ev:
                    return ev
    finally:
        c.close()
    return None


def position_manager():
    global _POSITIONS
    store = ledger()
    if _POSITIONS is None or _POSITIONS.store is not store:
        _POSITIONS = PositionManager(store, store.path.with_name('position_manager_status.json'))
    return _POSITIONS


def execution_probes(side):
    """Known component evidence only; missing runtime probes stay UNKNOWN."""
    store = ledger()
    now = store.clock()
    heartbeats = {name: {'status': 'UNKNOWN', 'observed_at': now,
                         'source': 'gateway:' + name} for name in CRITICAL}
    capabilities = {name: False for name in CAPABILITIES}
    observed = read_monitor().heartbeat()
    runtime = observed.get('runtime_health', {})
    for name in ('CORE', 'MONITORING'):
        if name in runtime.get('heartbeats', {}):
            heartbeats[name] = runtime['heartbeats'][name]
    capabilities['watchdog'] = runtime.get('watchdog', {}).get('status') == 'PASS'
    for name in ('GMGN_API', 'RPC', 'RECONCILIATION', 'WALLET_READER'):
        heartbeats[name] = {'status': observed['status'],
            'observed_at': observed.get('observation_at') or now, 'source': 'gateway:ReadMonitor'}
    if validation_contract(store):
        heartbeats['JUPITER_API']=dict(_JUPITER_PROBE or {'status':'UNKNOWN','observed_at':now,'source':'gateway:JupiterQuote'})
    position = position_manager().heartbeat(wait_seconds=.5)
    heartbeats['POSITION_MANAGER'] = {'status': position['status'],
        'observed_at': position.get('observed_at') or now, 'source': 'gateway:PositionManager',
        'reason': position.get('reason'),
        'valid_until_monotonic': position.get('valid_until_monotonic', 0)}
    if _EXECUTION is not None:
        available = _EXECUTION.transport.available()
        capabilities.update(order_build=True, buy_transport=available, sell_transport=available,
                            fill_verification=True)
        heartbeats['EXECUTION_ADAPTER']['status'] = 'PASS' if available else 'UNKNOWN'
        heartbeats['RISK_ENGINE']['status'] = 'PASS'
    status = store.status()
    capabilities['wallet_binding'] = observed['status'] == 'PASS' and bool(status.get('wallet'))
    capabilities['restart_recovery'] = (observed['status'] == 'PASS'
        and status['reconciliation_required'] is False and bool(status.get('reconciliation_revision')))
    return {'heartbeats': heartbeats, 'capabilities': capabilities}


def execution_adapter():
    """Same gateway/ledger/Risk ownership; no HTTP caller can inject a quote."""
    global _EXECUTION
    with _EXECUTION_LOCK:
        store = ledger()
        if _EXECUTION is None or _EXECUTION.store is not store:
            risk = RiskEngine(store, clock=store.clock)
            if validation_contract(store):
                from execution_jupiter import JupiterRPCTransport,JupiterTransactionBuilder
                _EXECUTION = SingleSendAdapter(risk,execution_probes,contract_transport=JupiterRPCTransport(store),
                                               jupiter_builder=JupiterTransactionBuilder(store))
            else:
                _EXECUTION = SingleSendAdapter(risk, execution_probes,
                    contract_transport=SDKContractTransport(risk.gate_path))
        return _EXECUTION


def quote_event(event):
    """Existing broker reader, bound to reconciled inventory and this event.

    GMGN documents no route impact/liquidity fields in this response. Missing
    market evidence remains UNKNOWN and cannot become a zero-valued approval.
    """
    store = ledger()
    if validation_contract(store):
        global _VALIDATION_QUOTE, _JUPITER_PROBE
        cached=_VALIDATION_QUOTE;_VALIDATION_QUOTE=None
        if cached and cached['event_id']==event['event_id']:
            if age(cached['quoted_at'],store.clock())>store.cfg['quote_max_age_seconds']:
                raise StateError('jupiter_validation_candidate_quote_expired')
            _JUPITER_PROBE={'status':'PASS','observed_at':cached['quoted_at'],'source':'gateway:JupiterQuote'}
            return cached
        from execution_jupiter_read import JupiterExecutionReader
        quote=JupiterExecutionReader(store).quote(event)
        _JUPITER_PROBE={'status':'PASS','observed_at':quote['quoted_at'],'source':'gateway:JupiterQuote'}
        return quote
    with store.transaction() as c:
        status = store.status(connection=c)
        reconciled = store.meta(c, 'reconciled_at')
        if (status['reconciliation_required'] or not status['reconciliation_revision']
                or not reconciled or age(reconciled, store.clock()) > store.cfg['reconciliation_max_age_seconds']
                or not status['wallet']):
            raise StateError('quote_requires_current_wallet_reconciliation')
        wallet, balance = status['wallet'], raw(status['wallet_balance_lamports'])
        if event['side'] == 'BUY':
            amount = int(decimal(balance) * decimal(store.cfg['position_fraction']))
            input_token, output_token = SOL, event['mint']
        else:
            amount = raw(event['amount_raw'])
            if wallet != event['wallet']:
                raise StateError('quote_sell_wallet_mismatch')
            input_token, output_token = event['mint'], SOL
        if amount <= 0:
            raise StateError('quote_amount_invalid')
    observed = GMGNReader({'quote_max_latency_seconds': store.cfg['quote_max_age_seconds']}).quote(
        wallet, input_token, output_token, amount)
    return {'source': observed['source'], 'event_id': event['event_id'], 'wallet': wallet,
            'input_token': observed['input_token'], 'output_token': observed['output_token'],
            'input_raw': observed['input_amount'], 'minimum_output_raw': observed['min_output_amount'],
            'slippage_bps': str(decimal(observed['slippage']) * 100),
            'quoted_at': datetime.fromtimestamp(observed['requested_at'], timezone.utc).isoformat(),
            'price_impact_bps': None, 'price_deviation_bps': None, 'liquidity_usd': None}


def dispatch_next():
    """One existing runtime poll; callers never supply execution evidence."""
    global _QUOTE_RETRY_AT
    if not _DISPATCH_LOCK.acquire(blocking=False):
        return {'signal': None, 'execution': {'state': 'BUSY'}}
    from mykonos_meme_scout.jupiter_rpc_session import rpc_session
    scope=None
    try:
        scope=rpc_session(terminal_fault=ledger().block)
        scope.__enter__()
        try:
            event = next_event()  # Existing obligations/SELL always precede discovery.
        except StateError as error:
            if str(error) != 'position_manager_busy':
                raise
            # The existing owned PM loop is already handling obligations.
            # Do not fall through to discovery/BUY or count liveness as an API
            # failure; its real heartbeat remains UNKNOWN during this tick.
            return {'signal': None, 'execution': {'state': 'BUSY', 'reason': 'POSITION_MANAGER_BUSY'}}
        if event is None:
            return {'signal': None, 'execution': {'state': 'NO_READY_EVENT'}}
        if time.monotonic() < _QUOTE_RETRY_AT:
            return {'signal': event, 'execution': {'state': 'BLOCKED', 'reason': 'QUOTE_BACKOFF'}}
        try:
            quote = quote_event(event)
        except StateError:
            if validation_contract(ledger()):ledger().block('validation_inventory_or_route_unknown')
            return {'signal': event, 'execution': {'state': 'BLOCKED', 'reason': 'INVENTORY_UNVERIFIED'}}
        except GMGNReadError as error:
            evidence = error.evidence
            seconds = evidence['retry_after_seconds'] if evidence is not None else None
            # UNKNOWN is a latch, not a zero-second retry. The broker also
            # retains its shared cooldown across gateway/process restarts.
            _QUOTE_RETRY_AT = (float('inf') if seconds == -1 else time.monotonic() +
                max(ledger().cfg['bridge_rate_limit_seconds'], seconds or 0))
            if evidence is None or evidence['origin'] != 'local_cooldown':
                ledger().api_failure('execution_quote_unavailable')
            result = {'state': 'BLOCKED', 'reason': 'QUOTE_UNAVAILABLE'}
            if evidence is not None:
                result['read_evidence'] = dict(evidence)
            return {'signal': event, 'execution': result}
        except Exception:
            # The existing reader redacts provider errors, including 429. Use
            # conservative existing backoff for every unavailable quote; never
            # retry SEND or log the raw provider exception.
            _QUOTE_RETRY_AT = time.monotonic() + ledger().cfg['bridge_rate_limit_seconds']
            ledger().api_failure('execution_quote_unavailable')
            return {'signal': event, 'execution': {'state': 'BLOCKED', 'reason': 'QUOTE_UNAVAILABLE'}}
        return {'signal': event, 'execution': execution_adapter().execute(event, quote)}
    finally:
        try:
            if scope is not None:scope.__exit__(None,None,None)
        finally:_DISPATCH_LOCK.release()


def ack(data):
    store = ledger()
    evidence = None
    if data.get('status') in {'FILLED', 'PARTIAL'}:
        with store.transaction() as c:
            ev = store.get(c, 'events', data.get('event_id'))
        if ev:
            try:
                if ev.get('provider')=='JUPITER_V2':
                    from execution_jupiter_evidence import JupiterChainEvidence
                    result=JupiterChainEvidence(store.cfg).observe(ev)
                    if result['state']!='FILLED' or result['proof']['signature']!=data.get('signature'):
                        raise StateError('jupiter_receipt_not_finalized')
                    evidence=result['proof']
                else:
                    evidence = SolanaEvidence(store.cfg).fill(ev, data.get('signature'))
                    evidence.update(GMGNOrderEvidence(store.cfg).verify(ev, data.get('signature')))
            except Exception as exc:
                store.api_failure(type(exc).__name__)
                # The ledger preserves the obligation and blocks buys.
    return store.feedback(data, evidence)


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(load_config()['http_timeout_seconds'])

    def sendj(self, obj, code=200):
        body = json.dumps(obj, allow_nan=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        secret = os.environ.get('IMC_EXECUTION_TOKEN', '')
        supplied = self.headers.get('Authorization', '')
        return bool(secret) and hmac.compare_digest(supplied.encode('utf-8'), ('Bearer ' + secret).encode('utf-8'))

    def do_GET(self):
        try:
            if self.path == '/health':
                status = ledger().status()
                # The existing health endpoint exposes owned leases. It never
                # promotes a saved JSON report or supplies execution evidence.
                status['execution_health'] = execution_probes('BUY')
                self.sendj(status)
            elif self.path == '/next':
                self.sendj({'signal': next_event()})
            else:
                self.sendj({'error': 'not_found'}, 404)
        except (sqlite3.Error, StateError, ValueError, OSError):
            self.sendj({'error': 'state_unavailable', 'new_buys_enabled': False}, 503)

    def do_POST(self):
        if not self.authorized():
            # Closing a Windows socket with an unread request body can reset it
            # before the peer receives our 403. Drain only a bounded body; never
            # parse unauthenticated content or wait on arbitrary byte counts.
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if 0 < size <= load_config()['max_http_body_bytes'] and not self.headers.get('Transfer-Encoding'):
                    self.rfile.read(size)
            except (ValueError, TimeoutError, OSError):
                pass
            return self.sendj({'ok': False, 'error': 'execution_token_required'}, 403)
        try:
            size = int(self.headers.get('Content-Length', '0'))
            limit = ledger().cfg['max_http_body_bytes']
            if size > limit:
                # Bounded drain avoids a Windows TCP reset hiding the 413 for
                # a just-over-limit request. Arbitrarily large bodies remain
                # bounded and are never parsed or buffered in full.
                self.rfile.read(min(size, limit + 1))
                return self.sendj({'error': 'body_size'}, 413)
            if size <= 0:
                return self.sendj({'error': 'body_size'}, 413)
            if self.headers.get('Transfer-Encoding'):
                return self.sendj({'error': 'unsupported_transfer_encoding'}, 400)
            data = json.loads(self.rfile.read(size), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if not isinstance(data, dict):
                raise ValueError()
        except (ValueError, TimeoutError):
            return self.sendj({'error': 'bad_json'}, 400)
        try:
            if self.path == '/execute-next':
                if data:
                    return self.sendj({'ok': False, 'error': 'caller_execution_evidence_not_accepted'}, 400)
                return self.sendj(dispatch_next())
            elif self.path == '/ack':
                ok = ack(data)
            elif self.path == '/control':
                ok = ledger().control(data.get('action'))
            elif self.path == '/shadow':
                ledger().shadow(data.get('trade_id'), data.get('result', {}))
                ok = True
            elif self.path == '/bridge-error':
                ledger().api_failure('bridge_error')
                ok = True
            elif self.path == '/claim':
                # No HTTP switch can authorize real money movement.
                return self.sendj({'ok': False, 'error': 'live_execution_disabled'}, 409)
            elif self.path == '/reconcile':
                if data:
                    return self.sendj({'ok': False, 'error': 'caller_balances_not_accepted'}, 400)
                try:
                    ok = read_monitor().tick()
                except Exception:
                    return self.sendj({'ok': False, 'error': 'reconciliation_unavailable'}, 503)
            else:
                return self.sendj({'error': 'not_found'}, 404)
            self.sendj({'ok': ok}, 200 if ok else 409)
        except StateError as exc:
            self.sendj({'ok': False, 'error': str(exc)}, 409)
        except (sqlite3.Error, OSError, ValueError, TypeError):
            self.sendj({'ok': False, 'error': 'state_unavailable'}, 503)

    def log_message(self, fmt, *args):
        return


def main():
    store = ledger()
    store.recover()
    # SQLite is authoritative even if this status projection cannot be written.
    try:
        write_status(store)
    except OSError:
        pass
    print(f"IMC SIGNAL gateway {store.cfg['host']}:{store.cfg['port']} ledger={store.path}", flush=True)
    server = ThreadingHTTPServer((store.cfg['host'], store.cfg['port']), Handler)
    monitor = read_monitor()
    positions = position_manager()
    execution_adapter()
    monitor.start()
    positions.start()
    try:
        server.serve_forever()
    finally:
        try:
            positions.stop()
        finally:
            try:
                monitor.stop()
            finally:
                server.server_close()


if __name__ == '__main__':
    main()
