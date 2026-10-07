"""Admission checkpoints preserve error priority and shared-pin fault ownership."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[1]


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows file and process identity')
@pytest.mark.parametrize('case', [
    'Normal', 'BadManifest', 'UnpinnedManifest', 'Generation', 'Schema',
    'FileDigest', 'DirectoryDigest', 'Self0', 'Self1', 'Self2', 'Self3',
    'NullSelf', 'ShortSelf', 'NullDigests', 'ShortDigests', 'SourceAbort',
    'PinAbort', 'NativeFileAfterCheck', 'NativeDirectoryAfterCheck',
    'ClaimNormal', 'ClaimManifest', 'ClaimGeneration', 'ClaimSchema', 'ClaimFiles',
    'ClaimDirectories', 'ClaimNull', 'ClaimShort', 'ClaimAndNative',
])
def test_source_admission_checkpoint(case):
    result = subprocess.run([
        shutil.which('powershell.exe'), '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-File',
        str(ROOT / 'tests/gmgn_source_admission_checkpoint_fixture.ps1'), '-Case', case,
    ], cwd=ROOT, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout.strip())
    assert proof['pass'] and proof['case'] == case
    assert proof['provider_calls'] == proof['process_actions'] == 0
    assert not proof['production_verified']
