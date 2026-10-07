"""Persistent markers and actual native START/RUN fences, isolated fixtures."""
import ctypes
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT=Path(__file__).parents[1]
spec=importlib.util.spec_from_file_location('maintenance_state',ROOT/'scripts/maintenance_state.py')
state=importlib.util.module_from_spec(spec);spec.loader.exec_module(state)


def idle():
    return {'schema_version':1,'state':'IDLE','nonce':'a'*32,'owner_pid':42,
        'owner_birth':'2026-10-03T00:00:00Z','updated_at':'2026-10-03T00:00:01Z',
        'reason':'VERIFIED_CLEANUP','proof':{'integrity':True,'safety':True,'native_cleanup':True}}


@pytest.mark.parametrize('field,value',[('schema_version',True),('schema_version',2),('state','UNKNOWN'),
    ('nonce','A'*32),('owner_pid',True),('owner_pid',0),('owner_birth','2026-10-03T00:00:00'),
    ('updated_at','2026-10-03T00:00:01+01:00'),('proof',None),('proof',{'integrity':1,'safety':True,'native_cleanup':True}),
    ('reason','OFFLINE_AUDIT')])
def test_invalid_idle_never_allows_start(field,value):
    item=idle();item[field]=value
    with pytest.raises((ValueError,TypeError)):state.validate(item)


def test_unknown_missing_duplicate_oversized_and_nan_are_rejected(tmp_path):
    path=tmp_path/'state.json'
    with pytest.raises(FileNotFoundError):state.read(path)
    for body in (json.dumps(idle())[:-1]+',"state":"IDLE"}',' '*8193,'{"schema_version":NaN}', '{}'):
        path.write_text(body)
        with pytest.raises((ValueError,TypeError)):state.read(path)


def test_atomic_active_survives_then_idle_requires_same_nonce(tmp_path):
    path=tmp_path/'state.json';item=idle();item.update(state='ACTIVE',reason='RECOVERY_REQUIRED',proof=None)
    state.write(path,item);assert state.read(path)==item
    expected=tmp_path/'expected.json';state.fresh_snapshot(expected,item)
    digest=hashlib.sha256(expected.read_bytes()).hexdigest()
    result=subprocess.run([sys.executable,'-I',str(ROOT/'scripts/maintenance_state.py'),'idle','--path',str(path),
        '--nonce','b'*32,'--owner-pid','42','--owner-birth',item['owner_birth'],'--expected-state',str(expected),'--expected-sha256',digest],capture_output=True,text=True,timeout=5)
    assert result.returncode==2 and state.read(path)==item
    assert json.loads(result.stdout)=={'state':'UNKNOWN','nonce':None}
    wrong_owner=subprocess.run([sys.executable,'-I',str(ROOT/'scripts/maintenance_state.py'),'idle','--path',str(path),
        '--nonce',item['nonce'],'--owner-pid','43','--owner-birth',item['owner_birth'],'--expected-state',str(expected),'--expected-sha256',digest],capture_output=True,text=True,timeout=5)
    assert wrong_owner.returncode==2 and state.read(path)==item


@pytest.mark.parametrize('field,value',[('nonce','c'*32),('owner_pid',43),
    ('owner_birth','2026-10-03T00:00:02Z'),('updated_at','2026-10-03T00:00:03Z'),
    ('reason','OFFLINE_AUDIT')])
def test_handover_compares_entire_old_generation_without_overwriting(tmp_path,field,value):
    path=tmp_path/'state.json';item=idle();item.update(state='ACTIVE',reason='RECOVERY_REQUIRED',proof=None)
    state.write(path,item)
    expected=dict(item);expected[field]=value
    before=path.read_bytes()
    with pytest.raises(ValueError,match='generation_changed'):
        state.handover(path,expected,nonce='b'*32,owner_pid=77,owner_birth='2026-10-03T00:00:04Z')
    assert path.read_bytes()==before and not list(tmp_path.glob('*.tmp'))


def test_handover_stays_active_and_requires_fresh_nonce(tmp_path):
    path=tmp_path/'state.json';item=idle();item.update(state='ACTIVE',reason='RECOVERY_REQUIRED',proof=None)
    state.write(path,item)
    with pytest.raises(ValueError,match='fresh_nonce'):
        state.handover(path,item,nonce=item['nonce'],owner_pid=77,owner_birth='2026-10-03T00:00:04Z')
    result=state.handover(path,item,nonce='b'*32,owner_pid=77,owner_birth='2026-10-03T00:00:04Z')
    assert state.read(path)==result and result['state']=='ACTIVE' and result['proof'] is None
    assert item['nonce']=='a'*32 and result['nonce']=='b'*32 and result['owner_pid']==77


@pytest.mark.parametrize('field,value',[('schema_version',2),('state','IDLE'),('nonce','c'*32),
    ('owner_pid',43),('owner_birth','2026-10-03T00:00:02Z'),('updated_at','2026-10-03T00:00:03Z'),
    ('reason','OFFLINE_AUDIT'),('proof',{'integrity':True,'safety':True,'native_cleanup':True})])
def test_idle_binds_every_field_without_any_write(tmp_path,monkeypatch,field,value):
    path=tmp_path/'state.json';item=idle();item.update(state='ACTIVE',reason='RECOVERY_REQUIRED',proof=None)
    state.write(path,item);before=path.read_bytes();expected=dict(item);expected[field]=value
    def forbidden(*args):pytest.fail('mismatched generation must not write')
    monkeypatch.setattr(state,'write',forbidden)
    with pytest.raises((ValueError,TypeError)):
        state.idle(path,expected,nonce=item['nonce'],owner_pid=42,owner_birth=item['owner_birth'])
    assert path.read_bytes()==before and not list(tmp_path.glob('*.tmp'))


def test_idle_defensive_copy_and_no_repeat(tmp_path,monkeypatch):
    path=tmp_path/'state.json';item=idle();item.update(state='ACTIVE',reason='RECOVERY_REQUIRED',proof=None)
    state.write(path,item);original_read=state.read
    def mutate_argument(target):
        item['reason']='OFFLINE_AUDIT'
        return original_read(target)
    monkeypatch.setattr(state,'read',mutate_argument)
    result=state.idle(path,item,nonce=item['nonce'],owner_pid=42,owner_birth=item['owner_birth'])
    assert result['state']=='IDLE' and result['reason']=='VERIFIED_CLEANUP'
    before=path.read_bytes()
    with pytest.raises(ValueError):
        state.idle(path,result,nonce=result['nonce'],owner_pid=42,owner_birth=result['owner_birth'])
    assert path.read_bytes()==before


def test_idle_cli_requires_expected_and_snapshot_is_exact_fresh(tmp_path):
    path=tmp_path/'state.json';snapshot=tmp_path/'expected.json'
    args=[sys.executable,'-I',str(ROOT/'scripts/maintenance_state.py')]
    owner=['--nonce','a'*32,'--owner-pid','42','--owner-birth','2026-10-03T00:00:00Z']
    active=subprocess.run(args+['active','--path',str(path),*owner,'--reason','RECOVERY_REQUIRED',
        '--published-state',str(snapshot)],capture_output=True,text=True,timeout=5)
    assert active.returncode==0 and state.read(snapshot)==state.read(path)
    assert json.loads(active.stdout)['snapshot_sha256']==hashlib.sha256(snapshot.read_bytes()).hexdigest()
    before=path.read_bytes()
    missing=subprocess.run(args+['idle','--path',str(path),*owner],capture_output=True,text=True,timeout=5)
    assert missing.returncode==2 and path.read_bytes()==before
    with pytest.raises(FileExistsError):state.fresh_snapshot(snapshot,state.read(path))
    completed=subprocess.run(args+['idle','--path',str(path),*owner,'--expected-state',str(snapshot),
        '--expected-sha256',hashlib.sha256(snapshot.read_bytes()).hexdigest()],
        capture_output=True,text=True,timeout=5)
    assert completed.returncode==0 and state.read(path)['state']=='IDLE'


def api():
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateMutexW.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_wchar_p];kernel.CreateMutexW.restype=ctypes.c_void_p
    kernel.OpenMutexW.argtypes=[ctypes.c_uint32,ctypes.c_int,ctypes.c_wchar_p];kernel.OpenMutexW.restype=ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_uint32];kernel.WaitForSingleObject.restype=ctypes.c_uint32
    kernel.ReleaseMutex.argtypes=[ctypes.c_void_p];kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    return kernel


@pytest.mark.skipif(os.name!='nt',reason='native Windows snapshot sharing')
@pytest.mark.parametrize('case',['Happy','Repeat','Reason','Updated','Missing','Hash','Repoint',
    'CloseException','NativeFalse','NativeException','Readback'])
def test_actual_snapshot_helper_without_global_mutex(tmp_path,case):
    (tmp_path/'RUNTIME').mkdir();(tmp_path/'scripts').mkdir()
    shutil.copyfile(ROOT/'scripts/maintenance_state.py',tmp_path/'scripts/maintenance_state.py')
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
        '-File',str(ROOT/'tests/maintenance_snapshot_fixture.ps1'),'-Workspace',str(tmp_path),
        '-Python',sys.executable,'-Case',case],capture_output=True,timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode==0,result.stdout.decode(errors='replace')+result.stderr.decode(errors='replace')
    assert result.stdout.strip()==b'PASS'
    marker=state.read(tmp_path/'RUNTIME/maintenance-state.json')
    assert marker['state']==('IDLE' if case in ('Happy','Repeat','Readback') else 'ACTIVE')


def test_expected_path_repoint_and_missing_hash_never_write(tmp_path,monkeypatch):
    original=idle();original.update(state='ACTIVE',reason='RECOVERY_REQUIRED',proof=None)
    first=tmp_path/'first.json';other=tmp_path/'other.json'
    state.fresh_snapshot(first,original);changed=dict(original,reason='OFFLINE_AUDIT')
    state.fresh_snapshot(other,changed)
    digest=hashlib.sha256(first.read_bytes()).hexdigest()
    def forbidden(*args):pytest.fail('snapshot mismatch must not write')
    monkeypatch.setattr(state,'write',forbidden)
    for sha in (None,'bad',digest):
        with pytest.raises(ValueError):state.bound_snapshot(other,sha)


def workspace(tmp_path):
    for folder in ('scripts','TOOLS','RUNTIME','LAUNCHERS'):(tmp_path/folder).mkdir()
    for file in ('scripts/maintenance_state.py','TOOLS/IMC_MAINTENANCE.ps1','TOOLS/IMC_RUNTIME_JOB.ps1','TOOLS/IMC_SHADOW_SESSION.ps1',
                 'TOOLS/GMGN-ReadProtocol.ps1','LAUNCHERS/START_IMC_SIGNAL_ONLY.ps1','LAUNCHERS/RUN_IMC_SIGNAL_ONLY.ps1'):
        shutil.copyfile(ROOT/file,tmp_path/file)
    # Actual launcher bodies are preserved. Pure test discovery cannot stop
    # or discover IMC; Start-Process executes the unchanged fixture RUN once.
    quoted=str(Path(sys.executable)).replace("'","''")
    (tmp_path/'LAUNCHERS/IMC_PYTHON_COMMON.ps1').write_text(
        "function Get-IMCPythonPath {return '"+quoted+"'}\n"
        "function Get-IMCPython {return '"+quoted+"'}")
    (tmp_path/'LAUNCHERS/IMC_PROCESS_COMMON.ps1').write_text(
        "function Get-IMCProcessSnapshot {return @{Processes=@();UnknownInterpreterPids=@()}}\n"
        "function Start-Process {param($FilePath,$WindowStyle,$ArgumentList) "
        "$output=@(& $FilePath @ArgumentList); if($LASTEXITCODE -ne 0){throw 'FIXTURE_RUN_REJECTED'};"
        "[IO.File]::WriteAllText((Join-Path (Split-Path -Parent $PSScriptRoot) 'run-prelude-result'),($output -join \"`n\"))}")
    state.write(tmp_path/'RUNTIME/maintenance-state.json',idle())
    return tmp_path


def command(path,case):
    return ['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',
        str(ROOT/'tests/maintenance_fixture.ps1'),'-Workspace',str(path),'-Python',sys.executable,'-Case',case]


def run(path,case):
    return subprocess.run(command(path,case),capture_output=True,text=True,timeout=8)


@pytest.mark.skipif(os.name!='nt',reason='native Windows fence')
def test_global_fence_blocks_actual_start_and_direct_run(tmp_path):
    path=workspace(tmp_path);kernel=api()
    handle=kernel.CreateMutexW(None,True,'Global\\IMC_TRADING_MAIN_MAINTENANCE_V1')
    assert handle
    try:
        for case in ('start','entry_start','entry_run'):
            result=run(path,case)
            assert result.returncode!=0,result.stdout+result.stderr
            assert 'IMC_MAINTENANCE_BUSY' in result.stdout+result.stderr
        assert not (path/'RUNTIME/signal-runtime-status.json').exists()
    finally:
        assert kernel.ReleaseMutex(handle);assert kernel.CloseHandle(handle)


@pytest.mark.skipif(os.name!='nt',reason='native Windows fence')
def test_actual_asynchronous_entry_handover_does_not_hold_fence_for_run(tmp_path):
    path=workspace(tmp_path);kernel=api()
    # Holding (or observing the existing owner of) RuntimeMutex proves the
    # admitted fixture RUN exits before all API, ledger, broker or worker code.
    runtime=kernel.CreateMutexW(None,False,'Global\\IMC_TRADING_MAIN_RUNTIME_V1');assert runtime
    wait=kernel.WaitForSingleObject(runtime,0);assert wait in (0,128,258)
    try:
        result=run(path,'entry_start')
        assert result.returncode==0,result.stdout+result.stderr
        assert (path/'run-prelude-result').read_text()=='IMC_SIGNAL_RUNTIME_ALREADY_RUNNING'
        assert not (path/'RUNTIME/signal-runtime-status.json').exists()
    finally:
        if wait in (0,128):assert kernel.ReleaseMutex(runtime)
        assert kernel.CloseHandle(runtime)


@pytest.mark.skipif(os.name!='nt',reason='native Windows fence')
def test_crashed_holder_leaves_durable_block_after_native_abandonment(tmp_path):
    path=workspace(tmp_path);kernel=api();reference=None
    child=subprocess.Popen(command(path,'hold'),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
        creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        deadline=time.monotonic()+5
        while not (path/'ready').exists() and time.monotonic()<deadline:
            assert child.poll() is None;time.sleep(.01)
        assert (path/'ready').exists()
        assert state.read(path/'RUNTIME/maintenance-state.json')['state']=='ACTIVE'
        reference=kernel.OpenMutexW(0x100001,False,'Global\\IMC_TRADING_MAIN_MAINTENANCE_V1');assert reference
        child.kill();child.wait(timeout=3)
        assert kernel.WaitForSingleObject(int(child._handle),0)==0
        result=run(path,'start')
        assert result.returncode==2 and 'IMC_MAINTENANCE_RECOVERY_REQUIRED' in result.stdout
        assert state.read(path/'RUNTIME/maintenance-state.json')['reason']=='RECOVERY_REQUIRED'
        # A subsequent caller sees the persisted block, not just abandonment.
        again=run(path,'start');assert again.returncode==2
    finally:
        if child.poll() is None:child.kill()
        child.wait(timeout=3)
        if reference:assert kernel.CloseHandle(reference)


@pytest.mark.skipif(os.name!='nt',reason='native Windows fence')
def test_holder_releases_only_after_completed_fixture_cleanup(tmp_path):
    path=workspace(tmp_path)
    child=subprocess.Popen(command(path,'hold'),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
        creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        deadline=time.monotonic()+5
        while not (path/'ready').exists() and time.monotonic()<deadline:
            assert child.poll() is None;time.sleep(.01)
        assert (path/'ready').exists()
        (path/'release').write_text('release')
        output,error=child.communicate(timeout=5)
        assert child.returncode==0,output+error
        assert state.read(path/'RUNTIME/maintenance-state.json')['state']=='IDLE'
        assert run(path,'start').returncode==0
    finally:
        if child.poll() is None:child.kill()
        child.wait(timeout=3)


def test_recovery_publishes_exact_bound_new_generation(tmp_path):
    path=tmp_path/'state.json';item=idle();item.update(state='ACTIVE',reason='RECOVERY_REQUIRED',proof=None)
    state.write(path,item);published=tmp_path/'new.json'
    value=state.handover(path,item,nonce='b'*32,owner_pid=77,owner_birth='2026-10-03T00:00:04Z',published_state=published)
    digest=hashlib.sha256(published.read_bytes()).hexdigest()
    assert state.bound_snapshot(published,digest)==value==state.read(path)
    assert value['state']=='ACTIVE'
    released=state.idle(path,value,nonce=value['nonce'],owner_pid=77,owner_birth=value['owner_birth'])
    assert released['state']=='IDLE'


def test_recovery_generation_change_during_snapshot_never_overwrites(tmp_path,monkeypatch):
    path=tmp_path/'state.json';item=idle();item.update(state='ACTIVE',reason='RECOVERY_REQUIRED',proof=None)
    state.write(path,item);original=state.fresh_snapshot
    def changed(target,value):
        original(target,value)
        state.write(path,dict(item,nonce='c'*32))
    monkeypatch.setattr(state,'fresh_snapshot',changed)
    with pytest.raises(ValueError,match='generation_changed'):
        state.handover(path,item,nonce='b'*32,owner_pid=77,owner_birth='2026-10-03T00:00:04Z',published_state=tmp_path/'new.json')
    assert state.read(path)['nonce']=='c'*32


@pytest.mark.skipif(os.name!='nt',reason='native process identity')
def test_native_recovery_owner_alive_and_pid_reuse():
    helper=str(ROOT/'TOOLS/IMC_MAINTENANCE.ps1').replace("'","''")
    code=". '"+helper+"'; $p=Get-Process -Id $PID; $null=$p.Handle; $b=$p.StartTime.ToUniversalTime().ToFileTimeUtc(); "
    code+="Write-Output ('ALIVE='+[IMCMaintenanceNativeClose]::OwnerEnded($PID,$b)); "
    code+="Write-Output ('REUSED='+[IMCMaintenanceNativeClose]::OwnerEnded($PID,($b-1))); $p.Dispose()"
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',code],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'ALIVE=False' in result.stdout and 'REUSED=True' in result.stdout
