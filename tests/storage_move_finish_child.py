"""Native cleanup of synthetic fixture scopes, never a production permit."""
import ctypes
import os
from pathlib import Path
import threading
import time

from storage_publish_manifest import encoded


def run(endpoint,authority,client,guards,root,case,source_calls):
    authority_handles={h for h in authority._handles.values() if h is not None}
    client_handles={h for h in client._handles.values() if h is not None}
    actual_authority_close=authority.api.CloseHandle
    actual_client_close=client.api.CloseHandle
    calls=[]
    expected='publish_endpoint_finish_request'
    def authority_close(handle):
        if handle in authority_handles:
            calls.append(('authority',handle))
            if case=='MoveReleaseFinishAuthorityFalse':ctypes.set_last_error(6);return 0
            if case=='MoveReleaseFinishAuthorityException':
                assert actual_authority_close(handle)
                raise RuntimeError('fixture_finish_after_actual_close')
            if case=='MoveReleaseFinishAuthorityDeadline':
                result=actual_authority_close(handle);assert result
                while time.monotonic()<guards.deadline:time.sleep(.01)
                client._shared_deadline.check()
                return result
        return actual_authority_close(handle)
    def client_close(handle):
        if handle in client_handles:
            calls.append(('client',handle))
            key=next(k for k,h in client._handles.items() if h==handle)
            if case=='MoveReleaseFinishPipeFalse' and key=='pipe':ctypes.set_last_error(6);return 0
            if case=='MoveReleaseFinishPeerFalse' and key=='peer':ctypes.set_last_error(6);return 0
            if case=='MoveReleaseFinishPipeException' and key=='pipe':
                assert actual_client_close(handle)
                raise RuntimeError('fixture_finish_after_actual_close')
            if case=='MoveReleaseFinishFinalDeadline' and key=='peer':
                result=actual_client_close(handle);assert result
                while time.monotonic()<guards.deadline:time.sleep(.01)
                return result
        return actual_client_close(handle)
    authority.api.CloseHandle=authority_close;client.api.CloseHandle=client_close
    if case in ('MoveReleaseFinishNormal','MoveReleaseFinishNoSidecars'):
        assert endpoint.serve_finish() is True
        assert endpoint.finish_completed and not endpoint.faulted and endpoint.phase=='WORKER_CLEANED'
        assert authority.closed and client.closed
        assert set(authority._states.values())==set(client._states.values())=={'CLOSED'}
        assert len(calls)==len(authority_handles)+len(client_handles)
        assert len(source_calls)==len(guards.release_states)
        return 'WORKER_FINISHED'
    expected_codes={'MoveReleaseFinishAuthorityFalse':'publish_authority_native_6',
        'MoveReleaseFinishPipeFalse':'publish_channel_native_6','MoveReleaseFinishPeerFalse':'publish_channel_native_6',
        'MoveReleaseFinishAuthorityException':'fixture_finish_after_actual_close',
        'MoveReleaseFinishPipeException':'fixture_finish_after_actual_close',
        'MoveReleaseFinishAuthorityDeadline':'publish_guard_deadline',
        'MoveReleaseFinishFinalDeadline':'publish_guard_deadline'}
    expected=expected_codes.get(case,expected)
    if case=='MoveReleaseFinishReentrantFinal':
        actual_guard=endpoint._cleanup_guard;triggered=[]
        def at_decision():
            actual_guard()
            if not triggered and endpoint._decision_lock._is_owned():
                triggered.append(True)
                try:endpoint.serve_finish();raise AssertionError('fixture_final_reentry_admitted')
                except ValueError as error:assert str(error)=='publish_endpoint_reentrant'
                actual_guard()
        endpoint._cleanup_guard=at_decision
        try:endpoint.serve_finish();raise AssertionError('fixture_final_reentry_completed')
        except ValueError as error:assert str(error)=='publish_endpoint_faulted'
        assert triggered and authority.closed and client.closed and len(calls)==len(authority_handles)+len(client_handles)
    elif case=='MoveReleaseFinishAuthorityReentrantFinal':
        actual_guard=authority._guard;triggered=[]
        def at_authority_decision():
            actual_guard()
            if not triggered and authority._fault_lock._is_owned():
                triggered.append(True)
                try:authority.check();raise AssertionError('fixture_authority_final_reentry_admitted')
                except ValueError as error:assert str(error)=='publish_authority_concurrent'
                actual_guard()
        authority._guard=at_authority_decision
        try:endpoint.serve_finish();raise AssertionError('fixture_authority_final_reentry_completed')
        except ValueError as error:assert str(error)=='publish_authority_unconfirmed'
        assert triggered and authority.faulted and not authority.closed
        assert set(authority._states.values())=={'CLOSED'} and len(calls)==len(authority_handles)
        assert set(client._states.values())=={'OWNED'}
    elif case=='MoveReleaseFinishClientConcurrentFinal':
        actual_guard=client._cleanup_check;entered=threading.Event();resume=threading.Event();errors=[];triggered=[]
        def at_client_decision():
            actual_guard()
            if not triggered and all(state=='CLOSED' for state in client._states.values()):
                triggered.append(True);entered.set();assert resume.wait(5)
        client._cleanup_check=at_client_decision
        def primary():
            try:endpoint.serve_finish();raise AssertionError('fixture_client_final_completed')
            except ValueError as error:errors.append(('finish',str(error)))
        def competing():
            try:client.write(b'forbidden');raise AssertionError('fixture_client_final_write_admitted')
            except ValueError as error:errors.append(('write',str(error)))
        first=threading.Thread(target=primary);first.start();assert entered.wait(5)
        second=threading.Thread(target=competing);second.start();assert client._fault_requested.wait(5)
        resume.set();first.join(5);second.join(5)
        assert not first.is_alive() and not second.is_alive()
        assert sorted(errors)==[('finish','publish_channel_unconfirmed'),('write','publish_channel_concurrent')]
        assert authority.closed and client.faulted and not client.closed and set(client._states.values())=={'CLOSED'}
        assert len(calls)==len(authority_handles)+len(client_handles)
    elif case in ('MoveReleaseFinishConcurrentBefore','MoveReleaseFinishConcurrentDuring','MoveReleaseFinishConcurrentFinal','MoveReleaseFinishReentrant'):
        entered=threading.Event();resume=threading.Event();errors=[]
        if case=='MoveReleaseFinishConcurrentDuring':
            def during(handle):
                if handle in authority_handles:
                    calls.append(('authority',handle));assert actual_authority_close(handle)
                    entered.set();assert resume.wait(5);return 1
                return actual_authority_close(handle)
            authority.api.CloseHandle=during
        elif case=='MoveReleaseFinishConcurrentFinal':
            actual_guard=endpoint._cleanup_guard
            def final_guard():
                actual_guard()
                if authority.closed and client.closed:
                    entered.set();assert resume.wait(5)
            endpoint._cleanup_guard=final_guard
        else:
            actual_step=authority._close
            def before(key,**options):
                if key=='marker':
                    if case=='MoveReleaseFinishReentrant':
                        try:endpoint.serve_finish();raise AssertionError('fixture_finish_reentry_admitted')
                        except ValueError as error:assert str(error)=='publish_endpoint_reentrant'
                    else:entered.set();assert resume.wait(5)
                return actual_step(key,**options)
            authority._close=before
        if case=='MoveReleaseFinishReentrant':
            try:endpoint.serve_finish();raise AssertionError('fixture_finish_fault_admitted')
            except ValueError as error:assert str(error)=='publish_endpoint_faulted'
        else:
            def first():
                try:endpoint.serve_finish()
                except ValueError as error:errors.append(('first',str(error)))
            def second():
                try:endpoint.serve_finish()
                except ValueError as error:errors.append(('second',str(error)))
            primary=threading.Thread(target=first);primary.start();assert entered.wait(5)
            competing=threading.Thread(target=second);competing.start();assert endpoint._fault_requested.wait(5)
            resume.set();primary.join(5);competing.join(5)
            assert not primary.is_alive() and not competing.is_alive()
            assert sorted(errors)==[('first','publish_endpoint_faulted'),('second','publish_endpoint_concurrent')]
        expected_count={'MoveReleaseFinishConcurrentBefore':0,'MoveReleaseFinishConcurrentDuring':1,
            'MoveReleaseFinishConcurrentFinal':len(authority_handles)+len(client_handles),'MoveReleaseFinishReentrant':0}[case]
        assert len(calls)==expected_count
        if case=='MoveReleaseFinishConcurrentFinal':
            assert authority.closed and client.closed and set(authority._states.values())==set(client._states.values())=={'CLOSED'}
    else:
        try:endpoint.serve_finish();raise AssertionError('fixture_finish_fault_admitted')
        except (ValueError,OSError,RuntimeError) as error:assert str(error)==expected
    assert endpoint.faulted and endpoint.phase=='FAULT' and not endpoint.finish_completed and endpoint.source_closed
    before=list(calls)
    try:endpoint.serve_finish();raise AssertionError('fixture_finish_retried')
    except ValueError as error:assert str(error)=='publish_endpoint_faulted'
    assert calls==before and len(source_calls)==len(guards.release_states)
    if case in ('MoveReleaseFinishWrongHash','MoveReleaseFinishWrongSeq','MoveReleaseFinishWrongContext',
                'MoveReleaseFinishOldNonce','MoveReleaseFinishNoncanonical'):
        assert not calls and not endpoint.finish_attempted
    if case in ('MoveReleaseFinishAuthorityFalse','MoveReleaseFinishAuthorityException'):
        assert len(calls)==1 and authority._states['marker']=='FAULT'
        assert all(authority._states[k]=='OWNED' for k in ('thread','mutex0','mutex1','mutex2'))
    if case=='MoveReleaseFinishAuthorityDeadline':
        assert len(calls)==1 and authority._states['marker']=='CLOSED' and authority._handles['marker'] is None
    if case in ('MoveReleaseFinishPipeFalse','MoveReleaseFinishPipeException','MoveReleaseFinishPeerFalse','MoveReleaseFinishFinalDeadline'):
        assert authority.closed and set(authority._states.values())=={'CLOSED'}
    if case=='MoveReleaseFinishPeerFalse':assert client._states=={'pipe':'CLOSED','peer':'FAULT'}
    if case=='MoveReleaseFinishFinalDeadline':
        assert client._states=={'pipe':'CLOSED','peer':'CLOSED'} and not client.closed
        assert all(handle is None for handle in client._handles.values())
    evidence={'result':'FINISH_FAULT','endpoint_faulted':True,'finish_completed':False,
        'source_closed':True,'authority_states':authority._states,'client_states':client._states,
        'close_calls':len(calls),'source_close_calls':len(source_calls)}
    temporary=Path(root)/'RUNTIME/finish-fault.json.tmp'
    with open(temporary,'xb') as stream:
        stream.write(encoded(evidence));stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,Path(root)/'RUNTIME/finish-fault.json')
    while True:time.sleep(1)
