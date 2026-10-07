"""CIM expiration requires positive native OS evidence; live unknowns still block."""
import json
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows native lifetime')
def test_snapshot_lifetime():
    result = subprocess.run(
        ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
         str(Path(__file__).with_name('snapshot_lifetime_fixture.ps1'))],
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
    assert result.returncode == 0, result.stderr or result.stdout
    report = json.loads(result.stdout)
    assert report['ok'] and report['checks'] >= 20
