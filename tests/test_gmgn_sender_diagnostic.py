"""Fixed sender diagnostics never retain raw exception payloads."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[1]


@pytest.mark.skipif(os.name != 'nt', reason='Windows PowerShell native contracts')
@pytest.mark.parametrize('case', [
    'Known', 'Wrapped', 'Suffix', 'InnerData', 'Depth7', 'Depth8', 'Null', 'Unknown',
])
def test_sender_fixed_diagnostic_boundary(case):
    result = subprocess.run([
        shutil.which('powershell.exe'), '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-File',
        str(ROOT / 'tests/gmgn_sender_diagnostic_fixture.ps1'), '-Case', case,
    ], cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'SYNTHETIC_PRIVATE_SENDER_DIAGNOSTIC' not in result.stdout + result.stderr
    proof = json.loads(result.stdout.strip())
    assert proof['pass'] and proof['case'] == case
    assert proof['provider_calls'] == 0 and not proof['production_verified']
