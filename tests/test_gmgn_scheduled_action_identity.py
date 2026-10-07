"""Scheduler instance binding through private native process read handles."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[1]
ERRORS = {
    'Normal': '',
    'CommandMismatch': 'GMGN_ACTION_COMMAND_MISMATCH',
    'SidMismatch': 'GMGN_ACTION_OWNER_MISMATCH',
    'PeerMismatch': 'GMGN_ACTION_PIPE_PEER_MISMATCH',
    'EarlyExit': 'GMGN_ACTION_ORIGINAL_ENDED',
    'InstanceSwitch': 'GMGN_ACTION_SCHEDULER_INSTANCE_CHANGED',
    'EngineSwitch': 'GMGN_ACTION_SCHEDULER_INSTANCE_CHANGED',
}


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows scheduler/process API')
@pytest.mark.parametrize('case', ERRORS)
def test_native_scheduled_action_identity(case):
    result = subprocess.run([
        shutil.which('powershell.exe'), '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-File',
        str(ROOT / 'tests/gmgn_task_action_identity_fixture.ps1'), '-Case', case,
    ], cwd=ROOT, capture_output=True, text=True, timeout=50)
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout.strip())
    assert proof['pass'] and proof['case'] == case and proof['code'] == ERRORS[case]
    assert proof['worker_native_exit'] == 0 and proof['provider_calls'] == 0
    assert not proof['pipe_verified'] and not proof['production_verified']
    assert proof['engine_pid'] == int(proof['worker_native'][0])
    assert proof['source_sha256'] == hashlib.sha256(
        (ROOT / 'TOOLS/GmgnScheduledActionIdentity.cs').read_bytes()).hexdigest()


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows original-process handles')
@pytest.mark.parametrize('case', ['Normal', 'BirthMismatch', 'SidMismatch', 'DirectoryMismatch', 'CallerMutation'])
def test_expected_peer_original_identity(case):
    result = subprocess.run([
        shutil.which('powershell.exe'), '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-File',
        str(ROOT / 'tests/gmgn_expected_peer_fixture.ps1'), '-Case', case,
    ], cwd=ROOT, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout.strip())
    assert proof['pass'] and proof['case'] == case
    assert proof['provider_calls'] == 0 and not proof['production_verified']
    assert proof['source_sha256'] == hashlib.sha256(
        (ROOT / 'TOOLS/GmgnScheduledActionIdentity.cs').read_bytes()).hexdigest()
