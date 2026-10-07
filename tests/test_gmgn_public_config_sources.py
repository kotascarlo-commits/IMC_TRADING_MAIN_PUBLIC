"""The source pinning allowlist never admits arbitrary JSON or other scopes."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[1]


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows source pins')
@pytest.mark.parametrize('case', ['Allowed', 'WrongScope', 'OtherConfig', 'Basename', 'InvalidScope'])
def test_public_config_source_allowlist(case):
    result = subprocess.run([
        shutil.which('powershell.exe'), '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-File',
        str(ROOT / 'tests/gmgn_public_config_source_fixture.ps1'), '-Case', case,
    ], cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout.strip())
    assert proof['pass'] and proof['case'] == case
    assert proof['provider_calls'] == 0 and not proof['production_verified']
    assert proof['source_sha256'] == hashlib.sha256(
        (ROOT / 'TOOLS/GmgnImmutableSources.cs').read_bytes()).hexdigest()
