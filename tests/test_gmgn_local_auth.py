"""Isolated IMC IPC DPAPI tests; no provider keys or real auth store."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[1]


@pytest.mark.skipif(os.name != 'nt', reason='Windows owner DPAPI and native file ACLs')
@pytest.mark.parametrize('case', [
    'Normal', 'WrongHash', 'WrongFileId', 'WrongDirectoryId', 'Missing',
    'InvalidKey', 'CreateCollision', 'BodyLimit', 'MacMismatch', 'Abort',
    'BodyBoundary', 'MultibyteLimit', 'UnpairedSurrogate', 'EmptyBody', 'EntropyVector',
    'NullBody',
])
def test_local_ipc_auth_store(case):
    result = subprocess.run([
        shutil.which('powershell.exe'), '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-File',
        str(ROOT / 'tests/gmgn_local_auth_fixture.ps1'), '-Case', case,
    ], cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout.strip())
    assert proof['pass'] and proof['case'] == case
    assert proof['provider_calls'] == 0 and not proof['production_verified']
    assert proof['source_sha256'] == hashlib.sha256(
        (ROOT / 'TOOLS/GmgnReadLocalAuth.cs').read_bytes()).hexdigest()


@pytest.mark.skipif(os.name != 'nt', reason='Owner InteractiveToken task DPAPI')
def test_local_ipc_auth_reopens_in_owner_scheduler_host():
    result = subprocess.run([
        shutil.which('powershell.exe'), '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-File',
        str(ROOT / 'tests/gmgn_local_auth_task_fixture.ps1'),
    ], cwd=ROOT, capture_output=True, text=True, timeout=35)
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout.strip())
    assert proof['pass'] and proof['owner_task_dpapi_verified']
    assert proof['task_deleted'] and proof['child_native_exit'] == 0
    assert proof['provider_calls'] == 0 and not proof['production_verified']


@pytest.mark.skipif(os.name != 'nt', reason='Windows file sharing and native close boundary')
@pytest.mark.parametrize('case', [
    'WindowMutation', 'WindowWriter', 'AclMismatch', 'WriterCloseFault',
    'OverlapCloseFault', 'ProductionProbeRejected',
])
def test_local_ipc_auth_creation_boundaries(case):
    result = subprocess.run([
        shutil.which('powershell.exe'), '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-File',
        str(ROOT / 'tests/gmgn_local_auth_boundary_fixture.ps1'), '-Case', case,
    ], cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout.strip())
    assert proof['pass'] and proof['case'] == case and not proof['authority_returned']
    assert proof['provider_calls'] == 0 and not proof['production_verified']
    if case in ('WriterCloseFault', 'OverlapCloseFault'):
        assert proof['uncertain_closes'] == 1
        assert proof['injected_native_failure']
