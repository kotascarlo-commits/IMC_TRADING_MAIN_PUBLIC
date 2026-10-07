"""Isolated actual client handles; no publication or IMC runtime authority."""
import json
import os
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
from storage_publish_channel import Client,_RETAINED

nonce,pid,birth,root,case=sys.argv[1:]
client=Client(nonce,'client_final',parent_pid=int(pid),parent_birth=int(birth),seconds=15)
assert client.read()==b'begin'
client.write(b'ready')
assert client.read()==b'close now'
actual_guard=client._cleanup_check;actual_close=client.api.CloseHandle
calls=[];errors=[];entered=threading.Event();resume=threading.Event();triggered=[]
def close_native(handle):calls.append(handle);return actual_close(handle)
client.api.CloseHandle=close_native
def guard():
    actual_guard()
    if not triggered and all(state=='CLOSED' for state in client._states.values()):
        triggered.append(True)
        if case=='concurrent':entered.set();assert resume.wait(5)
        else:
            try:client.write(b'forbidden');raise AssertionError('reentrant write admitted')
            except ValueError as error:assert str(error)=='publish_channel_concurrent'
            actual_guard()
client._cleanup_check=guard
def closer():
    try:client.close();raise AssertionError('faulted final close admitted')
    except ValueError as error:errors.append(('close',str(error)))
if case=='concurrent':
    primary=threading.Thread(target=closer);primary.start();assert entered.wait(5)
    def competing():
        try:client.write(b'forbidden');raise AssertionError('parallel write admitted')
        except ValueError as error:errors.append(('write',str(error)))
    secondary=threading.Thread(target=competing);secondary.start()
    assert client._fault_requested.wait(5)
    resume.set();primary.join(5);secondary.join(5)
    assert not primary.is_alive() and not secondary.is_alive()
    assert sorted(errors)==[('close','publish_channel_unconfirmed'),('write','publish_channel_concurrent')]
else:
    closer();assert errors==[('close','publish_channel_unconfirmed')]
assert client.faulted and not client.closed and client in _RETAINED
assert client._states=={'pipe':'CLOSED','peer':'CLOSED'}
assert client._handles=={'pipe':None,'peer':None} and client._current is None and len(calls)==2
try:client.close();raise AssertionError('close retried')
except ValueError as error:assert str(error)=='publish_channel_unconfirmed'
assert len(calls)==2
report=Path(root)/'client-final.json';temporary=report.with_suffix('.tmp')
with temporary.open('xb') as stream:
    stream.write(json.dumps({'faulted':True,'closed':False,'native_closed':2,'retry_calls':0}).encode())
    stream.flush();os.fsync(stream.fileno())
os.replace(temporary,report)
while True:time.sleep(1)
