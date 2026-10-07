"""Production lifecycle admission remains fail-closed before task integration."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[1]


@pytest.mark.skipif(os.name != 'nt', reason='PowerShell lifecycle commands')
def test_production_retire_requires_unimplemented_admission():
    result = subprocess.run([
        shutil.which('powershell.exe'), '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-File',
        str(ROOT / 'tests/gmgn_production_retire_block_fixture.ps1'),
    ], cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout.strip())
    assert proof['pass']
    assert proof['code'] == 'GMGN_STAGE_PRODUCTION_HANDOVER_NOT_ADMITTED'
    assert proof['provider_calls'] == proof['process_actions'] == 0
