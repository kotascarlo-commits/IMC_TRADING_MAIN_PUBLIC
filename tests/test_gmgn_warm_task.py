"""Actual Scheduler action with immutable plan; isolated before legacy and SDK."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[1]


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows Scheduler, process and DPAPI')
@pytest.mark.parametrize('case', ['Normal', 'WrongProof', 'ReplayChallenge', 'MissingChallenge', 'WrongAuth'])
def test_actual_warm_action(case):
    result = subprocess.run([
        shutil.which('powershell.exe'), '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-File',
        str(ROOT / 'tests/gmgn_warm_task_fixture.ps1'), '-Case', case,
    ], cwd=ROOT, capture_output=True, text=True, timeout=40)
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout.strip())
    assert proof['pass'] and proof['case'] == case and proof['native_cleanup_confirmed']
    assert proof['warm_action_verified'] == (case == 'Normal')
    assert proof['original_native_exit'] >= 0
    assert proof['provider_calls'] == 0 and not proof['legacy_touched']
    assert not proof['sdk_granted'] and not proof['production_verified']
