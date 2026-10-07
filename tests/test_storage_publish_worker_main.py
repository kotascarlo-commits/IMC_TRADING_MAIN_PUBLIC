"""Actual native worker.main integration; production GlobalMu is excluded."""
import os
from pathlib import Path
import subprocess
import sys
import pytest

ROOT=Path(__file__).parents[1]
pytestmark=pytest.mark.skipif(os.name!='nt',reason='Windows native worker main')

@pytest.mark.parametrize('role',['journal','rename'])
@pytest.mark.parametrize('case',['Normal','Mismatch','Expiry','Partial','ReadyWrite'])
def test_actual_main_native_boot_lifecycle_and_fault_retention(tmp_path,role,case):
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
        '-File',str(ROOT/'tests/storage_worker_main_fixture.ps1'),'-Workspace',str(tmp_path),
        '-Python',sys._base_executable,'-Role',role,'-Case',case],capture_output=True,timeout=55,
        creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode==0,(result.stdout+result.stderr).decode('utf-8',errors='replace')
    assert b'ACTUAL_WORKER_MAIN_LOCAL_MUTEX_PASS' in result.stdout


@pytest.mark.parametrize('role',['journal','rename'])
def test_actual_main_absent_shm_remains_absent_through_ack_and_finish(tmp_path,role):
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
        '-File',str(ROOT/'tests/storage_worker_main_fixture.ps1'),'-Workspace',str(tmp_path),
        '-Python',sys._base_executable,'-Role',role,'-Case','Normal','-AbsentSHM'],capture_output=True,timeout=55,
        creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode==0,(result.stdout+result.stderr).decode('utf-8',errors='replace')
    assert b'ACTUAL_WORKER_MAIN_LOCAL_MUTEX_PASS' in result.stdout
    assert not (tmp_path/'CORE/data/imc-main.db-shm').exists()
    assert not list((tmp_path/'.local-tools').glob('storage-publish-*/original/imc-main.db-shm'))
