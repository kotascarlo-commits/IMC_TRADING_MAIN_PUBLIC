"""Real local pipe/owned Job fixtures; no publication or runtime authority."""
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
import ctypes

import pytest

ROOT=Path(__file__).parents[1]
pytestmark=pytest.mark.skipif(os.name!='nt',reason='native x64 overlapped Windows pipes')


@pytest.mark.parametrize('case',['concurrent','reentrant'])
def test_client_fault_registration_survives_final_native_close(tmp_path,case):
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
        '-File',str(ROOT/'tests/storage_client_final_fault_fixture.ps1'),'-Workspace',str(tmp_path),
        '-Python',sys._base_executable,'-Case',case],capture_output=True,text=True,timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'NATIVE_CLIENT_FINAL_FAULT_PASS' in result.stdout


@pytest.mark.parametrize('case',['FastExit','NonzeroExit','UnknownCompletion','PartialWrite','Replay'])
def test_terminal_write_confirms_native_completion_without_losing_fast_exit(tmp_path,case):
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
        '-File',str(ROOT/'tests/storage_terminal_channel_fixture.ps1'),'-Workspace',str(tmp_path),'-Case',case],
        capture_output=True,text=True,timeout=30,creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'NATIVE_TERMINAL_CHANNEL_FIXTURE_PASS' in result.stdout


@pytest.mark.parametrize('case',['Normal','Early','WrongPid','WrongBirth','Collision',
    'ConnectTimeout','ReadTimeout','PeerExit','Oversize','Empty','Concurrent',
    'CloseFault','CloseException','WriteTimeout','PartialWrite','ZeroRead','WaitFailed','IOIncomplete',
    'CloseAfterDeadline','ConcurrentClose','PythonNormal','PythonReadTimeout','PythonWrongPid','PythonWrongBirth',
    'PythonHeapFreeFalse','PythonPartialWrite','PythonCloseFalse','PythonCloseException','PythonConcurrentClose',
    'PythonUntil','PythonUntilReadTimeout','Until','UntilReadTimeout'])
def test_native_channel_identity_completion_framing_and_retention(tmp_path,case):
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive',
        '-ExecutionPolicy','Bypass','-File',str(ROOT/'tests/storage_channel_fixture.ps1'),
        '-Workspace',str(tmp_path),'-Python',sys._base_executable,'-Case',case],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    marker=('NATIVE_PYTHON_CLIENT_PASS' if case.startswith('Python') else
            'NATIVE_CHANNEL_PAIR_PASS' if case in ('Normal','Early','IOIncomplete','Until') else
            'COLLISION_REJECTED' if case=='Collision' else 'NATIVE_CHANNEL_FAULT_RETAINED')
    assert marker in result.stdout


def test_python_checked_cleanup_after_confirmed_io_and_actual_peer_exit(tmp_path):
    sys.path.insert(0,str(ROOT/'scripts'))
    from storage_publish_channel import Client,_FileTime,_RETAINED
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    api.OpenProcess.argtypes=[ctypes.c_uint32,ctypes.c_int,ctypes.c_uint32]
    api.OpenProcess.restype=ctypes.c_void_p
    api.GetProcessTimes.argtypes=[ctypes.c_void_p]+[ctypes.POINTER(_FileTime)]*4
    api.GetProcessTimes.restype=ctypes.c_int
    api.CloseHandle.argtypes=[ctypes.c_void_p];api.CloseHandle.restype=ctypes.c_int
    def birth(pid):
        handle=api.OpenProcess(0x1000,False,pid);assert handle
        try:
            values=[_FileTime() for _ in range(4)]
            assert api.GetProcessTimes(handle,*(ctypes.byref(v) for v in values))
            return (values[0].high<<32)|values[0].low
        finally:assert api.CloseHandle(handle)
    nonce=uuid.uuid4().hex
    server=subprocess.Popen(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
        '-File',str(ROOT/'tests/storage_channel_exit_server.ps1'),'-Nonce',nonce,
        '-PeerId',str(os.getpid()),'-PeerBirth',str(birth(os.getpid())),
        '-Workspace',str(tmp_path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        deadline=time.monotonic()+5
        while not (tmp_path/'server-ready').exists():
            assert server.poll() is None
            assert time.monotonic()<deadline
            time.sleep(.01)
        client=Client(nonce,'peer_exit',parent_pid=server.pid,parent_birth=birth(server.pid),seconds=10)
        client.write(b'request');assert client.read()==b'reply'
        client.write(b'close then exit')
        assert client._current is None
        (tmp_path/'client-write-completed').write_bytes(b'native write and release confirmed')
        output,error=server.communicate(timeout=5)
        assert server.returncode==0,(output+error).decode(errors='replace')
        client.close()
        assert client.closed and not client.faulted and client not in _RETAINED
        assert client._states=={'pipe':'CLOSED','peer':'CLOSED'}
        assert client._handles=={'pipe':None,'peer':None}
    finally:
        if server.poll() is None:server.kill();server.wait(timeout=5)
