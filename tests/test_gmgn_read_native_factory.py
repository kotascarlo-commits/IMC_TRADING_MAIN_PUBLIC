"""Real Windows process lifecycle against fake, local Node fixtures only."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[1]
CASES = (
    'Normal', 'Mutation', 'DuplicateOption', 'AmountOverflow', 'PrivateTicket',
    'CallerMutation', 'StartFailure', 'Aborted', 'ParentExit',
    'DelayedParentExit', 'WrongParentBirth', 'ConcurrentDispose',
    'AfterCreateFailure', 'FilteredEnvironment', 'StreamDisposeFailure',
    'GenerationRead', 'GenerationNull', 'GenerationSame', 'GenerationTicketFault',
)


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows job/process contracts')
@pytest.mark.parametrize('case', CASES)
def test_native_read_process_contract(case):
    shell = shutil.which('powershell.exe')
    if not shell or not Path(r'C:\Program Files\nodejs\node.exe').is_file():
        pytest.skip('PowerShell/approved Node executable unavailable')
    result = subprocess.run([
        shell, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
        '-File', str(ROOT / 'tests/gmgn_read_factory_fixture.ps1'), '-Case', case,
    ], cwd=ROOT, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=40)
    assert result.returncode == 0, result.stdout + result.stderr
    proofs = [json.loads(line) for line in result.stdout.splitlines()
              if line.startswith('{')]
    assert len(proofs) == 1
    proof = proofs[0]
    assert proof['case'] == case and proof['pass']
    assert proof['native_job_cleanup_confirmed']
    assert proof['provider_calls'] == 0
    assert not proof['production_verified']
    assert proof['factory_sha256'] == hashlib.sha256(
        (ROOT / 'TOOLS/GmgnReadProcessLifetime.cs').read_bytes()).hexdigest()
    if case == 'StartFailure':
        assert proof['native_error'] == 193 and not proof['started']
    if case == 'StreamDisposeFailure':
        assert proof['code'] == 'GMGN_READ_NATIVE_TICKET_CLEANUP'
        assert proof['state'] == ['FAULT', 'True', '0', '0']
