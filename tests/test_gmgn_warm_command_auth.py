"""Warm commands require the native local store, even with valid legacy bait."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[1]


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows identity and DPAPI')
@pytest.mark.parametrize('case', [
    'Normal', 'WrongMac', 'MissingAuth', 'ForeignAuth', 'AuthFault', 'PlanFault',
])
def test_warm_command_authentication(case):
    result = subprocess.run([
        shutil.which('powershell.exe'), '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-File',
        str(ROOT / 'tests/gmgn_warm_command_auth_fixture.ps1'), '-Case', case,
    ], cwd=ROOT, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout.strip())
    assert proof['pass'] and proof['case'] == case
    assert proof['provider_calls'] == proof['process_actions'] == 0
    assert not proof['production_verified']
