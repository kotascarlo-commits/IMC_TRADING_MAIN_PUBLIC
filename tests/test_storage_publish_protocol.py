"""Supervisor-local completion receipts; not cross-process authority proof."""
import copy
import hashlib
import os
import pickle
from pathlib import Path
import sys
import threading
import time

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import storage_publish_protocol as protocol


@pytest.fixture
def context():
    return {'publish_nonce': 'a'*32, 'generation_sha256': 'b'*64,
            'envelope_sha256': 'c'*64, 'parent_pid': 123, 'worker_pid': 456,
            'parent_birth': '2026-10-03T07:13:23.2169372Z',
            'worker_birth': '2026-10-03T07:15:14.1234567Z'}


def exchange(value):
    return protocol.Exchange(value, deadline=time.monotonic()+10)


def request(owner, i=0, nonce=None):
    return owner.request(operation=protocol.OPERATIONS[i], previous_sha256='d'*64,
                         request_nonce=nonce or f'{i+1:032x}')


@pytest.mark.skipif(os.name!='nt',reason='native Windows publication')
def test_native_all_twelve_once_only_ack_publications(tmp_path,context):
    owner=exchange(context)
    context['parent_pid']=999 # No caller reference retained.
    for i,operation in enumerate(protocol.OPERATIONS):
        value=request(owner,i);path=tmp_path/f'{i}.json'
        receipt=owner.publish_ack(value,path,authority_check=lambda:True)
        ack=protocol.read_ack_bytes(path,deadline=time.monotonic()+5)
        result=owner.consume(ack,receipt,authority_check=lambda:True)
        assert result['sequence']==i and result['operation']==operation
        assert result['context_sha256']==hashlib.sha256(protocol.encoded({
            **context,'parent_pid':123})).hexdigest()
    assert not owner.faulted


@pytest.mark.parametrize('field,value', [('parent_pid',True),('worker_pid',123),
    ('worker_pid',0),('parent_birth','bad'),('worker_birth','2026-10-03T00:00:00'),
    ('publish_nonce','a'*31),('generation_sha256','b'*63),('envelope_sha256',False)])
def test_invalid_context_rejected(context,field,value):
    context[field]=value
    with pytest.raises(ValueError):exchange(context)


@pytest.mark.parametrize('field,value',[('kind','ACK'),('sequence',True),
    ('operation','ARCHIVE_WAL:INTENT'),('context_sha256','e'*64),
    ('previous_sha256','f'*64),('request_nonce','f'*32)])
def test_ack_bound_to_exact_request_before_any_publication(tmp_path,context,monkeypatch,field,value):
    owner=exchange(context);frame=request(owner);frame[field]=value
    monkeypatch.setattr(protocol,'_publish',lambda *args:pytest.fail('unexpected publication'))
    with pytest.raises(ValueError,match='request_binding'):
        owner.publish_ack(frame,tmp_path/'ack',authority_check=lambda:True)
    assert owner.faulted


@pytest.mark.skipif(os.name!='nt',reason='native Windows publication')
@pytest.mark.parametrize('failure',['readback','close','deadline','authority'])
def test_visible_ack_after_failed_publisher_has_no_consumable_receipt(tmp_path,context,monkeypatch,failure):
    owner=exchange(context);frame=request(owner);path=tmp_path/'ack'
    realread=protocol.read_ack_bytes
    def failed_read(*args,**kwargs):
        assert path.exists()
        if failure=='readback':return b'{}'
        if failure=='close':raise RuntimeError('ack_read_close_unconfirmed')
        result=realread(*args,**kwargs)
        if failure=='deadline':owner._deadline=time.monotonic()-1
        return result
    monkeypatch.setattr(protocol,'read_ack_bytes',failed_read)
    calls=[]
    def authority():
        calls.append(True)
        return failure!='authority' or len(calls)==1
    with pytest.raises((RuntimeError,ValueError,TimeoutError)):
        owner.publish_ack(frame,path,authority_check=authority)
    assert path.exists() and owner.faulted and len(owner._receipts)==0
    with pytest.raises(ValueError,match='faulted'):
        owner.consume(path.read_bytes(),protocol._Receipt(),authority_check=lambda:True)


@pytest.mark.skipif(os.name!='nt',reason='native Windows publication')
def test_preexisting_ack_never_overwritten_and_no_publication_retry(tmp_path,context):
    owner=exchange(context);frame=request(owner);path=tmp_path/'ack'
    path.write_bytes(b'old generation')
    with pytest.raises(OSError):owner.publish_ack(frame,path,authority_check=lambda:True)
    assert owner.faulted and path.read_bytes()==b'old generation'
    assert len(list(tmp_path.glob('ack.*.tmp')))==1
    with pytest.raises(ValueError,match='faulted'):
        owner.publish_ack(frame,path,authority_check=lambda:True)


def completed(tmp_path,context,monkeypatch):
    owner=exchange(context);frame=request(owner)
    monkeypatch.setattr(protocol,'_publish',lambda path,body,deadline:path.write_bytes(body))
    receipt=owner.publish_ack(frame,tmp_path/'ack',authority_check=lambda:True)
    return owner,receipt,(tmp_path/'ack').read_bytes()


def test_parallel_consume_yields_exactly_one_success_and_permanent_duplicate_fault(tmp_path,context,monkeypatch):
    owner,receipt,body=completed(tmp_path,context,monkeypatch)
    barrier=threading.Barrier(3);results=[]
    def consume():
        barrier.wait()
        try:results.append(owner.consume(body,receipt,authority_check=lambda:True))
        except ValueError:results.append(None)
    threads=[threading.Thread(target=consume) for _ in range(2)]
    for thread in threads:thread.start()
    barrier.wait()
    for thread in threads:thread.join(2);assert not thread.is_alive()
    assert sum(value is not None for value in results)==1
    assert owner.faulted and len(owner._receipts)==0


@pytest.mark.parametrize('case',['forged','other_instance','reconstructed','altered','authority'])
def test_local_receipt_cannot_be_reconstructed_replayed_or_imported(tmp_path,context,monkeypatch,case):
    owner,receipt,body=completed(tmp_path,context,monkeypatch)
    if case=='forged':receipt=protocol._Receipt()
    elif case=='other_instance':owner=exchange(context);request(owner)
    elif case=='reconstructed':receipt=type(receipt)()
    elif case=='altered':body=body.replace(b'ARCHIVE_SHM',b'ARCHIVE_WAL')
    with pytest.raises(ValueError):
        owner.consume(body,receipt,authority_check=lambda:case!='authority')
    assert owner.faulted


def test_receipt_has_no_mutable_body_and_cannot_be_serialized(tmp_path,context,monkeypatch):
    owner,receipt,body=completed(tmp_path,context,monkeypatch)
    with pytest.raises(AttributeError):receipt.body=body
    for operation in (lambda:copy.copy(receipt),lambda:pickle.dumps(receipt)):
        with pytest.raises(TypeError,match='not_serializable'):operation()
    assert owner.consume(body,receipt,authority_check=lambda:True)['sequence']==0


@pytest.mark.parametrize('field', ['sequence','schema_version'])
def test_bool_integer_equivalence_never_accepts_unpublished_ack_bytes(tmp_path,context,monkeypatch,field):
    owner,receipt,body=completed(tmp_path,context,monkeypatch)
    value=protocol.decode(body);value[field]=bool(value[field])
    forged=protocol.encoded(value)
    assert forged!=body and protocol.decode(forged)==protocol.decode(body)
    with pytest.raises(ValueError,match='ack_binding'):
        owner.consume(forged,receipt,authority_check=lambda:True)
    assert owner.faulted and len(owner._receipts)==0


def test_same_request_cannot_publish_twice(tmp_path,context,monkeypatch):
    owner,receipt,body=completed(tmp_path,context,monkeypatch)
    frame={**protocol.decode(body),'kind':'REQUEST'}
    monkeypatch.setattr(protocol,'_publish',lambda *args:pytest.fail('duplicate I/O'))
    with pytest.raises(ValueError,match='request_binding'):
        owner.publish_ack(frame,tmp_path/'second',authority_check=lambda:True)
    assert owner.faulted


def test_nonce_reuse_across_operations_is_permanently_rejected(tmp_path,context,monkeypatch):
    owner,receipt,body=completed(tmp_path,context,monkeypatch)
    owner.consume(body,receipt,authority_check=lambda:True)
    with pytest.raises(ValueError,match='request'):
        request(owner,1,nonce=f'{1:032x}')
    assert owner.faulted


def test_reentrant_consume_callback_cannot_create_same_sequence_request(tmp_path,context,monkeypatch):
    owner,receipt,body=completed(tmp_path,context,monkeypatch)
    def authority():
        with pytest.raises(ValueError,match='reentrant'):request(owner,0,nonce='e'*32)
        return True
    with pytest.raises(ValueError,match='faulted'):
        owner.consume(body,receipt,authority_check=authority)
    assert owner.faulted and owner._sequence==0 and owner._pending is None
    assert len(owner._receipts)==0


def test_caught_reentrant_publish_callback_fault_prevents_any_io(tmp_path,context,monkeypatch):
    owner=exchange(context);frame=request(owner)
    monkeypatch.setattr(protocol,'_publish',lambda *args:pytest.fail('I/O after nested fault'))
    def authority():
        with pytest.raises(ValueError,match='reentrant'):request(owner,0,nonce='e'*32)
        return True
    with pytest.raises(ValueError,match='faulted'):
        owner.publish_ack(frame,tmp_path/'ack',authority_check=authority)
    assert owner.faulted and len(owner._receipts)==0


def test_fsync_failure_has_no_visible_ack_and_no_retry(tmp_path,context,monkeypatch):
    owner=exchange(context);frame=request(owner);path=tmp_path/'ack';calls=[]
    def failed(fd):calls.append(fd);raise OSError('fixture fsync failure')
    monkeypatch.setattr(protocol.os,'fsync',failed)
    with pytest.raises(OSError):owner.publish_ack(frame,path,authority_check=lambda:True)
    assert len(calls)==1 and not path.exists() and owner.faulted
    assert len(list(tmp_path.glob('ack.*.tmp')))==1
