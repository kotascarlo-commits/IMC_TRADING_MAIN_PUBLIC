"""Independent validator fixtures; no original controller invocation."""
import copy
from contextlib import ExitStack,closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

import pytest
from test_execution import flow
from mykonos_meme_scout.store import SCHEMA

sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
import storage_copy_contract as contract
import storage_copy_worker as worker

pytestmark=pytest.mark.skipif(os.name!='nt',reason='actual guarded Windows files')


@pytest.fixture
def completed(flow,tmp_path,monkeypatch,request):
    source=tmp_path/'source.db'
    c=sqlite3.connect(source);c.executescript(SCHEMA)
    c.execute("INSERT INTO snapshots VALUES(19,'column','a','s',1,'raw payload')")
    c.execute("INSERT INTO model_registry(rowid,model_family,model_version,created_at,status,config_json) VALUES(99,'f','v','time','SHADOW','raw')")
    c.commit();c.close()
    scope=tmp_path/'scope';scope.mkdir();output=scope/'report.json'
    gate=tmp_path/'gate.json';gate.write_text(json.dumps({'schema_version':1,'OWNER_BROADCAST_GATE':'OFF'}))
    value={'schema_version':1,'nonce':'a'*32,'pid':os.getpid(),'phase':'RUNNING',
        'complete':False,'cleanup_confirmed':False,'budget_seconds':7200.}
    monkeypatch.setattr(worker,'ack',lambda *args:None)
    worker.frozen_copy(source,scope,flow.store.path,gate,value,output,scope/'raw-ack',scope/'end-ack',time.monotonic()+10)
    value.update(phase='FINAL',stage='FINAL',complete=True,cleanup_confirmed=True)
    mutation=getattr(request,'param',None)
    if mutation is not None:
        target=scope/'work'/source.name
        from mykonos_meme_scout.storage_codec import dictionary_key
        with closing(sqlite3.connect(target)) as altered:
            digest=dictionary_key('unused') if mutation=='unused' else 'invalid'
            altered.execute('INSERT INTO _prediction_reasons VALUES(999,?,?)',(digest,'unused'))
            altered.commit()
        # A forged report can contain the correct current file hash and metadata
        # while public rows remain identical. Physical proof must still reject.
        value['target']=worker.reports.state(target)
        value['target_sha256']=worker.offline_ledger.hash_file(target,time.monotonic()+5)
        value['builder']['target_bytes']=target.stat().st_size
    # These are primitive fixtures, not proof of the production parent's handoff.
    paths=[source,scope/'raw'/source.name,Path(str(scope/'raw'/source.name)+'-wal'),scope/'work'/source.name]
    with worker.native.ReadFiles(paths,deadline=time.monotonic()+30):
        yield source,scope,value


def validate(completed,report=None):
    source,scope,value=completed
    return contract.validate_final(value if report is None else report,source=source,scope=scope,
        nonce='a'*32,worker_pid=os.getpid(),exit_code=0,deadline=time.monotonic()+10)


def test_actual_independent_bytes_and_every_logical_row(completed):
    source,scope,value=completed
    result=validate(completed)
    assert result['complete'] and result['independent_comparison']
    assert result['logical_tables']==11 and result['source_unchanged']
    assert not result['eligible_for_publication']
    assert result['semantic_orphans']==0


@pytest.mark.parametrize('completed',['unused','bad_digest'],indirect=True)
def test_independent_verifier_rejects_hidden_data_despite_current_hashes(completed):
    with pytest.raises(ValueError,match='compact_dictionary_(unused_entry|digest_mismatch)'):
        validate(completed)


def retained_origin(completed):
    value=copy.deepcopy(completed[2])
    value.update(phase='FINAL',stage='BUILD',complete=False,cleanup_confirmed=False,
                 code='COPY_PROOF_FAILED',exception_kind='TimeoutError')
    return value


def candidate(completed,flow,origin=None,deadline=None):
    source,scope,_=completed
    return contract.prove_retained_candidate(retained_origin(completed) if origin is None else origin,
        source=source,scope=scope,old_nonce='a'*32,nonce='b'*32,pid=os.getpid(),
        ledger=flow.store.path,gate=source.parent/'gate.json',
        deadline=time.monotonic()+10 if deadline is None else deadline)


def test_retained_candidate_is_fully_reproved_and_independently_verified(completed,flow):
    source,scope,original=completed
    files=[source,scope/'raw'/source.name,scope/'work'/source.name]
    before=[path.read_bytes() for path in files]
    origin=retained_origin(completed)
    result=candidate(completed,flow,origin)
    assert origin['complete'] is False and origin['code']=='COPY_PROOF_FAILED'
    assert [path.read_bytes() for path in files]==before
    report={'schema_version':1,'nonce':'b'*32,'pid':os.getpid(),'phase':'FINAL','stage':'FINAL',
            'complete':True,'cleanup_confirmed':True,'budget_seconds':21600.,**result}
    validated=contract.validate_final(report,source=source,scope=scope,nonce='b'*32,
        worker_pid=os.getpid(),exit_code=0,deadline=time.monotonic()+10)
    assert validated['complete'] and not validated['eligible_for_publication']


@pytest.mark.parametrize('key,value',[('nonce','c'*32),('phase','RUNNING'),('complete',True),
    ('cleanup_confirmed',1),('pid',True),('exception_kind','ValueError'),('stage','FINAL')])
def test_retained_origin_requires_exact_failed_generation(completed,flow,key,value):
    origin=retained_origin(completed);origin[key]=value
    with pytest.raises(ValueError,match='origin_invalid'):candidate(completed,flow,origin)


@pytest.mark.parametrize('seconds',[True,float('nan'),float('inf'),-1,21601])
def test_retained_budget_rejects_unbounded_or_invalid_values(completed,flow,seconds):
    deadline=seconds if type(seconds)is bool else time.monotonic()+seconds
    with pytest.raises(ValueError,match='budget'):candidate(completed,flow,deadline=deadline)


def test_retained_six_hour_budget_is_accepted_by_both_verifiers(completed,flow):
    result=candidate(completed,flow,deadline=time.monotonic()+21600)
    source,scope,_=completed
    report={'schema_version':1,'nonce':'b'*32,'pid':os.getpid(),'phase':'FINAL','stage':'FINAL',
        'complete':True,'cleanup_confirmed':True,'budget_seconds':21600.,**result}
    assert contract.validate_final(report,source=source,scope=scope,nonce='b'*32,
        worker_pid=os.getpid(),exit_code=0,deadline=time.monotonic()+21600)['complete']


@pytest.mark.parametrize('protected',['source','raw','work','report'])
def test_cli_never_publishes_failure_over_protected_inputs(completed,tmp_path,monkeypatch,protected):
    source,scope,value=completed
    report=tmp_path/'input.json';report.write_text(json.dumps(value))
    paths={'source':source,'raw':scope/'raw'/source.name,'work':scope/'work'/source.name,'report':report}
    destination=paths[protected];before=destination.read_bytes()
    monkeypatch.setattr(sys,'argv',['storage_copy_contract.py','--report',str(report),'--source',str(source),
        '--scope',str(scope),'--nonce','a'*32,'--worker-pid',str(os.getpid()),'--exit-code','0',
        '--seconds','10','--output',str(destination),'--ack',str(tmp_path/'ack.json')])
    assert contract.main()==2
    assert destination.read_bytes()==before
    assert not Path(str(destination)+'.'+'a'*32+'.tmp').exists()


@pytest.mark.parametrize('budget',[True,21601,float('inf'),float('nan')])
def test_independent_verifier_rejects_invalid_report_budget(completed,budget):
    report=copy.deepcopy(completed[2]);report['budget_seconds']=budget
    with pytest.raises(ValueError,match='report_budget'):validate(completed,report)


@pytest.mark.parametrize('field,value',[
    ('schema_version',True),('pid',True),('nonce','b'*32),('phase','RUNNING'),('stage','WORK_READY'),
    ('complete',False),('complete',1),('cleanup_confirmed',False),('budget_seconds',True),
    ('raw_empty_wal_created',False),('target_sha256','0'*64),
])
def test_partial_unknown_spoofed_headers_never_pass(completed,field,value):
    report=copy.deepcopy(completed[2]);report[field]=value
    with pytest.raises(ValueError):validate(completed,report)


def test_equal_forged_row_proofs_are_rejected_by_fresh_sql_reads(completed):
    report=copy.deepcopy(completed[2])
    for key in ('source_row_proofs','destination_row_proofs'):
        report['builder'][key]['snapshots']['sha256']='0'*64
    with pytest.raises(ValueError,match='independent_row'):validate(completed,report)


def test_missing_empty_table_proofs_are_rejected(completed):
    report=copy.deepcopy(completed[2])
    for key in ('source_row_proofs','destination_row_proofs'):report['builder'][key].pop('trades')
    with pytest.raises(ValueError,match='inventory'):validate(completed,report)


def test_reference_counts_are_independently_recomputed(completed):
    report=copy.deepcopy(completed[2]);report['builder']['semantic_reference_counts']['trades.position_id']=1
    with pytest.raises(ValueError,match='independent_reference'):validate(completed,report)


def test_source_raw_bytes_must_match_report(completed):
    report=copy.deepcopy(completed[2]);report['source_sha256']['main']='0'*64
    with pytest.raises(ValueError,match='source_raw_hash'):validate(completed,report)


def test_boolean_safety_counts_rejected(completed):
    report=copy.deepcopy(completed[2]);report['safety_before']['positions']=False
    with pytest.raises(ValueError,match='safety'):validate(completed,report)


def test_oversized_duplicate_or_nonfinite_report_rejected(tmp_path):
    path=tmp_path/'report'
    for body in ('x'*131073,'{"pid":1,"pid":2}','{"pid":NaN}'):
        path.write_text(body)
        with pytest.raises(ValueError):contract.read_report(path)


@pytest.mark.parametrize('operation,stage',[
    ('validate','INITIAL_PUBLISH'),('validate','WAIT_ACK'),('validate','VALIDATE'),
    ('validate','FINAL_PUBLISH'),('prove-retained','INITIAL_PUBLISH'),
    ('prove-retained','WAIT_ACK'),('prove-retained','PROVE'),
    ('prove-retained','PROGRESS_PUBLISH'),('prove-retained','FINAL_PUBLISH')])
def test_failure_diagnostics_identify_stage_without_secret_messages(tmp_path,monkeypatch,operation,stage):
    root=tmp_path;old_nonce='a'*32;nonce='b'*32
    source=root/'source.db';source.write_bytes(b'fixture')
    scope=root/'.local-tools'/('offline-audit-'+old_nonce);scope.mkdir(parents=True)
    output=root/'.local-tools'/('retained-repair-'+nonce)/'copy-proof.json'
    output.parent.mkdir();ack=output.parent/'copy-proof-seen.json'
    report=root/'input.json';report.write_text('{}')
    arguments=['storage_copy_contract.py','--operation',operation,'--root',str(root),
        '--old-nonce',old_nonce,'--ledger',str(root/'ledger'),'--gate',str(root/'gate'),
        '--report',str(report),'--source',str(source),'--scope',str(scope),'--nonce',nonce,
        '--worker-pid','7','--exit-code',('2' if operation=='prove-retained' else '0'),
        '--seconds','10','--output',str(output),'--ack',str(ack)]
    monkeypatch.setattr(sys,'argv',arguments)
    monkeypatch.setattr(contract,'read_report',lambda _: {'pid':7})
    calls=[]
    def fail():raise ValueError('fixture_SECRET_NEVER_OUTPUT')
    def publish(value,*args):
        calls.append(dict(value))
        if ((stage=='INITIAL_PUBLISH' and len(calls)==1)
                or (stage=='PROGRESS_PUBLISH' and len(calls)==2)
                or (stage=='FINAL_PUBLISH' and value['phase']=='FINAL')):fail()
    def acknowledge(*args):
        if stage=='WAIT_ACK':fail()
    def validate(*args,**kwargs):
        if stage=='VALIDATE':fail()
        return {'complete':True}
    def prove(*args,**kwargs):
        if stage=='PROVE':fail()
        kwargs['progress']({'table':'fixture'})
        return {'complete':True}
    monkeypatch.setattr(contract,'publish',publish)
    monkeypatch.setattr(contract,'ack',acknowledge)
    monkeypatch.setattr(contract,'validate_final',validate)
    monkeypatch.setattr(contract,'prove_retained_candidate',prove)
    assert contract.main()==2
    body=output.read_bytes();value=json.loads(body)
    expected='FINAL_PUBLISH' if stage in ('INITIAL_PUBLISH','PROGRESS_PUBLISH') else stage
    assert value['failure_stage']==expected and value['exception_kind']=='ValueError'
    assert value['complete'] is False and value['cleanup_confirmed'] is False
    assert value['code']=='COPY_VALIDATION_FAILED' and b'SECRET' not in body
