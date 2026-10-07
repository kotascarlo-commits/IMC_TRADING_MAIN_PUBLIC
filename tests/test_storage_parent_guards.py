"""Native PS-parent / Python-worker guard contract in isolated temp roots."""
import json
import ctypes
import os
from pathlib import Path
import subprocess
import sys
import struct

import pytest

ROOT = Path(__file__).parents[1]
pytestmark = pytest.mark.skipif(os.name != 'nt', reason='native PS x64 guard ABI')


def run(root, case):
    return subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
        '-File',str(ROOT/'tests/storage_parent_guards_fixture.ps1'),'-Workspace',str(root),
        '-Python',sys._base_executable,'-Case',case],capture_output=True,text=True,timeout=40)


@pytest.mark.parametrize('case', ['Normal','Weak','Sentinel','DuplicateKey','DuplicatePath',
                                 'Hardlink','Unpinned','BrokenChain','Inspection','PriorFault'])
def test_native_strong_append_pins_and_retains_partial_faults(tmp_path,case):
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
        '-File',str(ROOT/'tests/storage_strong_append_fixture.ps1'),'-Workspace',str(tmp_path),
        '-Case',case],capture_output=True,text=True,timeout=40)
    assert result.returncode==0,result.stdout+result.stderr
    assert ('STRONG_APPEND_NATIVE_PASS' if case=='Normal' else
            'STRONG_APPEND_FAULT_RETAINED') in result.stdout


def test_native_parent_directory_sentinel_and_file_handoff_across_processes(tmp_path):
    for directory in ('source','work','archive','journal'):(tmp_path/directory).mkdir()
    source=tmp_path/'source/imc-main.db';work=tmp_path/'work/imc-main.db'
    before={str(source):b'original',str(source)+'-wal':b'wal',str(source)+'-shm':b'shm',str(work):b'compact'}
    for path,body in before.items():Path(path).write_bytes(body)
    result=run(tmp_path,'Normal')
    assert result.returncode==0,result.stdout+result.stderr
    assert 'PS_NATIVE_PARENT_STRONG_HANDOFF_PASS' in result.stdout
    assert source.read_bytes()==b'compact'
    for suffix in ('','-wal','-shm'):
        assert Path(str(tmp_path/'archive/imc-main.db')+suffix).read_bytes()==before[str(source)+suffix]
    assert not work.exists()


def test_unsigned_full128_and_volume_json_are_actual_positive_integers(tmp_path):
    result=run(tmp_path,'Unsigned')
    assert result.returncode==0,result.stdout+result.stderr
    rows=[json.loads(row) for row in result.stdout.splitlines()]
    assert rows==[{'volume':2**64-1,'file_id':2**127,'bytes':7},
                 {'volume':2**64-1,'file_id':2**128-1,'bytes':9}]


@pytest.mark.parametrize('case', ['RebindPhases','RebindStale','RebindWrong','RebindLate',
                                  'RebindUnpinned','RebindIdentity','RebindStrong'])
def test_phase_rebind_checks_all_native_members_before_changing_any_path(tmp_path,case):
    for directory in ('source','work','archive','journal','unpinned'):
        (tmp_path/directory).mkdir()
    source=tmp_path/'source/imc-main.db';work=tmp_path/'work/imc-main.db'
    source.write_bytes(b'original');work.write_bytes(b'compact')
    Path(str(source)+'-wal').write_bytes(b'wal')
    Path(str(source)+'-shm').write_bytes(b'shm')
    result=run(tmp_path,case)
    assert result.returncode==0,result.stdout+result.stderr
    assert ('FOUR_PHASE_REBIND_PASS' if case=='RebindPhases' else
            'INVALID_REBIND_RETAINED_NO_PARTIAL_MAPPING') in result.stdout


@pytest.mark.parametrize('case',['Scopes','ScopesEmpty','ScopesOutside'])
def test_scope_only_group_has_independent_lifetime_without_dummy_files(tmp_path,case):
    (tmp_path/'source').mkdir();(tmp_path/'journal').mkdir()
    source=tmp_path/'source/imc-main.db';source.write_bytes(b'untouched')
    result=run(tmp_path,case)
    assert result.returncode==0,result.stdout+result.stderr
    assert ('INDEPENDENT_SCOPE_ONLY_LIFETIME_PASS' if case=='Scopes' else
            'INVALID_SCOPES_RETAINED') in result.stdout
    assert source.read_bytes()==b'untouched'


def test_outside_path_rejected_before_native_file_open_and_group_retained(tmp_path):
    result=run(tmp_path,'Bounds')
    assert result.returncode==0,result.stdout+result.stderr
    assert 'BOUND_REJECTED_RETAINED' in result.stdout
    assert list(tmp_path.iterdir())==[]


@pytest.mark.parametrize('case', ['Duplicate','Hardlink','CloseFault','WrongStrong'])
def test_native_partial_faults_and_close_uncertainty_retain_the_guard_group(tmp_path,case):
    for directory in ('source','work','archive','journal'):(tmp_path/directory).mkdir()
    source=tmp_path/'source/imc-main.db';work=tmp_path/'work/imc-main.db'
    source.write_bytes(b'original');work.write_bytes(b'compact')
    Path(str(source)+'-wal').write_bytes(b'wal');Path(str(source)+'-shm').write_bytes(b'shm')
    if case=='Hardlink':os.link(source,tmp_path/'source/alias.db')
    result=run(tmp_path,case)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'RETAINED' in result.stdout
    assert source.read_bytes()==b'original' and work.read_bytes()==b'compact'


def test_concurrent_native_hash_and_close_are_serialized(tmp_path):
    for directory in ('source','work','archive','journal'):(tmp_path/directory).mkdir()
    source=tmp_path/'source/imc-main.db';work=tmp_path/'work/imc-main.db'
    source.write_bytes(b'original'*1048576);work.write_bytes(b'compact')
    Path(str(source)+'-wal').write_bytes(b'wal');Path(str(source)+'-shm').write_bytes(b'shm')
    result=run(tmp_path,'Concurrency')
    assert result.returncode==0,result.stdout+result.stderr
    assert result.stdout.strip() in ('HASH_THEN_CLOSE','CLOSE_THEN_HASH_REJECTED')


@pytest.mark.parametrize('case', ['RenameAssert','RenameMember','RenameSentinel','RenamePaths',
                                  'UnpinnedStrong','LateUnpinned','ClosedThenException','PartialStrong'])
def test_public_assert_strong_preflight_and_ambiguous_close_remain_fail_closed(tmp_path,case):
    for directory in ('source','work','archive','journal','unpinned'):(tmp_path/directory).mkdir()
    source=tmp_path/'source/imc-main.db';work=tmp_path/'work/imc-main.db'
    source.write_bytes(b'original');work.write_bytes(b'compact')
    Path(str(source)+'-wal').write_bytes(b'wal');Path(str(source)+'-shm').write_bytes(b'shm')
    result=run(tmp_path,case)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'RETAINED' in result.stdout
    actual_work=work if work.exists() else tmp_path/'unpinned/imc-main.db'
    assert actual_work.read_bytes()==b'compact'
    actual=source if source.exists() else (tmp_path/'foreign.db' if case.startswith('Rename') else tmp_path/'unpinned/imc-main.db')
    assert actual.read_bytes()==b'original'


def junction(path,target):
    path.mkdir()
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    api.CreateFileW.argtypes=[ctypes.c_wchar_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p]
    api.CreateFileW.restype=ctypes.c_void_p
    api.CloseHandle.argtypes=[ctypes.c_void_p];api.CloseHandle.restype=ctypes.c_int
    api.DeviceIoControl.argtypes=[ctypes.c_void_p,ctypes.c_uint32,ctypes.c_void_p,ctypes.c_uint32,ctypes.c_void_p,ctypes.c_uint32,ctypes.c_void_p,ctypes.c_void_p]
    api.DeviceIoControl.restype=ctypes.c_int
    substitute=('\\??\\'+str(target)).encode('utf-16-le');display=str(target).encode('utf-16-le')
    names=substitute+bytes(2)+display+bytes(2)
    data=struct.pack('<IHHHHHH',0xA0000003,8+len(names),0,0,len(substitute),len(substitute)+2,len(display))+names
    handle=api.CreateFileW(str(path),0x40000000,7,None,3,0x2200000,None)
    assert handle not in (None,ctypes.c_void_p(-1).value)
    try:
        buffer=ctypes.create_string_buffer(data);count=ctypes.c_uint32()
        result=api.DeviceIoControl(handle,0x900A4,buffer,len(data),None,0,ctypes.byref(count),None)
        error=ctypes.get_last_error()
        assert result, 'unprotected native junction control failed: '+str(error)
    finally:assert api.CloseHandle(handle)
    assert path.lstat().st_file_attributes & 0x400


@pytest.mark.parametrize('where',['above_root','at_root','inside_root'])
def test_native_junction_access_chain_never_opens_source_member(tmp_path,where):
    target=tmp_path/'real';target.mkdir()
    if where=='above_root':
        (target/'project').mkdir();root=tmp_path/'link/project'
        junction(tmp_path/'link',target)
        actual=target/'project'
    elif where=='at_root':
        root=tmp_path/'link';junction(root,target);actual=target
    else:
        root=tmp_path/'project';root.mkdir();actual=root
        junction(root/'source',target)
    source_parent=target if where=='inside_root' else actual/'source'
    if where!='inside_root':source_parent.mkdir()
    (source_parent/'imc-main.db').write_bytes(b'untouched')
    result=run(root,'Junction')
    assert result.returncode==0,result.stdout+result.stderr
    assert 'JUNCTION_REJECTED_BEFORE_MEMBER_OPEN' in result.stdout
    assert (source_parent/'imc-main.db').read_bytes()==b'untouched'


def test_held_leaf_sentinel_has_successful_native_unprotected_reparse_control(tmp_path):
    for directory in ('source','work','archive','journal','control','redirect'):(tmp_path/directory).mkdir()
    source=tmp_path/'source/imc-main.db';work=tmp_path/'work/imc-main.db'
    source.write_bytes(b'original');work.write_bytes(b'compact')
    Path(str(source)+'-wal').write_bytes(b'wal');Path(str(source)+'-shm').write_bytes(b'shm')
    result=run(tmp_path,'ReparseHeld')
    assert result.returncode==0,result.stdout+result.stderr
    assert 'HELD_LEAF_REPARSE_PROTECTION_PASS' in result.stdout
