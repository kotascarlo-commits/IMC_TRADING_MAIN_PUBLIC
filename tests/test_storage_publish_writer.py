import ctypes
import hashlib
import json
from pathlib import Path
import sys
import threading

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import storage_publish_manifest as manifest
from storage_publish_deadline import Deadline
import storage_publish_writer as writer
from test_storage_publish_manifest import generation, proof


def create(tmp_path, generation, authority=lambda: True):
    binding = {'publish_nonce': generation['publish_nonce'],
        'generation_sha256': writer.digest(manifest.encoded(generation)),
        'envelope_sha256': 'f'*64, 'role': 'journal', 'parent_pid': 123,
        'parent_birth': 1234567890, 'worker_pid': 456, 'worker_birth': 2345678901}
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    frequency,tick=ctypes.c_int64(),ctypes.c_int64()
    assert api.QueryPerformanceFrequency(ctypes.byref(frequency))
    assert api.QueryPerformanceCounter(ctypes.byref(tick))
    deadline = Deadline(tick.value+frequency.value*10, frequency.value)
    return writer.Writer(tmp_path/'journal', generation, binding, project_root='C:/IMC',
                         deadline=deadline, authority_check=authority)


def request(endpoint, generation, i=0):
    kind, step = ('DONE' if i%2 else 'INTENT'), manifest.STEPS[i//2]
    evidence = proof(generation, kind, step)
    if step=='RELEASE' and kind=='DONE':
        evidence['source_guard_cleanup'] = evidence.pop('native_cleanup')
    return {'schema_version': 2, 'kind': 'JOURNAL_REQUEST',
        'context_sha256': endpoint.context_sha256, 'ipc_sequence': i+1,
        'journal_sequence': i, 'request_nonce': f'{i+1:032x}',
        'previous_sha256': endpoint.previous, 'step': step,
        'event_kind': kind, 'evidence': evidence}


def test_one_writer_all_twelve_bound_completions(tmp_path, generation):
    endpoint=create(tmp_path,generation)
    responses=[]
    for i in range(12):
        body=manifest.encoded(request(endpoint,generation,i))
        response=endpoint.process(body,send_response=responses.append)
        value=manifest.decode(response)
        assert value['journal_sequence']==i and value['ipc_sequence']==i+1
        assert value['request_sha256']==hashlib.sha256(body).hexdigest()
        assert value['event_sha256']==endpoint.previous
        assert responses[-1]==response
    result=manifest.Journal.load(tmp_path/'journal',project_root='C:/IMC')
    assert result['complete'] and result['completed_steps']==6
    release=json.loads((tmp_path/'journal/11-DONE.json').read_bytes())
    assert release['evidence']['source_guard_cleanup'] is True
    assert 'native_cleanup' not in release['evidence']
    assert not endpoint.faulted


@pytest.mark.parametrize('change',['previous','ipc_bool','journal_bool','sequence_mix','context','role','noncanonical'])
def test_invalid_request_before_write_latches(tmp_path,generation,change):
    endpoint=create(tmp_path,generation);value=request(endpoint,generation)
    if change=='previous':value['previous_sha256']='e'*64
    elif change=='ipc_bool':value['ipc_sequence']=True
    elif change=='journal_bool':value['journal_sequence']=False
    elif change=='sequence_mix':value['ipc_sequence']=0
    elif change=='context':value['context_sha256']='e'*64
    elif change=='role':value['role']='rename'
    body=manifest.encoded(value)
    if change=='noncanonical':body=b' '+body
    with pytest.raises(ValueError):endpoint.process(body,send_response=lambda _:pytest.fail('response'))
    assert endpoint.faulted and not (tmp_path/'journal/00-INTENT.json').exists()
    with pytest.raises(ValueError,match='faulted'):
        endpoint.process(manifest.encoded(request(endpoint,generation)),send_response=lambda _:None)


def test_replay_never_writes_next_event(tmp_path,generation):
    endpoint=create(tmp_path,generation);body=manifest.encoded(request(endpoint,generation))
    endpoint.process(body,send_response=lambda _:None)
    with pytest.raises(ValueError):endpoint.process(body,send_response=lambda _:pytest.fail('response'))
    assert endpoint.faulted and not (tmp_path/'journal/01-DONE.json').exists()


@pytest.mark.parametrize('failure',['fsync','close','post_authority','response'])
def test_visible_event_does_not_prove_success(tmp_path,generation,monkeypatch,failure):
    permitted=[True];endpoint=create(tmp_path,generation,lambda:permitted[0])
    body=manifest.encoded(request(endpoint,generation));responses=[]
    if failure=='fsync':
        def fail(_):raise OSError('fixture_fsync')
        monkeypatch.setattr(manifest.os,'fsync',fail)
    elif failure=='close':
        actual=Path.open
        class CloseFault:
            def __init__(self,stream):self.stream=stream
            def write(self,body):return self.stream.write(body)
            def flush(self):return self.stream.flush()
            def fileno(self):return self.stream.fileno()
            def close(self):self.stream.close();raise OSError('fixture_close')
        monkeypatch.setattr(Path,'open',lambda path,*a,**k:CloseFault(actual(path,*a,**k)))
    elif failure=='post_authority':
        actual=manifest.fresh_record
        def write_then_lose(*args):
            result=actual(*args);permitted[0]=False;return result
        monkeypatch.setattr(manifest,'fresh_record',write_then_lose)
    def send(response):
        if failure=='response':raise OSError('fixture_send')
        responses.append(response)
    with pytest.raises((ValueError,OSError,RuntimeError)):endpoint.process(body,send_response=send)
    assert endpoint.faulted and (tmp_path/'journal/00-INTENT.json').exists()
    assert not responses
    with pytest.raises(ValueError,match='faulted'):endpoint.process(body,send_response=send)


def test_caught_callback_reentry_still_faults_before_io(tmp_path,generation):
    endpoint=create(tmp_path,generation);body=manifest.encoded(request(endpoint,generation))
    def reentry():
        with pytest.raises(ValueError,match='reentrant'):
            endpoint.process(body,send_response=lambda _:None)
        return True
    endpoint._authority=reentry
    with pytest.raises(ValueError,match='faulted'):endpoint.process(body,send_response=lambda _:None)
    assert not (tmp_path/'journal/00-INTENT.json').exists()


def test_concurrent_call_does_not_queue_or_write(tmp_path,generation):
    endpoint=create(tmp_path,generation);body=manifest.encoded(request(endpoint,generation))
    entered=threading.Event();release=threading.Event();errors=[]
    def authority():entered.set();assert release.wait(3);return True
    endpoint._authority=authority
    def run():
        try:endpoint.process(body,send_response=lambda _:pytest.fail('response'))
        except ValueError as error:errors.append(str(error))
    worker=threading.Thread(target=run);worker.start();assert entered.wait(3)
    with pytest.raises(ValueError,match='concurrent'):endpoint.process(body,send_response=lambda _:None)
    release.set();worker.join(3)
    assert not worker.is_alive() and errors==['publish_writer_faulted']
    assert not (tmp_path/'journal/00-INTENT.json').exists()


def test_authority_checks_bracket_header_creation(tmp_path,generation):
    calls=[]
    def authority():calls.append(True);return len(calls)==1
    with pytest.raises(ValueError,match='authority_lost'):create(tmp_path,generation,authority)
    assert (tmp_path/'journal/generation.json').exists() and len(calls)==2


def test_authority_callback_cannot_mutate_bound_generation(tmp_path,generation):
    original=manifest.decode(manifest.encoded(generation))
    def authority():generation['copy_nonce']='e'*32;return True
    endpoint=create(tmp_path,generation,authority)
    endpoint.process(manifest.encoded(request(endpoint,original)),send_response=lambda _:None)
    result=manifest.Journal.load(tmp_path/'journal',project_root='C:/IMC')
    assert result['generation']==original and not endpoint.faulted


@pytest.mark.parametrize('change',['contract','schema_bool','schema1_event','header_hash'])
def test_schema2_header_and_event_chain_bound(tmp_path,generation,change):
    endpoint=create(tmp_path,generation)
    endpoint.process(manifest.encoded(request(endpoint,generation)),send_response=lambda _:None)
    header=tmp_path/'journal/generation.json';event=tmp_path/'journal/00-INTENT.json'
    if change=='schema1_event':
        value=json.loads(event.read_bytes());value['schema_version']=1;event.write_bytes(manifest.encoded(value))
    else:
        value=json.loads(header.read_bytes())
        if change=='contract':value['contract']='OTHER'
        elif change=='schema_bool':value['schema_version']=True
        else:value['generation']['copy_nonce']='d'*32
        header.write_bytes(manifest.encoded(value))
    with pytest.raises(ValueError):manifest.Journal.load(tmp_path/'journal',project_root='C:/IMC')


def test_schema2_rejects_legacy_release_cleanup_claim(tmp_path,generation):
    journal=manifest.Journal(tmp_path/'journal',generation,project_root='C:/IMC',schema_version=2)
    for step in manifest.STEPS:
        journal.intent(step,proof(generation,'INTENT',step))
        if step=='RELEASE':
            with pytest.raises(ValueError,match='shape'):journal.done(step,proof(generation,'DONE',step))
            break
        journal.done(step,proof(generation,'DONE',step))
    assert manifest.Journal.load(journal.directory,project_root='C:/IMC')['pending_step']=='RELEASE'
