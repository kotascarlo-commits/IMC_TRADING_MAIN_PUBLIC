"""Native public-proof retention confers no process or SDK authority."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[1]


@pytest.mark.skipif(os.name != 'nt', reason='Windows native proof handles')
@pytest.mark.parametrize('case', [
    'Normal', 'Expired', 'Future', 'Frequency', 'WrongType', 'LoadedHash',
    'Frame', 'DuplicateBody', 'EscapedDuplicateBody', 'DuplicateEnvelope',
    'EscapedDuplicateEnvelope', 'BadNumber', 'NestedBody', 'BadEscape',
    'Trailing', 'EnvelopeType', 'NullBody', 'WrongMac', 'WrongHash', 'Zero',
    'Oversize', 'HardLink', 'InvalidUtf8', 'Abort', 'SourceAbort', 'AuthAbort',
    'EmptyMac', 'ShortMac', 'NonHexMac', 'UpperMac', 'ExpiresAfterBinding',
    'Reparse',
    'ExpiresDuringValidation', 'NamespaceAbort', 'AuthKeyChanged', 'ExpiresDuringPublicCheck',
    'ClockSnapshot', 'ClockSnapshotSourceFault', 'ClockSnapshotAuthFault', 'ClockSnapshotExpired',
])
def test_public_owner_proof_native_pin(case):
    result = subprocess.run([
        shutil.which('powershell.exe'), '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-File',
        str(ROOT / 'tests/gmgn_owner_pin_fixture.ps1'), '-Case', case,
    ], cwd=ROOT, capture_output=True, text=True, timeout=35)
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout.strip())
    assert proof['pass'] and proof['case'] == case
    assert proof['process_actions'] == proof['provider_calls'] == 0
    assert not proof['production_verified']
    assert proof['retained_write'] and proof['retained_delete']
    assert proof['retained_rename']
    if case in ['ExpiresAfterBinding', 'ExpiresDuringValidation', 'ExpiresDuringPublicCheck', 'ClockSnapshotExpired']:
        assert proof['bound'] and proof['expiry_step']
    if case in ['Normal', 'Abort', 'SourceAbort', 'AuthAbort']:
        assert proof['write_blocked']
