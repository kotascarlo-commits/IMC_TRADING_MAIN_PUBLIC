"""Existing runtime poller and outbox projection; gateway owns every SEND."""
import json
import os
import subprocess
import time
import urllib.request
import urllib.error
from decimal import Decimal
from pathlib import Path

from execution_state import StateError, age, decimal, load_config, raw, timestamp
from mykonos_meme_scout.projection_io import write_json_projection

HERE = Path(__file__).resolve().parent
OUT = HERE / 'outbox'
LATEST = OUT / 'latest_signal.json'
JOURNAL = OUT / 'signal_journal.jsonl'
STATE = HERE / 'bridge_state.json'
FATAL_LOG = HERE / 'bridge_fatal.log'
PHASE = 'START'
SOL = 'So11111111111111111111111111111111111111112'
GMGN_SHIM = Path(__file__).resolve().parents[3] / 'TOOLS/GMGN_READ_SHIM/gmgn-cli.cmd'


def gateway():
    cfg = load_config()
    return f"http://{cfg['host']}:{cfg['port']}"


def get_json(url):
    try:
        with urllib.request.urlopen(url, timeout=load_config()['http_timeout_seconds']) as response:
            return json.loads(response.read(4 * 1024 * 1024))
    except urllib.error.HTTPError as error:
        # Only fixed local state errors; never copy arbitrary response text.
        try:
            code = json.loads(error.read(4096)).get('error')
            if code in {'position_manager_busy', 'position_manager_deadline', 'invalid_timestamp', 'state_unavailable'}:
                error.gateway_reason = code
        except (ValueError, TypeError, AttributeError, OSError):
            pass
        raise


def execute_next():
    token = os.environ.get('IMC_EXECUTION_TOKEN')
    if not token:
        raise StateError('execution_token_required')
    request = urllib.request.Request(gateway() + '/execute-next', data=b'{}', method='POST',
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
    return get_json(request)


def load_state():
    if not STATE.exists():
        return {'last_event_id': None, 'started_at': timestamp()}
    data = json.loads(STATE.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict) or 'last_event_id' not in data:
        raise StateError('corrupt_bridge_projection')
    return data


def atomic_json(path, obj):
    write_json_projection(path,obj)


def gmgn_sol_wallet():
    result = subprocess.run([str(GMGN_SHIM), 'portfolio', 'info', '--raw'],
                            stdin=subprocess.DEVNULL, capture_output=True, text=True,
                            timeout=load_config()['http_timeout_seconds'])
    if result.returncode:
        # CLI stderr may contain credentials or sensitive response data.
        raise StateError('gmgn_portfolio_failed')
    data = json.loads(result.stdout, parse_float=Decimal)
    wallets = [w for w in data.get('wallets', []) if w.get('chain') == 'sol']
    if len(wallets) != 1:
        raise StateError('gmgn_wallet_binding_ambiguous')
    wallet = wallets[0]
    balance = next((b.get('balance') for b in wallet.get('balances', []) if b.get('symbol') == 'SOL'), None)
    lamports = decimal(balance) * Decimal(1_000_000_000)
    if lamports != lamports.to_integral_value() or not wallet.get('address'):
        raise StateError('invalid_wallet_balance')
    return wallet['address'], int(lamports)


def translate(event, wallet_reader=gmgn_sol_wallet):
    cfg = load_config()
    if any(not isinstance(event.get(k), str) or not event[k] for k in ('event_id', 'trade_id', 'mint', 'side')):
        raise StateError('missing_event_identity')
    if event.get('state') != 'READY':
        raise StateError('event_not_ready')
    side = event['side']
    wallet, balance = wallet_reader()
    balance = raw(balance)
    if side == 'BUY':
        if age(event.get('signal_at'), timestamp()) > cfg['signal_max_age_seconds']:
            raise StateError('stale_signal')
        amount = int(Decimal(balance) * decimal(cfg['position_fraction']))
        if amount <= 0 or amount + raw(cfg['reserve_lamports']) > balance:
            raise StateError('capital_limit')
        order = {'wallet': wallet, 'input_token': SOL, 'output_token': event['mint'],
                 'amount_raw': str(amount), 'amount_lamports': str(amount),
                 'position_fraction': cfg['position_fraction'], 'amount_source': 'GMGN_WALLET_PROPOSAL'}
    elif side == 'SELL':
        amount = raw(event.get('amount_raw'))
        if amount <= 0 or wallet != event.get('wallet'):
            raise StateError('sell_position_wallet_or_amount_mismatch')
        order = {'wallet': wallet, 'input_token': event['mint'], 'output_token': SOL,
                 'amount_raw': str(amount), 'amount_source': 'CONFIRMED_BUY_POSITION'}
    else:
        raise StateError('unsupported_side')
    return {'schema': 'imc-gmgn-signal-v1', 'created_at': event['created_at'],
            'event_id': event['event_id'], 'trade_id': event['trade_id'], 'chain': 'sol',
            'side': side, 'mint': event['mint'], 'order': order, 'execution': False,
            'execution_state': 'READY_FOR_GMGN_EXECUTOR', 'source': 'IMC_SIGNAL_ONLY',
            'requires_fresh_quote_and_claim': True, 'exit_reason': event.get('exit_reason'), 'raw_signal': event}


def report_error(error):
    token = os.environ.get('IMC_EXECUTION_TOKEN')
    if not token:
        return
    req = urllib.request.Request(gateway() + '/bridge-error',
        data=json.dumps({'reason': type(error).__name__}).encode(),
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=load_config()['http_timeout_seconds']):
            pass
    except Exception:
        pass


def reconcile_obligations():
    token = os.environ.get('IMC_EXECUTION_TOKEN')
    if not token:
        return
    health = get_json(gateway() + '/health')
    # Passive signal-only mode requires no wallet queries for recovery. Real
    # obligations require periodic independent observations, also after a stop.
    if not any(e['state'] not in {'READY', 'REJECTED'} for e in health.get('events', [])):
        return
    request = urllib.request.Request(gateway() + '/reconcile', data=b'{}', method='POST',
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
    with urllib.request.urlopen(request, timeout=load_config()['reconciliation_max_age_seconds'] + 1):
        pass


def main():
    global PHASE
    PHASE = 'OUT_INIT'
    OUT.mkdir(parents=True, exist_ok=True)
    PHASE = 'STATE_LOAD'
    state = load_state()  # corruption is visible; never silently starts anew
    PHASE = 'CONFIG_LOAD'
    cfg = load_config()
    last_reconcile = 0
    while True:
        delay = cfg['bridge_poll_seconds']
        state['heartbeat_at'] = timestamp()
        try:
            if time.monotonic() - last_reconcile >= cfg['reconciliation_poll_seconds']:
                PHASE = 'RECONCILIATION'
                last_reconcile = time.monotonic()
                reconcile_obligations()
            PHASE = 'EXECUTION_POLL'
            outcome = execute_next()
            event = outcome.get('signal')
            execution = outcome.get('execution', {})
            state['execution_state'] = execution.get('state', 'UNKNOWN')
            # The ledger owns pending/dedupe. Outbox files are only projections.
            if (event and execution.get('state') in {'BLOCKED', 'NO_BROADCAST'}
                    and (event['event_id'] != state.get('last_event_id') or not LATEST.exists())):
                PHASE = 'TRANSLATE'
                packet = translate(event)
                PHASE = 'OUTBOX_WRITE'
                atomic_json(LATEST, packet)
                PHASE = 'JOURNAL_WRITE'
                with JOURNAL.open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(packet, separators=(',', ':')) + '\n')
                state.update(last_event_id=event['event_id'], last_seen_at=timestamp())
                PHASE = 'EVENT_STATE_WRITE'
                atomic_json(STATE, state)
                print(json.dumps({'state': 'GMGN_SIGNAL_READY', 'event_id': event['event_id'], 'execution': False}), flush=True)
            state['last_success_at'] = timestamp()
            state['last_error'] = None
            state['gateway_http_status'] = None
            state['gateway_reason'] = None
            state['state'] = 'PASSIVE_LOOP'
        except Exception as exc:
            state['last_error'] = type(exc).__name__
            state['error_phase'] = PHASE
            code = getattr(exc, 'code', None)
            state['gateway_http_status'] = code if type(code) is int and 100 <= code <= 599 else None
            state['gateway_reason'] = getattr(exc, 'gateway_reason', None)
            state['last_error_at'] = timestamp()
            state['state'] = 'READ_UNAVAILABLE'
            PHASE = 'ERROR_REPORT'
            report_error(exc)
            delay = cfg['bridge_rate_limit_seconds'] if getattr(exc, 'code', None) == 429 else cfg['bridge_error_seconds']
        PHASE = 'HEARTBEAT_WRITE'
        atomic_json(STATE, state)
        PHASE = 'WAIT'
        time.sleep(delay)


def run_main():
    try:
        main()
        return 0
    except Exception as error:
        # Independent append-only diagnostic. Never log message, traceback,
        # request, payload, environment or credentials; preserve nonzero exit.
        try:
            record = {'at': timestamp(), 'phase': PHASE, 'error_class': type(error).__name__}
            if isinstance(error, OSError):
                for key in ('errno', 'winerror'):
                    value = getattr(error, key, None)
                    if type(value) is int and 0 <= value <= 65535:
                        record[key] = value
            if not FATAL_LOG.exists() or FATAL_LOG.stat().st_size < 1048576:
                with FATAL_LOG.open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(record, separators=(',', ':')) + '\n')
        except Exception:
            pass  # Diagnostic failure cannot change the original exit outcome.
        return 1


if __name__ == '__main__':
    raise SystemExit(run_main())
