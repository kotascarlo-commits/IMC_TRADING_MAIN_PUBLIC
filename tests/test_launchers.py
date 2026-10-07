"""Run Windows process-identity fixtures without executing any service launcher."""
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="Windows command-line semantics")
def test_launcher_process_identity():
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(Path(__file__).with_name("test_launcher_processes.ps1"))],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "process identity checks passed" in result.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="Windows launchers")
def test_python_environment_preflight():
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(Path(__file__).with_name("test_python_launchers.ps1"))],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Python preflight checks passed" in result.stdout
