import json
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
from storage_publish_channel import Client
from storage_publish_deadline import Deadline
from storage_publish_authority import ParentAuthority,_RETAINED


def fault_close(authority,case):
    import ctypes
    actual=authority.api.CloseHandle;calls=[]
    def failed(handle):
        calls.append(handle)
        if case=='CloseFalse':ctypes.set_last_error(6);return 0
        assert actual(handle)
        raise RuntimeError('fixture_authority_after_actual_close')
    authority.api.CloseHandle=failed
    try:authority.close();raise AssertionError('fixture_close_fault_admitted')
    except (OSError,RuntimeError) as error:
        assert len(calls)==1 and authority._states['marker']=='FAULT'
        try:authority.close();raise AssertionError('fixture_close_retried')
        except ValueError as repeat:assert str(repeat)=='publish_authority_unconfirmed'
        assert len(calls)==1
        raise error


def race_close(authority,case):
    import threading
    entered=threading.Event();release=threading.Event();calls=[];errors=[]
    native=authority.api.CloseHandle
    if case=='ConcurrentDuringClose':
        def barrier(handle):
            calls.append(handle);assert native(handle);entered.set();assert release.wait(5);return 1
        authority.api.CloseHandle=barrier
    else:
        guard=authority._guard;steps=[]
        def guarded():
            guard();steps.append(True)
            if ((case=='ConcurrentFinalClose' and len(steps)==7)
                    or (case!='ConcurrentFinalClose' and len(steps)==2)):
                entered.set();assert release.wait(5)
                if case!='ConcurrentFinalClose':guard()
        authority._guard=guarded
        def counted(handle):calls.append(handle);return native(handle)
        authority.api.CloseHandle=counted
    def close():
        try:authority.close()
        except ValueError as error:errors.append(('close',str(error)))
    def check():
        try:authority.check()
        except ValueError as error:errors.append(('check',str(error)))
    closer=threading.Thread(target=close);closer.start();assert entered.wait(5)
    checker=threading.Thread(target=check);checker.start()
    assert authority._fault_requested.wait(5)
    release.set()
    closer.join(5);checker.join(5)
    assert not closer.is_alive() and not checker.is_alive()
    assert sorted(errors)==[('check','publish_authority_concurrent'),('close','publish_authority_unconfirmed')]
    assert authority.faulted and not authority.closed and authority in _RETAINED
    if case=='ConcurrentFinalClose':
        assert len(calls)==5 and all(state=='CLOSED' for state in authority._states.values())
        try:authority.close();raise AssertionError('fixture_final_close_retried')
        except ValueError as repeat:assert str(repeat)=='publish_authority_unconfirmed'
        assert len(calls)==5
    else:
        assert all(authority._states[k]=='OWNED' for k in ('thread','mutex0','mutex1','mutex2'))
        assert len(calls)==(1 if case=='ConcurrentDuringClose' else 0)
        assert authority._states['marker']==('CLOSED' if case=='ConcurrentDuringClose' else 'OWNED')
    raise ValueError('publish_authority_unconfirmed')

nonce,pid,birth,end,frequency,root,case=sys.argv[1:]
role='journal' if case=='Writer' or case.startswith('Journal') else ('rename' if case.startswith('Move') else 'fixture')
if case=='RelativeDeadline':
    client=Client(nonce,role,parent_pid=int(pid),parent_birth=int(birth),seconds=15)
else:
    client=Client(nonce,role,parent_pid=int(pid),parent_birth=int(birth),
                  deadline=Deadline(int(end),int(frequency)))
value=json.loads(client.read())
try:
    authority=ParentAuthority(client,project_root=root,parent_thread=value['thread_id'],
        parent_thread_birth=value['thread_birth'],marker_identity=value['marker_identity'],
        active_snapshot=value['active'],private_test_nonce=nonce)
    assert authority.check() is True
    scopes=None
    if case=='Writer' or case.startswith('Journal'):
        sys.path.insert(0,str(Path(__file__).parent))
        from storage_writer_scope_child import run
        try:
            scopes=run(client,authority,value['writer'],root,nonce,int(pid),int(birth),case)
        except AssertionError:
            # Job children use NUL stdio. Preserve fixture assertions in the
            # prepared diagnostic leaf without changing production faulting.
            import traceback
            Path(root,'RUNTIME','fixture-assertion.txt').write_text(traceback.format_exc(),encoding='utf-8')
            raise
        if scopes=='WORKER_FINISHED':
            assert authority.closed and client.closed
            sys.exit(0)
    elif case.startswith('Move'):
        sys.path.insert(0,str(Path(__file__).parent))
        from storage_move_endpoint_child import run
        scopes=run(client,authority,value['move'],root,nonce,int(pid),int(birth),case)
        if scopes=='WORKER_FINISHED':
            assert authority.closed and client.closed
            sys.exit(0)
    else:client.write(b'{"result":"PASS"}')
    command=client.read()
    if case=='ThreadEnd':
        assert command==b'check again'
        authority.check();raise AssertionError('fixture_exited_owner_thread_admitted')
    assert command==b'close now'
    if scopes:scopes.assert_bound();scopes.close()
    if case in ('CloseFalse','CloseException'):fault_close(authority,case)
    if case in ('ConcurrentBeforeClose','ConcurrentDuringClose','ConcurrentFinalClose'):race_close(authority,case)
    authority.close();assert authority.closed and not authority.faulted and not _RETAINED
    client.close()
except (ValueError,OSError,RuntimeError) as error:
    assert case not in ('Normal','Writer','MoveNormal','MoveNoSidecars')
    if case in ('ProcessBirth','RelativeDeadline'):assert not _RETAINED
    else:
        assert len(_RETAINED)==1
        assert _RETAINED[0].faulted and not _RETAINED[0].closed
        if case!='ConcurrentFinalClose':assert any(state=='OWNED' for state in _RETAINED[0]._states.values())
    client.write(json.dumps({'result':'FAULT','code':str(error)}).encode())
    while True:time.sleep(1)
