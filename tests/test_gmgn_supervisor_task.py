"""Exact scheduler contract, without supervisor or provider admission."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[1]


@pytest.mark.skipif(os.name != 'nt', reason='Windows Task Scheduler COM')
def test_supervisor_task_roundtrip_and_tampering():
    result = subprocess.run([
        shutil.which('powershell.exe'), '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-File',
        str(ROOT / 'tests/gmgn_task_contract_fixture.ps1'),
    ], cwd=ROOT, capture_output=True, text=True, timeout=35)
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout.strip())
    assert proof['pass'] and proof['roundtrip'] and proof['collision_rejected']
    assert proof['tamper_cases'] == [
        'Action', 'Trigger', 'Principal', 'Settings', 'Substitution',
        'ExtraAction', 'ExtraTrigger', 'Schema', 'Restart',
    ]
    assert proof['provider_calls'] == 0 and not proof['production_verified']
    assert proof['helper_sha256'] == hashlib.sha256(
        (ROOT / 'TOOLS/GMGN-SupervisorTask.ps1').read_bytes()).hexdigest()
