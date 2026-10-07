"""Bounded retries affect private report generations only."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
import storage_copy_worker as worker


def denial():
    error=PermissionError('fixture report lock');error.winerror=5
    return error


def pending(report,path):
    temp=path.with_name(path.name+'.'+report['nonce']+'.tmp')
    temp.write_bytes(json.dumps(report,sort_keys=True,indent=2,allow_nan=False).encode())
    raise denial()


def test_retry_publishes_only_same_complete_generation(tmp_path,monkeypatch):
    report={'nonce':'a'*32,'complete':False};path=tmp_path/'report.json';path.write_text('old')
    original=worker.os.replace;attempts=[]
    def replace(source,target):
        attempts.append(1)
        if len(attempts)<3:raise denial()
        original(source,target)
    monkeypatch.setattr(worker.reports,'save',pending);monkeypatch.setattr(worker.os,'replace',replace)
    worker.publish(report,path,worker.time.monotonic()+2)
    assert len(attempts)==3 and json.loads(path.read_text())==report


def test_permanent_lock_is_bounded_and_keeps_old_and_pending_reports(tmp_path,monkeypatch):
    report={'nonce':'a'*32};path=tmp_path/'report.json';path.write_text('old');clock=[100.]
    monkeypatch.setattr(worker.reports,'save',pending)
    monkeypatch.setattr(worker.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(worker.time,'sleep',lambda duration:clock.__setitem__(0,clock[0]+duration))
    monkeypatch.setattr(worker.os,'replace',lambda *args:(_ for _ in ()).throw(denial()))
    with pytest.raises(TimeoutError,match='publication_timeout'):worker.publish(report,path,100.1)
    assert path.read_text()=='old' and path.with_name(path.name+'.'+report['nonce']+'.tmp').exists()
    assert clock[0]<100.2


def test_unknown_pending_bytes_are_never_retried(tmp_path,monkeypatch):
    report={'nonce':'a'*32};path=tmp_path/'report.json'
    def wrong(report,path):
        path.with_name(path.name+'.'+report['nonce']+'.tmp').write_text('unexpected bytes')
        raise denial()
    monkeypatch.setattr(worker.reports,'save',wrong)
    with pytest.raises(ValueError,match='pending_report_mismatch'):
        worker.publish(report,path,worker.time.monotonic()+2)
    assert not path.exists()


def test_successful_save_after_deadline_is_rejected(tmp_path,monkeypatch):
    clock=[100.];path=tmp_path/'report.json'
    monkeypatch.setattr(worker.time,'monotonic',lambda:clock[0])
    def late(report,path):clock[0]=102.
    monkeypatch.setattr(worker.reports,'save',late)
    with pytest.raises(TimeoutError,match='report_deadline'):worker.publish({'nonce':'a'*32},path,101.)
