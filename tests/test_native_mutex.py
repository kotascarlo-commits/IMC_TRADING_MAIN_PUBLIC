"""Actual native identity on private fixture mutexes; no production names."""
import os
from pathlib import Path
import subprocess

import pytest


@pytest.mark.skipif(os.name!='nt',reason='Windows native mutant query')
@pytest.mark.parametrize('case',['free','owned','foreign','abandoned','closed'])
def test_native_mutex_owner_thread_identity_and_invalid_handle(case):
    command=['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',
             str(Path(__file__).with_name('native_mutex_fixture.ps1')),'-Case',case]
    result=subprocess.run(command,capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'native mutex identity PASS' in result.stdout
