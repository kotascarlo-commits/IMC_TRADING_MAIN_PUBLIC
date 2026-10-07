"""Exercise actual controller functions on isolated data; no production STOP."""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest
from test_execution import flow
from mykonos_meme_scout.store import SCHEMA

ROOT=Path(__file__).parents[1]
pytestmark=pytest.mark.skipif(os.name!='nt',reason='Windows native Job ownership')


@pytest.mark.parametrize('with_wal',[False,True])
@pytest.mark.parametrize('operation',['Copy','Retained','Certified'])
def test_real_native_copy_jobs_parent_guards_and_independent_validator(flow,tmp_path,with_wal,operation):
    source=tmp_path/'source.db'
    if with_wal:
        code="""
import sys,sqlite3,os
sys.path.insert(0,sys.argv[2])
from mykonos_meme_scout.store import SCHEMA
c=sqlite3.connect(sys.argv[1]);c.executescript(SCHEMA)
c.execute('INSERT INTO snapshots VALUES(3,?,?,?,?,?)',('t','a','s',1,'raw'))
c.commit();os._exit(0)
"""
        assert subprocess.run([sys.executable,'-I','-c',code,str(source),str(ROOT/'CORE')],timeout=10).returncode==0
    else:
        c=sqlite3.connect(source);c.executescript(SCHEMA)
        c.execute("INSERT INTO snapshots VALUES(3,'t','a','s',1,'raw')");c.commit();c.close()
    original={path.name:path.read_bytes() for path in [source,Path(str(source)+'-wal')] if path.exists()}
    shutil.copyfile(flow.store.path,tmp_path/'ledger.db')
    (tmp_path/'gate.json').write_text(json.dumps({'schema_version':1,'OWNER_BROADCAST_GATE':'OFF'}))
    command=['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',
        str(Path(__file__).with_name('storage_copy_controller_fixture.ps1')),'-Workspace',str(tmp_path),'-Python',sys.executable,'-Case',operation]
    result=subprocess.run(command,capture_output=True,text=True,timeout=45)
    assert result.returncode==0,result.stdout+result.stderr
    if operation=='Certified':
        assert 'separate native Jobs + protected FINAL reports PASS' in result.stdout
        done=json.loads(next((tmp_path/'.local-tools').glob('retained-repair-*/done.json')).read_text())
        assert done['phase']=='CERTIFICATION_EVIDENCE' and done['complete'] is done['authority_grant'] is False
        assert done['proof_exit']==done['validation_exit']==0
        assert done['proof_native_cleanup'] is done['validation_native_cleanup'] is True
        assert done['publication_allowed'] is done['restart_allowed'] is False
        for name,body in original.items():assert (tmp_path/name).read_bytes()==body
        return
    assert 'independent validation PASS' in result.stdout
    for name,body in original.items():assert (tmp_path/name).read_bytes()==body
    report_path=(tmp_path/'controller/copy.json') if operation=='Copy' else next((tmp_path/'.local-tools').glob('offline-audit-*/copy.json'))
    report=json.loads(report_path.read_text())
    if operation=='Copy':
        assert report['complete'] and report['cleanup_confirmed'] and report['phase']=='FINAL'
    else:
        assert report['complete'] is False and report['code']=='COPY_PROOF_FAILED'
        assert 'retained proof + new native ACKs' in result.stdout
        proof=json.loads(next((tmp_path/'.local-tools').glob('retained-repair-*/copy-proof.json')).read_text())
        assert proof['complete'] is True and proof['kind']=='RETAINED_READONLY_PROOF'
        assert proof['old_copy_nonce']==report['nonce'] and proof['nonce']!=report['nonce']
    assert report['raw_empty_wal_created'] is (not with_wal)


@pytest.mark.parametrize('fault',['ReportProof','AckProof','ObserveProof',
    'ReportValidation','AckValidation','ObserveValidation'])
@pytest.mark.parametrize('operation',['Retained','Certified'])
def test_retained_exceptions_close_actual_native_jobs_and_keep_parent_guards(flow,tmp_path,fault,operation):
    source=tmp_path/'source.db'
    c=sqlite3.connect(source)
    try:c.executescript(SCHEMA);c.commit()
    finally:c.close()
    before=source.read_bytes()
    shutil.copyfile(flow.store.path,tmp_path/'ledger.db')
    (tmp_path/'gate.json').write_text(json.dumps({'schema_version':1,'OWNER_BROADCAST_GATE':'OFF'}))
    command=['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',
        str(Path(__file__).with_name('storage_copy_controller_fixture.ps1')),'-Workspace',str(tmp_path),
        '-Python',sys._base_executable,'-Case',operation,'-Fault',fault]
    result=subprocess.run(command,capture_output=True,text=True,timeout=45)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'exception native cleanup + guards held PASS' in result.stdout
    assert source.read_bytes()==before


@pytest.mark.parametrize('fault',['AuthorityAck','Budget','BudgetAck','WorkBudgetAck','AfterEvidence','Shutdown'])
def test_certification_authority_deadline_and_unknown_cleanup(flow,tmp_path,fault):
    source=tmp_path/'source.db';connection=sqlite3.connect(source)
    try:connection.executescript(SCHEMA);connection.commit()
    finally:connection.close()
    original=source.read_bytes();shutil.copyfile(flow.store.path,tmp_path/'ledger.db')
    (tmp_path/'gate.json').write_text(json.dumps({'schema_version':1,'OWNER_BROADCAST_GATE':'OFF'}))
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',
        str(ROOT/'tests/storage_copy_controller_fixture.ps1'),'-Workspace',str(tmp_path),
        '-Python',sys._base_executable,'-Case','Certified','-Fault',fault],capture_output=True,timeout=45,
        creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode==0,result.stdout.decode(errors='replace')+result.stderr.decode(errors='replace')
    failure=json.loads((tmp_path/'certificate-failure.json').read_text())
    assert failure['faulted'] is True and failure['complete'] is False
    assert source.read_bytes()==original
    records=list((tmp_path/'.local-tools').glob('retained-repair-*/done.json'))
    if fault=='AfterEvidence':
        assert len(records)==1
        done=json.loads(records[0].read_text())
        assert done['complete'] is done['authority_grant'] is done['publication_allowed'] is False
        assert failure['cleanup_status']==['CONFIRMED','CONFIRMED']
    else:assert not records
    if fault=='Shutdown':
        assert b'expected cleanup UNKNOWN; guards retained PASS' in result.stdout
        assert failure['cleanup_status']==['ATTEMPTED_UNCONFIRMED']
        assert failure['primary_failure']==failure['cleanup_failure']==[True]
    elif fault=='AuthorityAck':assert failure['cleanup_status']==['CONFIRMED']
    elif fault=='WorkBudgetAck':
        assert failure['cleanup_status']==['CONFIRMED'] and failure['cleanup_failure']==[False]
    elif fault=='BudgetAck':
        assert failure['cleanup_status']==['NOT_ATTEMPTED_NO_BUDGET'] and failure['cleanup_failure']==[False]
    elif fault=='Budget':assert failure['jobs']==0


def test_expensive_preflight_precedes_worker_ack_wait(flow,tmp_path):
    source=tmp_path/'source.db';connection=sqlite3.connect(source)
    try:connection.executescript(SCHEMA);connection.commit()
    finally:connection.close()
    shutil.copyfile(flow.store.path,tmp_path/'ledger.db')
    (tmp_path/'gate.json').write_text(json.dumps({'schema_version':1,'OWNER_BROADCAST_GATE':'OFF'}))
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',
        str(ROOT/'tests/storage_copy_controller_fixture.ps1'),'-Workspace',str(tmp_path),
        '-Python',sys._base_executable,'-Case','Certified','-Fault','DelayedPreflight'],capture_output=True,timeout=45,
        creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode==0,result.stdout.decode(errors='replace')+result.stderr.decode(errors='replace')
    assert b'short ACK check PASS' in result.stdout


@pytest.mark.parametrize('mutation',[
    None,('pid','123'),('schema_version',True),('schema_version','1'),('schema_version',2),
    ('schema_version',None),('complete','True'),('complete',1),('cleanup_confirmed','True'),
    ('code','unexpected_error'),('result.complete','True'),('result.independent_comparison',1),
    ('result.source_unchanged',None),('result.eligible_for_publication','False'),('result.logical_tables','11'),
    ('result.semantic_orphans',False),('result.semantic_orphans',-1),('result',None),
])
def test_actual_completion_contract_rejects_malformed_types_and_missing_fields(tmp_path,mutation):
    value={'schema_version':1,'nonce':'a'*32,'pid':123,'phase':'FINAL','complete':True,'cleanup_confirmed':True,
        'result':{'complete':True,'independent_comparison':True,'logical_tables':11,'source_unchanged':True,
            'raw_verified':True,'target_verified':True,'eligible_for_publication':False,'semantic_orphans':0}}
    if mutation:
        key,replacement=mutation;target=value
        if key.startswith('result.'):key=key.split('.')[1];target=value['result']
        if replacement is None:target.pop(key)
        else:target[key]=replacement
    (tmp_path/'final.json').write_text(json.dumps(value))
    command=['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',
        str(Path(__file__).with_name('storage_copy_controller_fixture.ps1')),'-Workspace',str(tmp_path),
        '-Python',sys.executable,'-Case','Validate']
    result=subprocess.run(command,capture_output=True,text=True,timeout=10)
    assert result.returncode==(0 if mutation is None else 2),result.stdout+result.stderr
