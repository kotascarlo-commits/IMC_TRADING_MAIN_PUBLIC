"""Native diagnostic projection does not use the trading CLI or open a database."""
from pathlib import Path
import json
import subprocess
import sys

import pytest


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows native process identity')
def test_diagnostic_status_projection(tmp_path):
    result = subprocess.run(
        ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
         str(Path(__file__).with_name('terminal_shadow_fixture.ps1')), '-WorkDir', str(tmp_path)],
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
    assert result.returncode == 0, result.stderr or result.stdout
    report = json.loads(result.stdout.strip())
    assert report['ok'] and report['checks'] >= 45
