"""Public-file and native-namespace identities for a source-pin handover."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[1]


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows file identity pins')
@pytest.mark.parametrize('case', [
    'Normal', 'ReverseOrder', 'SourceFault', 'BindingFault',
    'NativeFileIds', 'Culture', 'Duplicate',
])
def test_native_source_pin_binding(case):
    result = subprocess.run([
        shutil.which('powershell.exe'), '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-File',
        str(ROOT / 'tests/gmgn_source_pin_binding_fixture.ps1'), '-Case', case,
    ], cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout.strip())
    assert proof['pass'] and proof['case'] == case
    assert proof['provider_calls'] == 0 and not proof['production_verified']
    for field, filename in [
        ('source_sha256', 'GmgnImmutableSources.cs'),
        ('binding_sha256', 'GmgnSourcePinBinding.cs'),
    ]:
        assert proof[field] == hashlib.sha256(
            (ROOT / 'TOOLS' / filename).read_bytes()).hexdigest()
