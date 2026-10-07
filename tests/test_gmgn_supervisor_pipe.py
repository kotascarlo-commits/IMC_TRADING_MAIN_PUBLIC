"""Real kernel pipe/process boundaries, offline disposable scheduler tasks."""
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
    'NameCollision': 'GMGN_PIPE_NATIVE_OPEN',
    'ConnectTimeout': 'GMGN_PIPE_DEADLINE',
    'ReadTimeout': 'GMGN_PIPE_DEADLINE',
    'WriteTimeout': 'GMGN_PIPE_DEADLINE',
    'Disconnect': 'GMGN_PIPE_DISCONNECTED',
    'Oversize': 'GMGN_PIPE_MESSAGE_SIZE',
    'WrongPeer': 'GMGN_ACTION_PIPE_PEER_MISMATCH',
    'ChildSelfFault': 'GMGN_PIPE_DISCONNECTED',
}


@pytest.mark.skipif(os.name != 'nt', reason='Native local Windows pipes and scheduler')
@pytest.mark.parametrize('case', ERRORS)
def test_supervisor_pipe_native_boundaries(case):
    result = subprocess.run([
        shutil.which('powershell.exe'), '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-File',
        str(ROOT / 'tests/gmgn_supervisor_pipe_fixture.ps1'), '-Case', case,
    ], cwd=ROOT, capture_output=True, text=True, timeout=65)
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout.strip())
    assert proof['pass'] and proof['case'] == case and proof['code'] == ERRORS[case]
    assert proof['provider_calls'] == 0
    assert not proof['production_verified']
    assert not proof['authenticated'] and not proof['pin_transfer_verified']
    assert proof['pipe_sha256'] == hashlib.sha256(
        (ROOT / 'TOOLS/GmgnSupervisorPipe.cs').read_bytes()).hexdigest()


@pytest.mark.skipif(os.name != 'nt', reason='Native pipe resource ownership')
@pytest.mark.parametrize('case', ['Normal', 'CancelFault', 'CloseFault', 'ProductionProbeRejected'])
def test_pipe_cleanup_boundary(case):
    result = subprocess.run([
        shutil.which('powershell.exe'), '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-File',
        str(ROOT / 'tests/gmgn_pipe_cleanup_fixture.ps1'), '-Case', case,
    ], cwd=ROOT, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout.strip())
    assert proof['pass'] and proof['case'] == case
    assert proof['original_retained'] == (case != 'Normal')
    if case == 'Normal':
        assert proof['operations_before_dispose'] >= 3
        assert proof['buffers_before_dispose'] >= 2
        assert proof['pending_before_dispose'] == 0
    elif case == 'CancelFault':
        assert proof['operations_before_dispose'] == 1
        assert proof['pending_before_dispose'] == 1
    assert not proof['native_failure_verified']
    assert proof['provider_calls'] == 0 and not proof['production_verified']
    assert proof['pipe_sha256'] == hashlib.sha256(
        (ROOT / 'TOOLS/GmgnSupervisorPipe.cs').read_bytes()).hexdigest()
