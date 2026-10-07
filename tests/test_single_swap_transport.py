"""Installed SDK, generated test key, fake fetch only; never loads SDK config."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / 'RUNTIME/EXECUTOR_GATEWAY/GMGN_BRIDGE'))
from execution_adapter import build_swap_payload, SOL, unique_pairs
from execution_state import canonical

SDK = Path('%USERPROFILE%/AppData/Roaming/npm/node_modules/gmgn-cli/dist')
TOKEN = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'


def payload(side='BUY', bps=100):
    event = {'state': 'READY', 'side': side, 'mint': TOKEN, 'event_id': 'isolated-event',
             'wallet': SOL, 'amount_raw': '1000000'}
    quote = {'source': 'GMGN_ORDER_QUOTE', 'event_id': event['event_id'], 'wallet': SOL,
             'input_token': SOL if side == 'BUY' else TOKEN,
             'output_token': TOKEN if side == 'BUY' else SOL,
             'input_raw': '1000000', 'minimum_output_raw': '900000', 'slippage_bps': bps}
    return canonical(build_swap_payload(event, quote,
                                       {'max_slippage_bps': 150, 'max_order_fee_lamports': '3000000'}))


def run(scenario, body):
    result = subprocess.run(['node', str(Path(__file__).with_name('gmgn_single_swap_harness.mjs')),
                             scenario, str(SDK)], input=body, text=True, encoding='utf-8',
                            capture_output=True, timeout=8)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'synthetic SDK boundary passed' in result.stdout
    assert 'NEVER_OUTPUT_SYNTHETIC_SECRET' not in result.stdout + result.stderr
    assert 'PRIVATE KEY' not in result.stdout + result.stderr
    assert 'X-Signature' not in result.stdout + result.stderr
    if scenario in ('pythonContract', 'pythonContractLateAck'):
        replies = [line.removeprefix('SDK_RESULT ') for line in result.stdout.splitlines()
                   if line.startswith('SDK_RESULT ')]
        assert len(replies) == 1
        return json.loads(replies[0], object_pairs_hook=unique_pairs)


@pytest.mark.parametrize('scenario', [
    'success', 'parallel', 'mutation', 'gateOffAfterSend', 'off', 'invalidGate', 'missingAutomation',
    'expired', 'lateGate', 'wallRollback', 'monoRollback', 'wrongHost', 'missingConfirmation',
    'incompatibleBytes', 'invalidPayload', 'badHash', 'http429', 'http500', 'http503', 'redirect',
    'empty', 'invalid', 'html', 'duplicateCode', 'duplicateOrder', 'escapedDuplicate', 'nestedDuplicate',
    'boolCode', 'stringCode', 'apiError', 'noOrder', 'badOrder', 'longOrder', 'nonfinite', 'deep',
    'oversize', 'invalidUtf8', 'network', 'wallExpiredAfterSend', 'monoExpiredAfterSend',
    'timeoutFetch', 'timeoutStream',
    'wrongQuery', 'extraHeader', 'wrongBody', 'wrongRoute', 'wrongMethod', 'sdkDoubleSend',
    'signatureError', 'confirmationError',
    'lateAck', 'lateAckGateOff', 'lateSend', 'responseWallExpired', 'responseMonoExpired',
    'responseBudgetAtIntake', 'wallRollbackAboveStart', 'responseWindowShort', 'responseWindowLong',
    'responseWindowFloat', 'responseWindowBool', 'responseWindowUnsafe', 'responseWindowExtra',
    'responseWindowUndefined', 'realLateAck', 'realLateSend', 'realResponseTimeout',
    'sendWallBoundary', 'sendMonoBoundary',
])
def test_single_swap_sdk_boundary(scenario):
    run(scenario, payload())


@pytest.mark.parametrize('side,bps', [('BUY', 0), ('BUY', 75), ('BUY', 100), ('BUY', 150),
                                    ('SELL', 25), ('SELL', 100), ('SELL', 150)])
def test_actual_python_payload_bytes_survive_sdk_signature_boundary(side, bps):
    body = payload(side, bps)
    run('success', body)
    if bps in (0, 100):
        assert type(json.loads(body)['slippage']) is int
