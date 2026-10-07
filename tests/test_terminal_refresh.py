"""Isolated display fixtures: never create/stop a trading run or open its DB."""
import json
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows PowerShell console')
@pytest.mark.parametrize('mode', ['async', 'layout', 'native'])
def test_terminal_refresh(mode, tmp_path):
    args = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
            str(Path(__file__).with_name('terminal_refresh_fixture.ps1')),
            '-Mode', mode, '-WorkDir', str(tmp_path)]
    if mode == 'native':
        startup = subprocess.STARTUPINFO()
        startup.dwFlags = subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        result = subprocess.run(args, startupinfo=startup,
                                creationflags=subprocess.CREATE_NEW_CONSOLE, timeout=20)
    else:
        result = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, encoding='utf-8', errors='replace', timeout=20)
    report = tmp_path / (mode + '.json')
    detail = json.loads(report.read_text(encoding='utf-8-sig')) if report.exists() else {}
    assert result.returncode == 0 and detail.get('ok'), detail or getattr(result, 'stderr', '')
    assert detail['checks']
