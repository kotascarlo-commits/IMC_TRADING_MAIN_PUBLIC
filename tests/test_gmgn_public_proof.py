"""Public proof publication and independent READ/shareREAD consumers."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[1]


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows sharing and ACLs')
@pytest.mark.parametrize('case', [
    'Normal', 'RequestName', 'ProbeForbidden', 'ProbeSwitch', 'ProbeNullSnapshot', 'SizeLimit', 'NameMismatch', 'Collision', 'WriteBlocked',
    'DirectoryAclMismatch', 'FileAclMismatch', 'DirectoryCollision',
])
def test_public_proof_pin_boundaries(case):
    result = subprocess.run([
        shutil.which('powershell.exe'), '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-File',
        str(ROOT / 'tests/gmgn_public_proof_fixture.ps1'), '-Case', case,
    ], cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout.strip())
    assert proof['pass'] and proof['case'] == case
    assert proof['provider_calls'] == 0 and not proof['production_verified']
    assert proof['source_sha256'] == hashlib.sha256(
        (ROOT / 'TOOLS/GmgnReadProofPin.cs').read_bytes()).hexdigest()
