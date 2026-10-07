"""Actual OWN warm origin captures legacy IPC; no retirement or SDK factory."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[1]


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows scheduler and DPAPI')
@pytest.mark.parametrize('case', ['Normal', 'Replay', 'ExistingTarget', 'WrongMac',
                                 'ParentExit', 'AuthFault', 'RecoveryAcl', 'Concurrent'])
def test_warm_legacy_capture_admission(case):
    result = subprocess.run([
        shutil.which('powershell.exe'), '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-File',
        str(ROOT / 'tests/gmgn_warm_task_fixture.ps1'),
        '-Case', 'SourceTaskNormal', '-LegacyCase', case,
    ], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout.strip())
    assert proof['pass'] and proof['legacy_cleanup'] and proof['native_cleanup_confirmed']
    assert proof['root_receipt_verified'] and proof['root_task_verified']
    legacy = proof['legacy_proof']
    assert legacy['case'] == case and legacy['sdk_factory_count'] == 0
    assert legacy['admission_count'] == (0 if case == 'AuthFault' else 1)
    assert not legacy['retired'] and legacy['capture_committed'] == (case == 'Normal')
    assert not proof['sdk_granted'] and not proof['production_verified']
    if case == 'Concurrent':
        assert legacy['concurrent_reached'] and legacy['concurrent_joined']
        assert legacy['concurrent_code'] == 'GMGN_WARM_LEGACY_USED'
        assert legacy['code'] == 'GMGN_INSTALL_PLAN_FAULT'
        assert legacy['plan_state'] == 'FAULT'
