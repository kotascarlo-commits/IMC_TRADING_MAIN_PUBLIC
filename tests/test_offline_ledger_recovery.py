"""Actual hot journals on isolated ledgers; never touch production data."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest
from test_execution import flow

ROOT=Path(__file__).parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import offline_ledger as recovery

pytestmark=pytest.mark.skipif(os.name!='nt',reason='native file guards')


def fixture(flow,tmp_path,*,hot=True):
    flow.buy()
    source_dir=tmp_path/'source';source_dir.mkdir()
    path=source_dir/'execution-ledger.db'
    shutil.copyfile(flow.store.path,path)
    gate=tmp_path/'gate.json';gate.write_text(json.dumps({'schema_version':1,'OWNER_BROADCAST_GATE':'OFF'}))
    baseline=recovery.checked_database(path,gate,time.monotonic()+5)
    if hot:
        code="""
import sqlite3,sys,os,json
c=sqlite3.connect(sys.argv[1]);c.execute('PRAGMA synchronous=FULL');c.execute('PRAGMA cache_size=2')
c.execute('BEGIN IMMEDIATE');c.execute('UPDATE meta SET value=? WHERE key=?',('999','schema_version'))
for n in range(400):c.execute('INSERT INTO audit(at,trade_id,action,doc) VALUES(?,?,?,?)',('fixture',None,'uncommitted',json.dumps({'fixture':'x'*4000})))
os._exit(0)
"""
        child=subprocess.run([sys.executable,'-I','-c',code,str(path)],timeout=5)
        assert child.returncode==0
        with pytest.raises(sqlite3.Error) as caught:recovery.connect(path)
        assert caught.value.sqlite_errorcode==776
    return path,gate,baseline


def pair(path):
    return {p.name:p.read_bytes() for p in (path,Path(str(path)+'-journal'))}


def test_real_rollback_preserves_raw_pair_and_matches_work_exactly(flow,tmp_path):
    path,gate,baseline=fixture(flow,tmp_path);original=pair(path)
    result=recovery.recover_quiesced(path,gate,tmp_path/'backup')
    assert result['recovered'] and result['complete'] and result['cleanup_confirmed']
    assert result['integrity'] and result['safety']==baseline['safety']
    assert result['logical_sha256']==baseline['logical_sha256']
    assert result['main_sha256']==baseline['main_sha256']
    assert not Path(str(path)+'-journal').exists()
    for name,content in original.items():assert (tmp_path/'backup/raw'/name).read_bytes()==content
    assert recovery.checked_database(path,gate,time.monotonic()+5)==baseline


def test_healthy_quiesced_ledger_never_opens_original_writable(flow,tmp_path,monkeypatch):
    path,gate,baseline=fixture(flow,tmp_path,hot=False)
    real=recovery.connect
    def read_only(*args,**kwargs):
        assert not kwargs.get('writable');return real(*args,**kwargs)
    monkeypatch.setattr(recovery,'connect',read_only)
    result=recovery.recover_quiesced(path,gate,tmp_path/'unused')
    assert not result['recovered'] and result['logical_sha256']==baseline['logical_sha256']
    assert not (tmp_path/'unused').exists()


def test_copy_validation_failure_leaves_original_and_journal_untouched(flow,tmp_path,monkeypatch):
    path,gate,_=fixture(flow,tmp_path);original=pair(path)
    real=recovery.checked_database
    def bad_work(candidate,*args):
        if Path(candidate).parent.name=='work':raise ValueError('fixture_copy_failure')
        return real(candidate,*args)
    monkeypatch.setattr(recovery,'checked_database',bad_work)
    with pytest.raises(ValueError,match='fixture_copy_failure'):recovery.recover_quiesced(path,gate,tmp_path/'backup')
    assert pair(path)==original


def test_interruption_before_original_writes_keeps_source_and_raw_backup(flow,tmp_path):
    path,gate,_=fixture(flow,tmp_path);original=pair(path)
    def interrupt():raise RuntimeError('fixture_interruption')
    with pytest.raises(RuntimeError,match='fixture_interruption'):
        recovery.recover_quiesced(path,gate,tmp_path/'backup',before_original=interrupt)
    assert pair(path)==original
    for name,content in original.items():assert (tmp_path/'backup/raw'/name).read_bytes()==content


@pytest.mark.parametrize('kind',['super','bad_header','wal','gate_on','wrong_error'])
def test_recovery_rejects_unproved_source_conditions(flow,tmp_path,kind):
    path,gate,_=fixture(flow,tmp_path)
    journal=Path(str(path)+'-journal')
    if kind=='super':
        with journal.open('ab') as stream:stream.write(b'\0'*8+recovery.MAGIC)
    elif kind=='bad_header':
        data=journal.read_bytes();journal.write_bytes(b'\0'*8+data[8:])
    elif kind=='wal':Path(str(path)+'-wal').write_bytes(b'fixture')
    elif kind=='gate_on':gate.write_text(json.dumps({'schema_version':1,'OWNER_BROADCAST_GATE':'ON'}))
    elif kind=='wrong_error':path.write_bytes(b'not a database')
    original=pair(path)
    with pytest.raises((ValueError,sqlite3.Error)):recovery.recover_quiesced(path,gate,tmp_path/'backup')
    assert pair(path)==original


def test_source_hash_change_blocks_original_recovery(flow,tmp_path,monkeypatch):
    path,gate,_=fixture(flow,tmp_path);original=pair(path)
    real=recovery.hash_file;calls=0
    def changed(candidate,deadline):
        nonlocal calls
        if Path(candidate)==path:
            calls+=1
            if calls>=3:return 'f'*64
        return real(candidate,deadline)
    monkeypatch.setattr(recovery,'hash_file',changed)
    with pytest.raises(ValueError):recovery.recover_quiesced(path,gate,tmp_path/'backup')
    assert pair(path)==original


def test_original_post_recovery_mismatch_never_reports_complete(flow,tmp_path,monkeypatch):
    path,gate,_=fixture(flow,tmp_path)
    real=recovery.checked_database
    def changed(candidate,*args):
        result=real(candidate,*args)
        if Path(candidate)==path:result['logical_sha256']='f'*64
        return result
    monkeypatch.setattr(recovery,'checked_database',changed)
    with pytest.raises(ValueError,match='recovery_original_mismatch'):
        recovery.recover_quiesced(path,gate,tmp_path/'backup')
    assert (tmp_path/'backup/raw'/path.name).exists()


def test_actual_controller_native_worker_ack_and_final_revalidation(flow,tmp_path):
    path,gate,baseline=fixture(flow,tmp_path)
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',
        str(ROOT/'tests/offline_controller_fixture.ps1'),'-Workspace',str(tmp_path),'-Python',sys.executable,'-Case','recover'],
        capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stdout+result.stderr
    assert result.stdout.strip()=='isolated owned recovery PASS'
    assert recovery.checked_database(path,gate,time.monotonic()+5)==baseline
    report=json.loads((tmp_path/'controller-scope/ledger-recovery.json').read_text())
    assert report['phase']=='FINAL' and report['complete'] and report['cleanup_confirmed']
    assert report['recovered'] and type(report['pid'])is int


def test_missing_native_ack_never_reaches_source_recovery(flow,tmp_path):
    path,gate,_=fixture(flow,tmp_path);original=pair(path)
    result=subprocess.run([sys.executable,'-I',str(ROOT/'scripts/offline_ledger.py'),'recover',
        '--ledger',str(path),'--gate',str(gate),'--backup',str(tmp_path/'backup'),
        '--output',str(tmp_path/'report.json'),'--nonce','a'*32,'--ack',str(tmp_path/'missing-ack')],
        capture_output=True,text=True,timeout=10)
    assert result.returncode==2 and not result.stdout and not result.stderr
    assert pair(path)==original and not (tmp_path/'backup').exists()
    report=json.loads((tmp_path/'report.json').read_text())
    assert report['phase']=='FINAL' and report['complete']is False and report['cleanup_confirmed']is False


def test_crash_after_original_rollback_before_validation_keeps_start_blocked(flow,tmp_path):
    from test_maintenance_state import workspace,run,idle
    workspace(tmp_path)
    path,gate,baseline=fixture(flow,tmp_path)
    code="""
import sys,os
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import offline_ledger as recovery
import maintenance_state
source=Path(sys.argv[2]);gate=Path(sys.argv[3]);scope=Path(sys.argv[4])
marker={'schema_version':1,'state':'ACTIVE','nonce':'a'*32,'owner_pid':os.getpid(),
 'owner_birth':'2026-10-03T00:00:00Z','updated_at':'2026-10-03T00:00:01Z','reason':'RECOVERY_REQUIRED','proof':None}
maintenance_state.write(scope/'RUNTIME/maintenance-state.json',marker)
real=recovery.connect
def interrupted(candidate,**kwargs):
 connection=real(candidate,**kwargs)
 if Path(candidate)==source and kwargs.get('writable'):os._exit(37)
 return connection
recovery.connect=interrupted
recovery.recover_quiesced(source,gate,scope/'backup')
"""
    child=subprocess.run([sys.executable,'-I','-c',code,str(ROOT/'scripts'),str(path),str(gate),str(tmp_path)],
        capture_output=True,text=True,timeout=8)
    assert child.returncode==37,child.stdout+child.stderr
    assert (tmp_path/'backup/raw'/path.name).exists()
    assert recovery.checked_database(path,gate,time.monotonic()+5)==baseline
    # Recovered bytes alone cannot clear a crash's unverified maintenance state.
    result=run(tmp_path,'start')
    assert result.returncode==2 and 'IMC_MAINTENANCE_RECOVERY_REQUIRED' in result.stdout
