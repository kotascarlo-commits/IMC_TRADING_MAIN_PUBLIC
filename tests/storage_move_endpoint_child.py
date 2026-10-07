"""Private native moves of synthetic file bytes; no production/copy permit."""
import ctypes
from pathlib import Path
import time
import os
import threading

from storage_publish_guard import RenameGuards
from storage_publish_channel import _FileTime
from storage_publish_manifest import encoded
from storage_publish_writer import digest
from storage_publish_move_endpoint import MoveEndpoint


def run(client,authority,data,root,nonce,pid,birth,case):
    value=data['generation'];paths=value['paths']
    members={key:Path(paths[key]) for key,info in value['members'].items() if info is not None}
    destinations=[Path(paths[key]) for key in ('archive_shm','archive_wal','archive_main','installed_main')]
    guards=RenameGuards(root,members,deadline=time.monotonic()+10,destinations=destinations,
        nonce=nonce,attached_directories=data['directories'],attached_sentinels=data['sentinels'],
        shared_deadline=client._shared_deadline)
    guards.acquire_retained()
    api=client.api;api.GetCurrentProcess.argtypes=[];api.GetCurrentProcess.restype=ctypes.c_void_p
    created,exit,kernel,user=(_FileTime() for _ in range(4))
    assert api.GetProcessTimes(api.GetCurrentProcess(),ctypes.byref(created),ctypes.byref(exit),ctypes.byref(kernel),ctypes.byref(user))
    binding={'publish_nonce':nonce,'generation_sha256':digest(encoded(value)),
        'envelope_sha256':'e'*64,'role':'rename','parent_pid':pid,'parent_birth':birth,
        'worker_pid':os.getpid(),'worker_birth':(created.high<<32)|created.low}
    endpoint=MoveEndpoint(value,binding,project_root=root,client=client,authority=authority,guards=guards)
    client.write(encoded({'result':'MOVE_READY','context_sha256':endpoint.context_sha256}))
    if case=='MoveReleaseEarly':
        try:endpoint.serve_release();raise AssertionError('fixture_early_release_admitted')
        except ValueError as error:assert str(error)=='publish_endpoint_release_phase'
        assert endpoint.faulted and not endpoint.close_attempted and not guards.release_attempted
        assert all(member['handle'] is not None for group in (guards.members,guards.sentinels,guards.directories) for member in group.values())
        assert authority.check() is True
        client.write(encoded({'result':'SOURCE_FAULT','endpoint_faulted':True,'authority_healthy':True,
            'source_closed':False,'close_calls':0,'states':{}}))
        while True:time.sleep(1)
    if case in ('MoveReentrant','MoveReceiveError','MoveResponseLost','MoveUnknownNative'):
        calls=[];native=guards.nt.NtSetInformationFile
        actual_write=client.write
        def submit(*args):
            calls.append(True);result=native(*args)
            return -1073741823 if case=='MoveUnknownNative' else result
        guards.nt.NtSetInformationFile=submit
        if case=='MoveReentrant':
            original=guards.bounded_path
            def bounded(path):
                result=original(path)
                if Path(path)==Path(paths['archive_shm']):
                    try:endpoint.serve_one();raise AssertionError('fixture_reentry_admitted')
                    except ValueError as error:assert str(error)=='publish_endpoint_reentrant'
                return result
            guards.bounded_path=bounded
        if case=='MoveReceiveError':
            def receive():raise OSError('fixture_move_receive_failed')
            client.read=receive
        if case=='MoveResponseLost':
            def send(_):raise OSError('fixture_move_response_lost')
            client.write=send
        expected={'MoveReentrant':'publish_endpoint_faulted','MoveReceiveError':'fixture_move_receive_failed',
            'MoveResponseLost':'fixture_move_response_lost','MoveUnknownNative':'publish_native_rename_unconfirmed'}[case]
        try:endpoint.serve_one();raise AssertionError('fixture_move_fault_admitted')
        except (ValueError,OSError) as error:assert str(error)==expected
        assert endpoint.faulted and len(calls)==(1 if case in ('MoveResponseLost','MoveUnknownNative') else 0)
        try:endpoint.serve_one();raise AssertionError('fixture_move_fault_retried')
        except ValueError as error:assert str(error)=='publish_endpoint_faulted'
        assert len(calls)==(1 if case in ('MoveResponseLost','MoveUnknownNative') else 0)
        assert all(member['handle'] is not None for member in guards.members.values())
        if case=='MoveUnknownNative':assert guards.native_operation is not None
        actual_write(encoded({'result':'FAULT','code':expected}))
        while True:time.sleep(1)
    if case in ('MoveConcurrent','MoveConcurrentAck'):
        entered=threading.Event();release=threading.Event();errors=[];calls=[]
        original=guards.bounded_path;native=guards.nt.NtSetInformationFile
        def bounded(path):
            result=original(path)
            if Path(path)==Path(paths['archive_shm']):
                entered.set();assert release.wait(5)
            return result
        def submitted(*args):calls.append(True);return native(*args)
        if case=='MoveConcurrent':guards.bounded_path=bounded
        else:
            from contextlib import contextmanager
            permit=endpoint._submission;permits=[]
            @contextmanager
            def before_ack():
                permits.append(True)
                if len(permits)==2:entered.set();assert release.wait(5)
                with permit():yield
            endpoint._submission=before_ack
        guards.nt.NtSetInformationFile=submitted
        actual_write=client.write;sends=[]
        def response(body):sends.append(body);return actual_write(body)
        client.write=response
        def first():
            try:endpoint.serve_one()
            except ValueError as error:errors.append(str(error))
        worker=threading.Thread(target=first);worker.start();assert entered.wait(5)
        try:endpoint.serve_one();raise AssertionError('fixture_parallel_move_admitted')
        except ValueError as error:assert str(error)=='publish_endpoint_concurrent'
        assert endpoint.faulted and endpoint.phase=='FAULT'
        release.set();worker.join(5)
        assert not worker.is_alive() and errors==['publish_endpoint_faulted']
        assert len(calls)==(1 if case=='MoveConcurrentAck' else 0) and not sends
        assert all(member['handle'] is not None for member in guards.members.values())
        assert all(member['handle'] is not None for member in guards.directories.values())
        try:endpoint.serve_one();raise AssertionError('fixture_concurrent_fault_retried')
        except ValueError as error:assert str(error)=='publish_endpoint_faulted'
        assert len(calls)==(1 if case=='MoveConcurrentAck' else 0) and not sends
        actual_write(encoded({'result':'FAULT','code':'publish_endpoint_faulted'}))
        while True:time.sleep(1)
    for _ in range(4):endpoint.serve_one()
    assert endpoint._sequence==4 and not endpoint.faulted
    guards.assert_bound()
    assert guards.digest('work_main')==value['members']['work_main']['sha256']
    assert guards.digest('source_main')==value['members']['source_main']['sha256']
    if case.startswith('MoveRelease'):
        actual_close=guards.api.CloseHandle;actual_write=client.write;calls=[]
        source_handles={member['handle'] for group in (guards.members,guards.sentinels,guards.directories) for member in group.values()}
        qpc_expiry=case in ('MoveReleaseQpcFirst','MoveReleaseQpcFinal')
        def closed(handle):
            if handle in source_handles:
                calls.append(handle)
                if case=='MoveReleaseFalse':return 0
                if case=='MoveReleaseException':
                    assert actual_close(handle)
                    raise RuntimeError('fixture_source_after_actual_close')
                if case=='MoveReleaseFinalFault' and len(calls)==len(source_handles):
                    result=actual_close(handle);assert result
                    endpoint._fault()
                    return result
                if case=='MoveReleaseLocalFinal' and len(calls)==len(source_handles):
                    result=actual_close(handle);assert result
                    while time.monotonic()<guards.deadline:time.sleep(.01)
                    client._shared_deadline.check()
                    return result
                if qpc_expiry and len(calls)==(1 if case=='MoveReleaseQpcFirst' else len(source_handles)):
                    result=actual_close(handle);assert result
                    # Real shared QPC deadline: no clock/end-tick substitution.
                    while True:
                        try:client._shared_deadline.check()
                        except TimeoutError as error:
                            assert str(error)=='publish_deadline_expired'
                            break
                        time.sleep(.01)
                    return result
            return actual_close(handle)
        guards.api.CloseHandle=closed
        if case=='MoveReleaseAckLost':
            def lost(_):raise OSError('fixture_release_ack_lost')
            client.write=lost
        if case=='MoveReleaseAckDeadline':
            def delayed(body):
                actual_write(body)
                while time.monotonic()<guards.deadline:time.sleep(.01)
                client._shared_deadline.check()
            client.write=delayed
        expected={'MoveReleaseWrongSeq':'publish_endpoint_release_request',
            'MoveReleaseFalse':'publish_native_close_unconfirmed','MoveReleaseException':'publish_native_close_unconfirmed',
            'MoveReleaseAckLost':'fixture_release_ack_lost','MoveReleaseFinalFault':'publish_endpoint_faulted',
            'MoveReleaseQpcFirst':'publish_deadline_faulted','MoveReleaseQpcFinal':'publish_deadline_faulted',
            'MoveReleaseLocalFinal':'publish_guard_deadline','MoveReleaseAckDeadline':'publish_guard_deadline'}
        if case.startswith('MoveReleaseFinish'):
            endpoint.serve_release()
            from storage_move_finish_child import run as finish
            return finish(endpoint,authority,client,guards,root,case,calls)
        if case in ('MoveReleaseNormal','MoveReleaseNoSidecars','MoveReleaseReplay'):
            endpoint.serve_release()
            assert endpoint.phase=='SOURCE_RELEASED' and endpoint.source_closed and endpoint.release_ack_completed
            assert len(calls)==len(source_handles) and set(guards.release_states.values())=={'CLOSED'}
            assert all(member['handle'] is None for group in (guards.members,guards.sentinels,guards.directories) for member in group.values())
            if case!='MoveReleaseReplay':return None
            try:endpoint.serve_release();raise AssertionError('fixture_release_replay_admitted')
            except ValueError as error:assert str(error)=='publish_endpoint_release_phase'
            assert endpoint.release_ack_completed
        elif case in ('MoveReleaseConcurrentBefore','MoveReleaseConcurrentDuring','MoveReleaseReentrant'):
            entered=threading.Event();resume=threading.Event();errors=[]
            if case=='MoveReleaseConcurrentDuring':
                def closing(handle):
                    if handle in source_handles:
                        calls.append(handle);assert actual_close(handle)
                        entered.set();assert resume.wait(5);return 1
                    return actual_close(handle)
                guards.api.CloseHandle=closing
            else:
                bound=guards._remaining_bound
                def checked():
                    bound()
                    if case=='MoveReleaseReentrant':
                        try:endpoint.serve_release();raise AssertionError('fixture_release_reentry_admitted')
                        except ValueError as error:assert str(error)=='publish_endpoint_reentrant'
                    else:entered.set();assert resume.wait(5)
                guards._remaining_bound=checked
            if case=='MoveReleaseReentrant':
                try:endpoint.serve_release();raise AssertionError('fixture_release_fault_admitted')
                except ValueError as error:assert str(error)=='publish_endpoint_faulted'
            else:
                def first():
                    try:endpoint.serve_release()
                    except ValueError as error:errors.append(('first',str(error)))
                def second():
                    try:endpoint.serve_release()
                    except ValueError as error:errors.append(('second',str(error)))
                worker=threading.Thread(target=first);worker.start();assert entered.wait(5)
                competing=threading.Thread(target=second);competing.start()
                assert endpoint._fault_requested.wait(5)
                resume.set();worker.join(5);competing.join(5)
                assert not worker.is_alive() and not competing.is_alive()
                assert sorted(errors)==[('first','publish_endpoint_faulted'),('second','publish_endpoint_concurrent')]
            assert len(calls)==(1 if case=='MoveReleaseConcurrentDuring' else 0)
            assert list(guards.release_states.values()).count('CLOSED')==len(calls)
            assert 'UNKNOWN' not in guards.release_states.values()
        else:
            if case=='MoveReleaseExpiry':
                # Expire the independently enforced guard deadline. Keep the
                # channel/Parent deadline healthy for a native diagnostic ACK.
                guards.deadline=time.monotonic()-1
                expected[case]='publish_guard_deadline'
            try:endpoint.serve_release();raise AssertionError('fixture_source_release_fault_admitted')
            except (ValueError,OSError) as error:assert str(error)==expected[case]
        assert endpoint.faulted
        assert endpoint.release_ack_completed==(case=='MoveReleaseReplay')
        assert endpoint.source_closed==(case in ('MoveReleaseAckLost','MoveReleaseReplay','MoveReleaseFinalFault','MoveReleaseQpcFinal','MoveReleaseLocalFinal','MoveReleaseAckDeadline'))
        if case in ('MoveReleaseFinalFault','MoveReleaseLocalFinal'):
            assert len(calls)==len(source_handles) and set(guards.release_states.values())=={'CLOSED'}
        count=len(calls)
        try:endpoint.serve_release();raise AssertionError('fixture_source_release_retried')
        except ValueError as error:assert str(error)=='publish_endpoint_faulted'
        assert len(calls)==count
        if case in ('MoveReleaseFalse','MoveReleaseException'):
            assert count==1 and list(guards.release_states.values()).count('UNKNOWN')==1
            assert list(guards.release_states.values()).count('CLOSED')==0
        evidence={'result':'SOURCE_FAULT','endpoint_faulted':True,'authority_healthy':not qpc_expiry,
            'source_closed':endpoint.source_closed,'close_calls':count,'states':guards.release_states}
        if qpc_expiry:
            assert client._shared_deadline is authority._deadline is guards._shared_deadline
            assert client._shared_deadline._faulted and not endpoint.release_ack_completed
            assert count==(1 if case=='MoveReleaseQpcFirst' else len(source_handles))
            assert list(guards.release_states.values()).count('CLOSED')==count
            assert 'UNKNOWN' not in guards.release_states.values()
            # Private fixture diagnostic only. An expired native channel must
            # not send a successful ACK; this file grants no release authority.
            temporary=Path(root)/'RUNTIME/release-expiry.json.tmp'
            with open(temporary,'xb') as stream:
                stream.write(encoded(evidence));stream.flush();os.fsync(stream.fileno())
            os.replace(temporary,Path(root)/'RUNTIME/release-expiry.json')
        else:
            assert authority.check() is True and not authority.faulted
            actual_write(encoded(evidence))
        while True:time.sleep(1)
    return guards
