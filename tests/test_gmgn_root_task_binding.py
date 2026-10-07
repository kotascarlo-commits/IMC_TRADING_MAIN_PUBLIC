"""Original scheduler objects and completed ROOT, with actual full S1 pins."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[1]


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows Scheduler/process/DPAPI')
@pytest.mark.parametrize('case', [
    'SourceTaskNormal', 'SourceTaskReplay', 'SourceTaskGuid', 'SourceTaskEngine',
    'SourceTaskPath', 'SourceTaskNotCom', 'SourceTaskDefinition', 'SourceTaskPlanFault',
    'SourceTaskAuthFault', 'SourceTaskSourceFault', 'SourceTaskChildFault',
    'SourceTaskNullCompletion', 'SourceTaskIncomplete', 'SourceTaskLateCheck', 'SourceTaskTransferFault',
    'SourceTaskWeakActor', 'SourceTaskActionMismatch', 'SourceTaskStrongSid',
    'SourceTaskStrongExe', 'SourceTaskStrongAbort', 'SourceTaskLateDefinition',
    'SourceTaskRegistryAbsent', 'SourceTaskRegistryLate',
])
def test_original_root_task_binding(case):
    result = subprocess.run([
        shutil.which('powershell.exe'), '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-File',
        str(ROOT / 'tests/gmgn_warm_task_fixture.ps1'), '-Case', case,
    ], cwd=ROOT, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout.strip())
    assert proof['pass'] and proof['case'] == case and proof['native_cleanup_confirmed']
    assert proof['root_receipt_verified'] and proof['source_handoff_verified']
    assert proof['root_task_verified'] == (case in (
        'SourceTaskNormal', 'SourceTaskReplay', 'SourceTaskLateCheck', 'SourceTaskWeakActor',
        'SourceTaskStrongSid', 'SourceTaskStrongExe', 'SourceTaskStrongAbort', 'SourceTaskLateDefinition',
        'SourceTaskRegistryLate'))
    assert proof['original_native_exit'] >= 0 and proof['provider_calls'] == 0
    assert proof['registry_failure_verified'] == (case == 'SourceTaskRegistryLate')
    assert not proof['legacy_touched'] and not proof['sdk_granted']
    assert not proof['production_verified']
