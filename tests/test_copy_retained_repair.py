"""Actual recovery body and native file guards, isolated lifecycle doubles."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

ROOT=Path(__file__).parents[1]
NONCE='14fce188ea2946deadd61bfe660a6cbf'
BIRTH='2026-10-03T04:16:05.7548417Z'


def workspace(path,case):
    scope=path/'.local-tools'/('offline-audit-'+NONCE)
    proof=path/'.local-tools'/('retained-job-'+'d'*32)
    for folder in (path/'CORE/data',scope/'raw',scope/'work',proof,path/'RUNTIME',path/'scripts',
                   path/'TOOLS',path/'.local-tools/sqlite-tools-3530400'):
        folder.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(ROOT/'scripts/maintenance_state.py',path/'scripts/maintenance_state.py')
    main=b'original fixture';wal=b'wal fixture'
    for folder in (path/'CORE/data',scope/'raw'):
        (folder/'imc-main.db').write_bytes(main)
        (folder/'imc-main.db-wal').write_bytes(wal)
    (scope/'work/imc-main.db').write_bytes(b'candidate fixture')
    (scope/'empty.sql').write_bytes(b'')
    (path/'.local-tools/sqlite-tools-3530400/sqlite3.exe').write_bytes(b'fixture')
    if case=='hash':(scope/'raw/imc-main.db').write_bytes(b'changed fixture')
    (scope/'controller.json').write_text(json.dumps({'pid':10528,'nonce':NONCE,'phase':'RETAINED',
        'stage':'COPY','failure_code':'OFFLINE_AUDIT_WORKER_TIMEOUT','stopped':True,
        'cleanup_confirmed':False,'restart_allowed':False,'copy_worker_native_pid':9356}))
    (path/'RUNTIME/core-storage-copy.json').write_text(json.dumps({'pid':9356,'nonce':NONCE,
        'phase':'FINAL','stage':'BUILD','complete':False,'cleanup_confirmed':False,
        'code':'COPY_PROOF_FAILED','exception_kind':'TimeoutError','source_sha256':{
            'main':hashlib.sha256(main).hexdigest(),'wal':hashlib.sha256(wal).hexdigest()}}))
    (proof/'intent.json').write_text('{}')
    mutex={'Count':0,'Owned':True,'Abandoned':False,'OwnerProcess':10528,
           'OwnerThread':18008,'CallerThread':18008}
    (proof/'done.json').write_text(json.dumps({'schema_version':1,'pid':10528,'birth':BIRTH,
        'copy_nonce':NONCE,'native_cleanup_confirmed':True,'copy_complete':False,
        'restart_allowed':False,'maintenance_state':'ACTIVE','streams_held':6,
        'code':{'sha256':'6caed580cf47b2fec19ee6b6506d556b4ca8c34609723bd0137090109d619493',
                'type_count':11,'proof_bytes':142231},'mutexes':[mutex]*3}))
    (path/'RUNTIME/maintenance-state.json').write_text(json.dumps({'schema_version':1,'state':'ACTIVE',
        'nonce':NONCE,'owner_pid':10528,'owner_birth':BIRTH,'updated_at':'2026-10-03T04:16:07Z',
        'reason':'OFFLINE_AUDIT','proof':None}))
    return path


@pytest.mark.skipif(os.name!='nt',reason='Windows guarded operator')
@pytest.mark.parametrize('case',['success','identity','proof','hash','exit','mutex','generation','validator'])
def test_copy_timeout_handover_never_releases_or_restarts(tmp_path,case):
    path=workspace(tmp_path,case)
    original=(path/'CORE/data/imc-main.db').read_bytes()
    child=subprocess.Popen(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
        '-File',str(ROOT/'tests/copy_retained_repair_fixture.ps1'),'-Workspace',str(path),
        '-Python',sys._base_executable,'-Case',case],stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        text=True,creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        deadline=time.monotonic()+10
        ready=path/'held-ready'
        while not ready.exists() and time.monotonic()<deadline:
            assert child.poll() is None,child.communicate()
            time.sleep(.01)
        assert ready.exists(), 'operator did not retain its resources'
        record=json.loads(next((path/'.local-tools').glob('retained-repair-*/repair.json')).read_text())
        assert record['restart_allowed'] is False and record['cleanup_confirmed'] is False
        assert record['phase']==('VERIFIED_HELD' if case=='success' else 'RETAINED')
        assert json.loads((path/'RUNTIME/maintenance-state.json').read_text())['state']=='ACTIVE'
        assert (path/'CORE/data/imc-main.db').read_bytes()==original
        calls=(path/'calls').read_text().splitlines() if (path/'calls').exists() else []
        if case in ('identity','proof','hash'):
            assert 'old.kill' not in calls and 'verify' not in calls
        else:
            assert calls.index('old.kill')<calls.index('old.exit')
        if case=='success':
            assert calls.count('mutex.takeover')==3
            assert calls.index('old.exit')<calls.index('verify')
            done=json.loads(next((path/'.local-tools').glob('retained-repair-*/handover-done.json')).read_text())
            assert done['state']=='ACTIVE' and done['restart_allowed'] is False
        if case in ('exit','mutex','generation'):
            assert 'verify' not in calls
    finally:
        if child.poll() is None:child.kill()
        child.communicate(timeout=3)
