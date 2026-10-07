"""Explicit offline contract test entry point; never a forward trading runner."""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'CORE'))
from mykonos_meme_scout.cabal_security import authority_off


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--duration-minutes', type=float, required=True)
    parser.add_argument('--virtual-capital-eur', type=float, required=True)
    parser.add_argument('--target-closed-trades', type=int, required=True)
    args = parser.parse_args()
    authority_off()
    if (args.target_closed_trades != 3 or not math.isfinite(args.duration_minutes)
            or not math.isfinite(args.virtual_capital_eur)
            or args.duration_minutes <= 0 or args.virtual_capital_eur <= 0):
        raise ValueError('offline_contract_requires_three_cycles_and_positive_inputs')
    directory = ROOT / 'RUNTIME/test-runs' / ('cabal-contract-' + uuid.uuid4().hex)
    directory.mkdir(parents=True)
    started = time.time()
    xml = directory / 'tests.xml'
    tests = ['tests/test_cabal_security.py', 'tests/test_live_parity_virtual.py']
    try:
        result = subprocess.run([sys.executable, '-m', 'pytest', *tests, '-q',
            '--basetemp', str(directory/'fixtures'), '--junitxml', str(xml)],
            cwd=ROOT, capture_output=True, text=True, timeout=min(60, args.duration_minutes*60))
        (directory/'test-output.txt').write_text(result.stdout+result.stderr, encoding='utf-8')
        case_results = ET.parse(xml).getroot()
        passed = (result.returncode == 0 and len(case_results.findall('.//testcase')) > 0
            and not any(case_results.findall('.//' + kind) for kind in ('failure','error','skipped')))
        files = list((directory/'fixtures').rglob('three-cycle-proof.json'))
        evidence = json.loads(files[0].read_bytes()) if len(files) == 1 else {}
        passed = passed and evidence.get('native_fixture_closed') == 3 and evidence.get('LIVE_PARITY_CLOSED') == 0
        tests_total = len(case_results.findall('.//testcase'))
        error = None
    except (OSError, ValueError, subprocess.TimeoutExpired, ET.ParseError) as failure:
        passed, tests_total, evidence = False, 0, {}
        error = type(failure).__name__
    authority_off()
    report = {'status': 'OFFLINE_CONTRACT_PASS' if passed else 'OFFLINE_CONTRACT_FAIL',
        'scope': 'OFFLINE_SYNTHETIC_FIXTURES_NOT_JUPITER_FORWARD_OR_PERFORMANCE',
        'started_at': datetime.fromtimestamp(started,timezone.utc).isoformat(),
        'runtime_seconds': time.time()-started, 'requested_virtual_capital_eur': args.virtual_capital_eur,
        'start_capital_eur': None, 'end_capital_eur': None, 'net_pnl_eur': None,
        'win_rate': None, 'profit_factor': None, 'max_drawdown': None,
        'native_fixture_closed': 3 if passed else 0, 'LIVE_PARITY_CLOSED': 0,
        'forward_buy': 0, 'forward_sell': 0, 'forward_closed': 0,
        'native_fixture_buy': 3 if passed else 0, 'native_fixture_sell': 3 if passed else 0,
        'tests_passed': tests_total if passed else 0, 'tests_total': tests_total,
        'creator_security_source': 'CABAL_HUNTER_VIRTUAL_CONTRACT_SYNTHETIC_UNIT_FIXTURES',
        'real_creator_security_coverage': None, 'real_holder_coverage': None,
        'jupiter_forward_quote_success': None, 'reconciliation': 'NATIVE_FIXTURE_PASS' if passed else 'UNKNOWN',
        'buy_simulation_success': None, 'sell_simulation_success': None,
        'market_data_freshness': 'NOT_MEASURED_SYNTHETIC_FIXTURES',
        'average_slippage': None, 'average_price_impact': None, 'total_estimated_fees_eur': None,
        'native_fixture_network_fees_lamports': sum(c['network_fees_lamports'] for c in evidence.get('cycles',[])) if passed else None,
        'LIVE_PARITY_READY': False, 'signing': False, 'broadcast': False,
        'private_key_access': False, 'real_money_moved': False, 'error': error}
    (directory/'live-parity-report.json').write_bytes(json.dumps(report,indent=2).encode('utf-8')+b'\n')
    print('IMC OFFLINE CONTRACT TEST\nStatus: ' + report['status'])
    print('Native Fixture CLOSED: ' + str(report['native_fixture_closed']) + ' | LIVE_PARITY_CLOSED: 0')
    print('Report: ' + str(directory/'live-parity-report.json'))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
