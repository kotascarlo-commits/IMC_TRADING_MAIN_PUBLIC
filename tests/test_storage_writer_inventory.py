import os
from pathlib import Path
import subprocess
import sys
import pytest

ROOT=Path(__file__).parents[1]
pytestmark=pytest.mark.skipif(os.name!='nt',reason='native Windows full ancestor inventory')

@pytest.mark.parametrize('case',['Normal','WrongAncestor','MissingAncestor','ExtraAncestor',
    'WrongSentinel','ExtraInventory','ExistingHeader'])
def test_native_powershell_python_scope_inventory(tmp_path,case):
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
        '-File',str(ROOT/'tests/storage_writer_inventory_fixture.ps1'),'-Workspace',str(tmp_path),
        '-Python',sys._base_executable,'-Case',case],capture_output=True,timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode==0,(result.stdout+result.stderr).decode('utf-8',errors='replace')
    assert b'NATIVE_INVENTORY_ROUNDTRIP_PASS' in result.stdout
