"""Canonical fail-closed admission. No transport, keys, signer or sender imports."""
import argparse
import ast
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import uuid

ROOT = Path(__file__).resolve().parents[2]
EXPECTED = {
    'SYSTEM': 'IMC TRADING', 'EXECUTION_PROVIDER': 'JUPITER',
    'WALLET': 'OWN_SOLANA_WALLET', 'GMGN_EXECUTION': 'DISABLED',
    'GMGN_BROADCAST': 'DISABLED', 'GMGN_RUNTIME_DEPENDENCY': 'NONE',
    'LIVE_TRADING': False, 'BROADCAST_ENABLED': False, 'SIGNING_ENABLED': False,
    'OWNER_GATE': 'OFF', 'MODE': 'LIVE_PARITY_NO_BROADCAST',
    'PRIVATE_KEY_ACCESS': False, 'REAL_WALLET_MOVEMENT': False,
}


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('ambiguous_canonical_state')
        result[key] = value
    return result


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'), object_pairs_hook=unique)


def validate(state, execution, owner, worker_source):
    if any(not isinstance(value, dict) for value in (state, execution, owner)):
        raise ValueError('configuration_object_required')
    for key, value in EXPECTED.items():
        if type(state.get(key)) is not type(value) or state[key] != value:
            raise ValueError('canonical_state_rejected:' + key)
    for key in ('signing', 'broadcast_enabled', 'live_trading_enabled', 'wallet_access'):
        if execution.get(key) is not False:
            raise ValueError('real_authority_rejected:' + key)
    authority = execution.get('execution_authority_pct')
    if type(authority) is not int or authority != 0 or owner.get('OWNER_BROADCAST_GATE') != 'OFF':
        raise ValueError('real_authority_rejected')
    # Inspect the actual permitted transport before importing or calling it.
    # Old GMGN or key-verification launchers are never fallback candidates.
    tree = ast.parse(worker_source)
    if any(word in worker_source.lower() for word in
           ('gmgn', 'keypair', 'private_key', 'wallet_verify', 'sendtransaction',
            'sendrawtransaction', 'sign_transaction', 'cryptunprotectdata')):
        raise ValueError('active_execution_transport_rejected')
    methods = [node.value for node in ast.walk(tree) if isinstance(node, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == 'RPC_METHODS' for t in node.targets)]
    allowed = {'getAccountInfo', 'getMultipleAccounts', 'getLatestBlockhash',
               'getFeeForMessage', 'simulateTransaction', 'getBlockHeight'}
    if len(methods) != 1 or ast.literal_eval(methods[0]) != allowed:
        raise ValueError('read_only_rpc_allowlist_rejected')
    if state.get('EVIDENCE_STATUS') != 'BLOCKED' or not state.get('BLOCKERS'):
        # Editing a readiness label cannot create an accepted lifecycle runner.
        raise ValueError('live_parity_runtime_not_accepted')
    blockers = state['BLOCKERS']
    if (not isinstance(blockers, list) or any(not isinstance(blocker, dict)
            or any(not isinstance(blocker.get(key), str) or not blocker[key].strip()
                   for key in ('code', 'reason')) for blocker in blockers)):
        raise ValueError('blocker_objects_required')
    return state['BLOCKERS']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--duration-minutes', type=float, default=60)
    parser.add_argument('--virtual-capital-eur', type=float, default=1000)
    parser.add_argument('--target-closed-trades', type=int, default=3)
    args = parser.parse_args()
    if (not math.isfinite(args.duration_minutes) or args.duration_minutes <= 0
            or not math.isfinite(args.virtual_capital_eur) or args.virtual_capital_eur <= 0
            or args.target_closed_trades < 3):
        parser.error('positive duration/capital and at least three independent closures required')
    started = datetime.now(timezone.utc)
    run_id = 'live-parity-' + uuid.uuid4().hex
    directory = ROOT / 'RUNTIME/test-runs' / run_id
    directory.mkdir(parents=True)
    try:
        blockers = validate(read(ROOT / 'RUNTIME/current-system-state.json'),
            read(ROOT / 'config/imc-execution.json'), read(ROOT / 'config/owner-broadcast-gate.json'),
            (ROOT / 'TOOLS/imc_jupiter_prepare_worker.py').read_text(encoding='utf-8'))
        checks = 'PASS'
    except (ValueError, OSError, SyntaxError, TypeError) as error:
        blockers = [{'code': 'PREFLIGHT_REJECTED', 'reason': str(error)}]
        checks = 'FAIL'
    report = {
        'schema_version': 1, 'run_id': run_id, 'status': 'BLOCKED',
        'scope': 'PREFLIGHT_ONLY_NO_TRADING_RUNTIME_STARTED', 'preflight_safety': checks,
        'requested_duration_minutes': args.duration_minutes,
        'target_closed_trades': args.target_closed_trades,
        'started_at': started.isoformat(), 'runtime_seconds': 0,
        'start_capital_eur': args.virtual_capital_eur,
        'end_capital_eur': args.virtual_capital_eur, 'net_pnl_eur': 0,
        'BUY': 0, 'SELL': 0, 'CLOSED': 0, 'LIVE_PARITY_CLOSED': 0,
        'win_rate': None, 'profit_factor': None, 'max_drawdown': None,
        'security_coverage': None, 'holder_coverage': None, 'market_data_freshness': None,
        'jupiter_quote_success_rate': None, 'buy_simulation_success': None,
        'sell_simulation_success': None, 'average_slippage': None,
        'average_price_impact': None, 'total_estimated_fees_eur': 0,
        'reject_reasons': {}, 'blockers': blockers,
        'execution_provider': 'JUPITER', 'wallet_mode': 'Own Wallet / Read Only',
        'signing': False, 'broadcast': False, 'private_key_access': False,
        'real_money_moved': False, 'runtime_started': False,
        'reconciliation': 'NOT_APPLICABLE_NO_FILLS', 'LIVE_PARITY_READY': False,
    }
    text = 'BLOCKED — kein Handelslauf gestartet; BUY/SELL/CLOSED 0/0/0; Performance N/A.\n'
    text += '\n'.join(str(b.get('code', 'UNKNOWN')) + ': ' + str(b.get('reason', '')) for b in blockers)
    (directory / 'live-parity-report.json').write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
    (directory / 'live-parity-report.txt').write_text(text + '\n', encoding='utf-8')
    print('IMC LIVE-PARITY\nSystemstatus: BLOCKED\nBUY / SELL / CLOSED: 0 / 0 / 0\nPerformance: N/A')
    print('Report: ' + str(directory / 'live-parity-report.json'))
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
