"""Native local IPC role authentication; no provider configuration or calls."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[1]


@pytest.mark.skipif(os.name != 'nt', reason='Windows native ownership and DPAPI')
@pytest.mark.parametrize('case', [
    'Normal', 'WrongEnvironment', 'LegacyAbsent', 'Null', 'False', 'Empty',
    'ForeignType', 'WrongMac', 'Faulted', 'FactoryNull', 'FactoryWrongMac',
    'FactoryFaulted', 'RetireAuthFault', 'RetireNormal', 'RetireLateAuthFault',
    'RetireOwnerDeadline',
])
def test_opaque_legacy_role(case):
    result = subprocess.run([
        shutil.which('powershell.exe'), '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-File',
        str(ROOT / 'tests/gmgn_opaque_legacy_fixture.ps1'), '-Case', case,
    ], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout.strip())
    assert proof['pass'] and proof['case'] == case
    assert proof['native_job_cleanup_confirmed'] and proof['provider_calls'] == 0
    assert proof['retirement_performed'] == (case == 'RetireNormal')
    assert not proof['production_verified']
