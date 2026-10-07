"""Actual Root task submits an OWN public request once; no retirement."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[1]


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows scheduler and process handles')
@pytest.mark.parametrize('case', [
    'Normal', 'Replay', 'FactoryReplay', 'ExistingRequest', 'Stale',
    'WrongHash', 'WrongMac', 'AuthFault', 'TaskFault', 'PostPublication', 'GrantBoundaries',
])
def test_original_root_single_retirement_submission(case):
    result = subprocess.run([
        shutil.which('powershell.exe'), '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-File',
        str(ROOT / 'tests/gmgn_warm_task_fixture.ps1'),
        '-Case', 'SourceTaskNormal', '-SenderCase', case,
    ], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'SYNTHETIC_PRIVATE_PUBLICATION' not in result.stdout + result.stderr
    proof = json.loads(result.stdout.strip())
    assert proof['pass'] and proof['native_cleanup_confirmed'] and proof['legacy_cleanup']
    assert proof['root_task_verified'] and proof['root_receipt_verified']
    sender = proof['sender_proof']
    assert sender['case'] == case and sender['retry_blocked']
    assert not sender['retirement_performed'] and not proof['sdk_granted']
    assert not proof['production_verified'] and proof['provider_calls'] == 0
    assert sender['request_published'] == (case in ['Normal', 'Replay', 'FactoryReplay', 'ExistingRequest', 'PostPublication', 'GrantBoundaries'])
    if case == 'PostPublication':
        assert sender['probe_reached'] and sender['state'] == 'SEND_UNKNOWN'
        assert sender['code'] == 'GMGN_ROOT_SENDER_OPERATION'
