"""No raw COM/PowerShell diagnostic can escape the root definition boundary."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[1]


@pytest.mark.skipif(os.name != 'nt', reason='Windows PowerShell compiled native contracts')
@pytest.mark.parametrize('case', ['Known', 'WrappedKnown', 'Unknown', 'PrefixSuffix',
                                 'KnownWithInner', 'Null', 'DeepLimit'])
def test_root_definition_diagnostic_boundary(case):
    result = subprocess.run([
        shutil.which('powershell.exe'), '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-File',
        str(ROOT / 'tests/gmgn_root_diagnostic_fixture.ps1'), '-Case', case,
    ], cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'SYNTHETIC_PRIVATE_DIAGNOSTIC' not in result.stdout + result.stderr
    proof = json.loads(result.stdout.strip())
    assert proof['pass'] and proof['case'] == case and not proof['inner_retained']
    assert proof['provider_calls'] == 0 and not proof['production_verified']
