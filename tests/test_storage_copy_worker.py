"""Worker primitives on isolated files; production controller is never run."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest
from test_execution import flow

ROOT=Path(__file__).parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import storage_copy_worker as worker
from mykonos_meme_scout.store import SCHEMA


@pytest.mark.parametrize('body',[
    {'nonce':'b'*32,'pid':7}, {'nonce':'a'*32,'pid':8}, {'nonce':'a'*32,'pid':True},
    {'nonce':'a'*32,'pid':7,'extra':True},
])
def test_ack_rejects_other_identity_and_fields(tmp_path,body):
    path=tmp_path/'ack';path.write_text(json.dumps(body))
    with pytest.raises(ValueError):worker.ack(path,'a'*32,7,time.monotonic()+1)


def test_ack_accepts_exact_bounded_identity(tmp_path):
    path=tmp_path/'ack';path.write_text(json.dumps({'nonce':'a'*32,'pid':7}))
    assert worker.ack(path,'a'*32,7,time.monotonic()+1) is None


@pytest.mark.parametrize('body',[
    '{"nonce":"'+('a'*32)+'","pid":7,"pid":7}',
    '{"nonce":"'+('a'*32)+'","pid":NaN}', 'x'*1025,
])
def test_ack_rejects_duplicate_nonfinite_and_oversized_inputs(tmp_path,body):
    path=tmp_path/'ack';path.write_text(body)
    with pytest.raises(ValueError):worker.ack(path,'a'*32,7,time.monotonic()+1)


def test_protocol_requires_separate_fresh_ack_files(tmp_path):
    output=tmp_path/'report';a,b,c=(tmp_path/name for name in ('a','b','c'))
    assert worker.protocol_paths(output,(a,b,c)) is None
    with pytest.raises(ValueError):worker.protocol_paths(output,(a,a,c))
    with pytest.raises(ValueError):worker.protocol_paths(output,(a,b,output))
    b.write_text('old acknowledgement')
    with pytest.raises(ValueError):worker.protocol_paths(output,(a,b,c))


def test_no_ack_prevents_every_raw_and_work_access(tmp_path):
    scope=tmp_path/'scope';scope.mkdir();output=scope/'report.json'
    args=[]
    for key,value in {'database':tmp_path/'nonexistent-source.db','scope':scope,
        'ledger':tmp_path/'nonexistent-ledger.db','gate':tmp_path/'nonexistent-gate.json',
        'output':output,'nonce':'a'*32,'ack':scope/'ack','raw-ack':scope/'raw-ack','end-ack':scope/'end-ack'}.items():
        args+=['--'+key,str(value)]
    result=subprocess.run([sys.executable,'-I',str(ROOT/'scripts/storage_copy_worker.py'),*args,'--seconds','5'],
        capture_output=True,text=True,timeout=10)
    assert result.returncode==2,result.stderr
    report=json.loads(output.read_text())
    assert report['complete'] is False and report['stage']=='AWAIT_NATIVE_ACK'
    assert not (scope/'raw').exists() and not (scope/'work').exists()


@pytest.mark.skipif(os.name!='nt',reason='native guards')
@pytest.mark.parametrize('with_wal',[False,True])
def test_actual_raw_copy_and_guard_handoffs_preserve_original(flow,tmp_path,monkeypatch,with_wal):
    source=tmp_path/'isolated.db'
    if with_wal:
        code="""
import sys,os,sqlite3
sys.path.insert(0,sys.argv[2])
from mykonos_meme_scout.store import SCHEMA
c=sqlite3.connect(sys.argv[1]);c.executescript(SCHEMA)
c.execute('INSERT INTO snapshots VALUES(1,?,?,?,?,?)',('t','a','s',1,'raw payload'))
c.commit();os._exit(0)
"""
        result=subprocess.run([sys.executable,'-I','-c',code,str(source),str(ROOT/'CORE')],timeout=10)
        assert result.returncode==0 and Path(str(source)+'-wal').exists()
    else:
        with sqlite3.connect(source) as c:
            c.executescript(SCHEMA)
            c.execute("INSERT INTO snapshots VALUES(1,'t','a','s',1,'raw payload')")
        c.close()
    original={p.name:p.read_bytes() for p in [source,Path(str(source)+'-wal')] if p.exists()}
    scope=tmp_path/'scope';scope.mkdir();output=scope/'report.json'
    gate=tmp_path/'gate.json';gate.write_text(json.dumps({'schema_version':1,'OWNER_BROADCAST_GATE':'OFF'}))
    report={'schema_version':1,'nonce':'a'*32,'pid':os.getpid(),'phase':'RUNNING','complete':False}
    seen=[]
    def handoff(path,nonce,pid,deadline):
        current=json.loads(output.read_text());seen.append(current['stage'])
        guarded=[source,scope/'raw'/source.name]
        if with_wal:guarded.append(Path(str(source)+'-wal'))
        guarded.append(Path(str(scope/'raw'/source.name)+'-wal'))
        if current['stage']=='WORK_READY':guarded.append(scope/'work'/source.name)
        for member in guarded:
            with pytest.raises(PermissionError):member.open('r+b')
    monkeypatch.setattr(worker,'ack',handoff)
    worker.frozen_copy(source,scope,flow.store.path,gate,report,output,scope/'raw-ack',scope/'end-ack',time.monotonic()+20)
    assert seen==['RAW_READY','WORK_READY']
    assert report['builder']['source_row_proofs']==report['builder']['destination_row_proofs']
    assert report['source_before']==report['source_after']
    assert report['raw_before']==report['raw_after']
    assert report['raw_empty_wal_created'] is (not with_wal)
    if not with_wal:
        assert not Path(str(source)+'-wal').exists()
        assert Path(str(scope/'raw'/source.name)+'-wal').stat().st_size==0
    for name,data in original.items():
        assert (source.parent/name).read_bytes()==data
        assert (scope/'raw'/name).read_bytes()==data
    assert not worker.native._RETAINED_GUARDS


@pytest.mark.skipif(os.name!='nt',reason='native guards')
@pytest.mark.parametrize('fail_at',['RAW_READY','WORK_READY'])
@pytest.mark.parametrize('kind',['missing','wrong'])
def test_bad_handoff_never_reports_success_or_changes_original(flow,tmp_path,monkeypatch,fail_at,kind):
    source=tmp_path/'source.db'
    c=sqlite3.connect(source);c.executescript(SCHEMA);c.close()
    before=source.read_bytes()
    scope=tmp_path/'scope';scope.mkdir();output=scope/'report.json'
    gate=tmp_path/'gate.json';gate.write_text(json.dumps({'schema_version':1,'OWNER_BROADCAST_GATE':'OFF'}))
    report={'schema_version':1,'nonce':'a'*32,'pid':os.getpid(),'phase':'RUNNING','complete':False}
    actual_ack=worker.ack
    def handoff(path,nonce,pid,deadline):
        stage=json.loads(output.read_text())['stage']
        if stage!=fail_at:return
        if kind=='wrong':Path(path).write_text(json.dumps({'nonce':nonce,'pid':pid+1}))
        actual_ack(path,nonce,pid,min(deadline,time.monotonic()+.02))
    monkeypatch.setattr(worker,'ack',handoff)
    with pytest.raises((ValueError,TimeoutError)):
        worker.frozen_copy(source,scope,flow.store.path,gate,report,output,scope/'raw-ack',scope/'end-ack',time.monotonic()+10)
    assert source.read_bytes()==before and report['complete'] is False
    assert not worker.native._RETAINED_GUARDS
    if fail_at=='RAW_READY':assert not (scope/'work').exists()
    else:assert (scope/'work'/source.name).exists()
