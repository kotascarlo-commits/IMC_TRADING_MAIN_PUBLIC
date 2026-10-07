"""Pure owner-proof contract checks confer no lifecycle authority."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[1]


@pytest.mark.skipif(os.name != 'nt', reason='Windows PowerShell')
@pytest.mark.parametrize('case', ['Normal', 'Fixture', 'Expired', 'Future',
                                 'Frequency', 'Schema', 'LoadedHash', 'Frame',
                                 'WrongRole', 'ExtraField', 'WrongType',
                                 'Exact15', 'Over15', 'ClockFuture'])
def test_owner_proof_body_contract(case):
    result = subprocess.run([
        shutil.which('powershell.exe'), '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-File',
        str(ROOT / 'tests/gmgn_owner_proof_fixture.ps1'), '-Case', case,
    ], cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout.strip())
    assert proof['pass'] and proof['case'] == case
    assert proof['process_actions'] == proof['provider_calls'] == 0
    assert not proof['production_verified']


@pytest.mark.skipif(os.name != 'nt', reason='Windows PowerShell')
def test_owner_clock_is_forwarded_by_existing_invoke():
    result = subprocess.run([
        shutil.which('powershell.exe'), '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-File',
        str(ROOT / 'tests/gmgn_owner_invoke_fixture.ps1'),
    ], cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout.strip())
    assert proof['pass'] and proof['retire_stub_calls'] == 1
    assert proof['process_actions'] == proof['provider_calls'] == 0
    assert not proof['production_verified']
