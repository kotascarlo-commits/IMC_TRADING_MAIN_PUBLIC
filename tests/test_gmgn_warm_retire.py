"""Actual OWN Root send and Warm native retirement, with no SDK/broadcast."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[1]


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows Scheduler and process handles')
@pytest.mark.parametrize('case', [
    'Normal', 'Replay', 'PlanAbort', 'Expired', 'PinAbortCommon',
    'AdmissionAbortCommon', 'PinAbortCaptured', 'AdmissionAbortCaptured',
    'PinAbortCommonSecond', 'AdmissionAbortCommonSecond',
    'PinAbortCapturedSecond', 'AdmissionAbortCapturedSecond', 'ProbeGuards',
    'PinAbortAtomic', 'AdmissionAbortAtomic',
])
def test_original_warm_retirement(case):
    result = subprocess.run([
        shutil.which('powershell.exe'), '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-File',
        str(ROOT / 'tests/gmgn_warm_task_fixture.ps1'),
        '-Case', 'SourceTaskNormal', '-SenderCase', 'Normal', '-RetireCase', case,
    ], cwd=ROOT, capture_output=True, text=True, timeout=150)
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout.strip())
    assert proof['pass'] and proof['native_cleanup_confirmed'] and proof['legacy_cleanup']
    assert proof['root_receipt_verified'] and proof['root_task_verified']
    retire = proof['sender_proof']['retire_proof']
    assert retire['case'] == case and retire['retry_blocked']
    assert retire['original_broker_exit'] == retire['original_parent_exit'] == (case in ['Normal', 'Replay', 'ProbeGuards'])
    assert retire['sdk_factory_count'] == retire['provider_calls'] == 0
    assert not retire['production_verified'] and not proof['sdk_granted']
    if 'AbortCommon' in case or 'AbortCaptured' in case:
        assert retire['end_probe_reached'] and retire['state'] == 'FAULT'
    if case == 'ProbeGuards':
        assert retire['guards_verified']
    if case.endswith('AbortAtomic'):
        assert retire['atomic_probe_reached'] and retire['state'] == 'FAULT'
