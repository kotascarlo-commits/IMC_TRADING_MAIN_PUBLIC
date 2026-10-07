"""Each captured public IPC member and manifest has a protected native ACL."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[1]


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows file ACL')
@pytest.mark.parametrize('case', ['Normal', 'MemberAcl', 'ManifestAcl',
                                 'MemberBeforeCommit', 'ManifestBeforeCommit',
                                 'ManifestFinalBeforeCommit', 'SystemOwner'])
def test_capture_file_acl_boundary(case):
    result = subprocess.run([
        shutil.which('powershell.exe'), '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-File',
        str(ROOT / 'tests/gmgn_capture_acl_fixture.ps1'), '-Case', case,
    ], cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout.strip())
    assert proof['pass'] and proof['case'] == case
    assert proof['provider_calls'] == 0 and not proof['production_verified']
