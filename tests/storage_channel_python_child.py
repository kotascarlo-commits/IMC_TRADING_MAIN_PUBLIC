"""Owned test peer. No production path, credentials or journal writes."""
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
from storage_publish_channel import Client,_RETAINED


def inject(client,case):
    import ctypes
    if case=='PythonPartialWrite':
        actual=client.api.WriteFile
        def half(handle,buffer,length,count,overlap):
            return actual(handle,buffer,length//2,count,overlap)
        client.api.WriteFile=half
    if case=='PythonHeapFreeFalse':
        client.api.HeapFree=lambda *args:0
    if case in ('PythonCloseFalse','PythonCloseException'):
        actual=client.api.CloseHandle;calls=[]
        def failed(handle):
            calls.append(handle)
            if case=='PythonCloseFalse':ctypes.set_last_error(6);return 0
            assert actual(handle)
            raise RuntimeError('fixture_after_actual_close')
        client.api.CloseHandle=failed
        client._fixture_close_calls=calls


def concurrent_close(client):
    import threading
    actual=client.api.CloseHandle;entered=threading.Event();release=threading.Event()
    calls=[];failures=[]
    def barrier(handle):
        calls.append(handle);assert actual(handle);entered.set();assert release.wait(5);return 1
    client.api.CloseHandle=barrier
    def closer():
        try:client.close()
        except ValueError as error:failures.append(str(error))
    thread=threading.Thread(target=closer);thread.start();assert entered.wait(5)
    try:
        client.write(b'forbidden concurrent frame')
        raise AssertionError('fixture_concurrent_allowed')
    except ValueError as error:assert str(error)=='publish_channel_concurrent'
    finally:release.set()
    thread.join(5);assert not thread.is_alive()
    assert failures==['publish_channel_unconfirmed'] and len(calls)==1
    assert client._states=={'pipe':'CLOSED','peer':'OWNED'}
    raise ValueError('publish_channel_unconfirmed')

nonce,parent_pid,parent_birth,scope,case=sys.argv[1:6]
scope=Path(scope);parent_pid=int(parent_pid);parent_birth=int(parent_birth)
if case=='PythonWrongPid':
    import ctypes
    class F(ctypes.Structure):_fields_=[('low',ctypes.c_uint32),('high',ctypes.c_uint32)]
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    api.GetCurrentProcess.restype=ctypes.c_void_p
    api.GetProcessTimes.argtypes=[ctypes.c_void_p]+[ctypes.POINTER(F)]*4
    birth,exit,kernel,user=(F() for _ in range(4))
    assert api.GetProcessTimes(api.GetCurrentProcess(),*(ctypes.byref(v) for v in (birth,exit,kernel,user)))
    parent_pid=os.getpid();parent_birth=(birth.high<<32)|birth.low
if case=='PythonWrongBirth':parent_birth+=1
try:
    if case in ('PythonUntil','PythonUntilReadTimeout'):
        from storage_publish_deadline import Deadline
        deadline=Deadline(int(sys.argv[6]),int(sys.argv[7]))
        client=Client(nonce,'fixture',parent_pid=parent_pid,parent_birth=parent_birth,deadline=deadline)
    else:
        client=Client(nonce,'fixture',parent_pid=parent_pid,parent_birth=parent_birth,
                      seconds=2 if case=='PythonReadTimeout' else 15)
    (scope/'connected').write_text('native Python client connected')
    if case in ('PythonHeapFreeFalse','PythonPartialWrite'):inject(client,case)
    if case in ('PythonReadTimeout','PythonUntilReadTimeout'):client.read();raise AssertionError('fixture_timeout_not_seen')
    assert client.read()==b'parent phase request'
    client.write(b'worker completion')
    assert client.read()==b'exit permitted'
    if case in ('PythonCloseFalse','PythonCloseException'):inject(client,case)
    if case=='PythonConcurrentClose':concurrent_close(client)
    client.close()
    assert client.closed and not client.faulted and not _RETAINED
except (ValueError,TimeoutError,RuntimeError,OSError) as error:
    expected={'PythonWrongPid':'publish_channel_peer_pid',
              'PythonWrongBirth':'publish_channel_peer_birth',
              'PythonReadTimeout':'publish_channel_deadline',
              'PythonHeapFreeFalse':'publish_channel_free_unconfirmed',
              'PythonPartialWrite':'publish_channel_write_partial',
              'PythonCloseFalse':'publish_channel_native_6',
              'PythonCloseException':'fixture_after_actual_close',
              'PythonConcurrentClose':'publish_channel_unconfirmed'}
    expected['PythonUntilReadTimeout']='publish_deadline_expired'
    assert str(error)==expected[case]
    assert len(_RETAINED)==1
    held=_RETAINED[0]
    assert held.faulted and not held.closed and held._states['peer']=='OWNED'
    if case in ('PythonReadTimeout','PythonUntilReadTimeout'):
        operation=held._current
        assert operation['submitted'] and not operation['confirmed']
        assert all(operation[k] is not None for k in ('buffer','overlap','overlap_memory','event'))
    if case in ('PythonCloseFalse','PythonCloseException'):
        assert held._states['pipe']=='FAULT' and len(held._fixture_close_calls)==1
        try:held.close();raise AssertionError('fixture_close_retried')
        except ValueError as repeat:assert str(repeat)=='publish_channel_unconfirmed'
        assert len(held._fixture_close_calls)==1
    (scope/'python-fault.json').write_text(json.dumps({'retained':True,'code':expected[case]}))
    # Only the owning native Job terminates a failed peer; no normal teardown
    # may be mistaken for successful cleanup/completion.
    while True:time.sleep(1)
