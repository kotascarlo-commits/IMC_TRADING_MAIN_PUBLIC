"""Held public requests have no Owner-proof or retirement authority."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[1]


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows public request')
@pytest.mark.parametrize('case', [
    'Normal', 'Duplicate', 'EnvelopeDuplicate', 'Type', 'Noncanonical',
    'Whitespace', 'Mac', 'WrongMac', 'Hash', 'InitialAcl', 'LateAcl',
    'RecoveryAbort', 'Abort', 'PublicBypass',
])
def test_readonly_retirement_request(case):
    result = subprocess.run([
        shutil.which('powershell.exe'), '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-File',
        str(ROOT / 'tests/gmgn_retire_request_fixture.ps1'), '-Case', case,
    ], cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout.strip())
    assert proof['pass'] and proof['case'] == case
    assert proof['process_actions'] == proof['provider_calls'] == 0
    assert not proof['production_verified']
    if case not in ['InitialAcl', 'PublicBypass']:
        assert proof['retained_write']
        assert proof['retained_delete'] and proof['retained_rename']
