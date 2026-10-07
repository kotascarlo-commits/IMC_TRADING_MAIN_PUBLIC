"""Twelve native durable writer completions with synthetic generation facts.

No Source file is created, moved or certified by this fixture.
"""
import ctypes
import json
from pathlib import Path
import time

from storage_publish_parent import ParentReadGuards
from storage_publish_manifest import STEPS, encoded, Journal
from storage_publish_writer import digest
from storage_publish_writer_scope import build
from storage_publish_channel import _FileTime
from storage_publish_deadline import Deadline


def journal_fault(endpoint,scopes,authority,client,root,error):
    assert endpoint.faulted and not endpoint.finish_completed
    diagnostic={'result':'JOURNAL_FINISH_FAULT','endpoint_faulted':endpoint.faulted,
        'finish_completed':endpoint.finish_completed,'code':str(error),
        'scope_closed':scopes.release_closed,'authority_closed':authority.closed,'client_closed':client.closed}
    import os
    path=root/'RUNTIME/journal-fault.json';temporary=path.with_suffix('.tmp')
    with temporary.open('xb') as stream:
        stream.write(encoded(diagnostic));stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,path)
    while True:time.sleep(1)


def run(client,authority,data,root,nonce,pid,birth,case='Writer'):
    root=Path(root);source=root/'CORE/data/imc-main.db';archive=root/'original/imc-main.db';work=root/'work/imc-main.db'
    paths={'source_main':str(source),'source_wal':str(source)+'-wal','source_shm':str(source)+'-shm',
        'work_main':str(work),'archive_main':str(archive),'archive_wal':str(archive)+'-wal',
        'archive_shm':str(archive)+'-shm','installed_main':str(source),'retained_new_main':str(root/'retained-new.db')}
    value={'copy_nonce':'a'*32,'publish_nonce':nonce,'root':str(root),'paths':paths,
        'members':{key:{'volume':5,'file_id':i,'bytes':1,'sha256':'a'*64}
                   for i,key in enumerate(('source_main','source_wal','source_shm','work_main'),1)}}
    api=client.api;api.GetCurrentProcess.argtypes=[];api.GetCurrentProcess.restype=ctypes.c_void_p
    created,exit,kernel,user=(_FileTime() for _ in range(4))
    assert api.GetProcessTimes(api.GetCurrentProcess(),ctypes.byref(created),ctypes.byref(exit),ctypes.byref(kernel),ctypes.byref(user))
    binding={'publish_nonce':nonce,'generation_sha256':digest(encoded(value)),
        'envelope_sha256':'e'*64,'role':'journal','parent_pid':pid,'parent_birth':birth,
        'worker_pid':__import__('os').getpid(),'worker_birth':(created.high<<32)|created.low}
    sentinel=Path(data['directory'])/('.imc-publish-guard-'+nonce+'.sentinel')
    scopes=ParentReadGuards(root,{'sentinel':sentinel},deadline=time.monotonic()+10,
        nonce=nonce,attached_directories=data['directories'],attached_sentinels=data['sentinels'],
        shared_deadline=client._shared_deadline)
    scopes.acquire_retained()
    common={'project_root':str(root),'deadline':client._shared_deadline,'authority':authority,'scopes':scopes}
    # Same end/frequency but a different object must not bypass host binding.
    other_deadline=Deadline(*client._shared_deadline.binding)
    try:
        build(data['directory'],value,binding,**{**common,'deadline':other_deadline})
        raise AssertionError('fixture_other_deadline_admitted')
    except ValueError as error:assert str(error)=='publish_writer_native_scope'
    wrong_birth=dict(binding);wrong_birth['worker_birth']+=1
    try:
        build(data['directory'],value,wrong_birth,**common)
        raise AssertionError('fixture_wrong_worker_birth_admitted')
    except ValueError as error:assert str(error)=='publish_writer_native_birth'
    actual_role=client._role;client._role='rename'
    try:
        try:
            build(data['directory'],value,binding,**common)
            raise AssertionError('fixture_wrong_channel_role_admitted')
        except ValueError as error:assert str(error)=='publish_writer_native_binding'
    finally:client._role=actual_role
    actual_destinations=scopes.destinations
    scopes.destinations=(root/'RUNTIME/extra-source-target',)
    try:
        try:
            build(data['directory'],value,binding,**common)
            raise AssertionError('fixture_extra_destination_admitted')
        except ValueError as error:assert str(error)=='publish_writer_native_binding'
    finally:scopes.destinations=actual_destinations
    extra=root/'RUNTIME'
    assert extra not in scopes.directories
    scopes.directories[extra]=dict(next(iter(scopes.directories.values())))
    try:
        try:
            build(data['directory'],value,binding,**common)
            raise AssertionError('fixture_extra_directory_admitted')
        except ValueError as error:assert str(error)=='publish_writer_native_inventory'
    finally:del scopes.directories[extra]
    closed_probe=ParentReadGuards(root,{'sentinel':sentinel},deadline=time.monotonic()+10,
        nonce=nonce,attached_directories=data['directories'],attached_sentinels=data['sentinels'],
        shared_deadline=client._shared_deadline)
    closed_probe.__enter__();closed_probe.close()
    try:
        build(data['directory'],value,binding,**{**common,'scopes':closed_probe})
        raise AssertionError('fixture_closed_scopes_admitted')
    except OSError as error:assert str(error)=='publish_native_path_unknown'
    assert not (Path(data['directory'])/'generation.json').exists()
    if case.startswith('Journal'):
        from storage_publish_journal_endpoint import JournalEndpoint
        native_endpoint=JournalEndpoint(data['directory'],value,binding,project_root=str(root),
            client=client,authority=authority,scopes=scopes)
        endpoint=native_endpoint._writer
    else:
        endpoint=build(data['directory'],value,binding,project_root=str(root),deadline=client._shared_deadline,
                       authority=authority,scopes=scopes)
    client.write(encoded({'result':'WRITER_READY','context_sha256':endpoint.context_sha256}))
    if case=='JournalEarly':
        try:native_endpoint.serve_finish();raise AssertionError('early journal finish admitted')
        except ValueError as error:
            assert str(error)=='publish_journal_endpoint_finish_phase'
            journal_fault(native_endpoint,scopes,authority,client,root,error)
    moves=(('source_shm','archive_shm'),('source_wal','archive_wal'),
           ('source_main','archive_main'),('work_main','installed_main'))
    for i in range(12):
        if not case.startswith('Journal'):
            assert client.read()==str(i).encode('ascii')
        kind='DONE' if i%2 else 'INTENT';step=STEPS[i//2]
        if i<8:
            source,target=moves[i//2]
            proof={'source':paths[source],'destination':paths[target],'identity':value['members'][source]}
            if i%2:proof.update(source_absent=True,destination_absent=False)
        else:
            proof={'installed_main':paths['installed_main'],'identity':value['members']['work_main']}
            if i==9:proof.update(integrity=True,schema=True,canonical_sidecars_absent=True)
            if i==11:proof.update(source_guard_cleanup=True,integrity=True,safety=True,absence=True)
        request=encoded({'schema_version':2,'kind':'JOURNAL_REQUEST','context_sha256':endpoint.context_sha256,
            'ipc_sequence':i+1,'journal_sequence':i,'request_nonce':f'{i+1:032x}',
            'previous_sha256':endpoint.previous,'step':step,'event_kind':kind,'evidence':proof})
        if case.startswith('Journal'):
            # Preview is synthetic fixture input, not a durable grant. Root
            # echoes it through the actual pipe; endpoint reads native bytes.
            client.write(request)
            if case=='JournalAck12Lost' and i==11:
                actual_write=client.write
                def lost_ack(body):
                    actual_write(body)
                    raise RuntimeError('fixture_journal_ack12_post_write_lost')
                client.write=lost_ack
                try:native_endpoint.serve_one();raise AssertionError('lost final ACK admitted')
                except RuntimeError as error:
                    assert native_endpoint._completed==11 and endpoint._sequence==11
                    assert endpoint.faulted and endpoint._journal.next_step==6
                    journal_fault(native_endpoint,scopes,authority,client,root,error)
            else:native_endpoint.serve_one()
        else:
            endpoint.process(request,send_response=client.write)
    assert Journal.load(data['directory'],project_root=str(root))['complete']
    assert not endpoint.faulted
    if not case.startswith('Journal'):
        return scopes
    finish=encoded({'schema_version':2,'kind':'WORKER_FINISH','context_sha256':endpoint.context_sha256,
        'ipc_sequence':13,'request_nonce':'c'*32,'journal_sha256':endpoint.previous})
    client.write(finish)
    close_calls=[]
    if case=='JournalCloseFalse':
        actual_close=scopes.api.CloseHandle
        # Fail the retained Source FD, rather than an earlier freshness
        # probe's temporary FD. The release inventory must become UNKNOWN.
        source_handle=next(iter(scopes.members.values()))['handle']
        def failed(handle):
            if handle!=source_handle:return actual_close(handle)
            close_calls.append(handle)
            ctypes.set_last_error(6)
            return 0
        scopes.api.CloseHandle=failed
    if case=='JournalFinalReentrant':
        actual=native_endpoint._cleanup_check
        def reentrant():
            if native_endpoint._decision_lock._is_owned():
                try:native_endpoint.serve_finish();raise AssertionError('journal final reentry admitted')
                except ValueError as error:assert str(error)=='publish_journal_endpoint_reentrant'
            return actual()
        native_endpoint._cleanup_check=reentrant
    if case=='JournalFinalDeadline':
        actual_close=client.api.CloseHandle
        peer=client._handles['peer']
        def delayed_close(handle):
            result=actual_close(handle)
            if handle==peer:
                assert result
                while time.monotonic()<scopes.deadline:time.sleep(.01)
            return result
        client.api.CloseHandle=delayed_close
    if case=='JournalClientConcurrentFinal':
        import threading
        actual_guard=client._cleanup_check;entered=threading.Event();resume=threading.Event();triggered=[];errors=[]
        def guarded():
            actual_guard()
            if not triggered and all(state=='CLOSED' for state in client._states.values()):
                triggered.append(True);entered.set();assert resume.wait(5)
        client._cleanup_check=guarded
        def compete():
            assert entered.wait(5)
            def write():
                try:client.write(b'forbidden');raise AssertionError('parallel write admitted')
                except ValueError as error:errors.append(str(error))
            writer=threading.Thread(target=write);writer.start()
            assert client._fault_requested.wait(5);resume.set();writer.join(5)
            assert not writer.is_alive()
        racer=threading.Thread(target=compete);racer.start()
    try:
        assert native_endpoint.serve_finish() is True
        assert case=='JournalNormal'
        assert native_endpoint.finish_completed and authority.closed and client.closed
        return 'WORKER_FINISHED'
    except (ValueError,OSError) as error:
        assert case!='JournalNormal'
        assert native_endpoint.faulted and not native_endpoint.finish_completed
        if case=='JournalClientConcurrentFinal':
            racer.join(5);assert not racer.is_alive() and errors==['publish_channel_concurrent']
            assert authority.closed and client.faulted and not client.closed
            assert set(client._states.values())=={'CLOSED'}
        if case=='JournalCloseFalse':
            assert len(close_calls)==1 and scopes.close_fault and not scopes.release_closed
            assert 'UNKNOWN' in scopes.release_states.values()
            try:
                scopes.release_source(authority_check=native_endpoint._release_authority,submission=native_endpoint._submission)
                raise AssertionError('journal native close retried')
            except ValueError as repeat:assert str(repeat)=='publish_source_release_consumed'
            assert len(close_calls)==1 and not authority.closed and not client.closed
        journal_fault(native_endpoint,scopes,authority,client,root,error)
