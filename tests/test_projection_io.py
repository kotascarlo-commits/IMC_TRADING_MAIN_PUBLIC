"""Projection replacement only; no order, ledger or production files."""
import ctypes
import json
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from mykonos_meme_scout import projection_io as io

@pytest.mark.parametrize('winerror',[5,32,33])
def test_one_serialization_then_transient_replace_retry(tmp_path,monkeypatch,winerror):
    target=tmp_path/'status.json';target.write_text('previous')
    original=Path.replace;calls=[];writes=[];open_original=Path.open
    def open_count(self,*args,**kwargs):
        if args and args[0]=='x':writes.append(self)
        return open_original(self,*args,**kwargs)
    def replace(self,path):
        calls.append(self)
        if len(calls)==1:
            error=PermissionError('fixture');error.winerror=winerror;raise error
        return original(self,path)
    monkeypatch.setattr(Path,'replace',replace);monkeypatch.setattr(Path,'open',open_count)
    io.write_json_projection(target,{'next':True},sleep=lambda _:None)
    assert len(calls)==2 and calls[0]==calls[1] and len(writes)==1
    assert json.loads(target.read_text())=={'next':True} and list(tmp_path.iterdir())==[target]

def test_no_attempt_after_sleep_exceeds_deadline(tmp_path,monkeypatch):
    target=tmp_path/'status.json';target.write_text('previous');now=[0];calls=[]
    def fail(self,path):
        calls.append(self);error=PermissionError('fixture');error.winerror=5;raise error
    def sleep(delay):now[0]=1
    monkeypatch.setattr(Path,'replace',fail)
    with pytest.raises(PermissionError):io.write_json_projection(target,{'next':True},clock=lambda:now[0],sleep=sleep)
    assert len(calls)==1 and target.read_text()=='previous' and list(tmp_path.iterdir())==[target]

def test_readonly_access_denied_is_not_retried(tmp_path,monkeypatch):
    target=tmp_path/'status.json';target.write_text('previous');calls=[];original_stat=Path.stat
    def fake_stat(self,*args,**kwargs):
        if self==target:return SimpleNamespace(st_file_attributes=stat.FILE_ATTRIBUTE_READONLY)
        return original_stat(self,*args,**kwargs)
    def fail(self,path):
        calls.append(self);error=PermissionError('fixture');error.winerror=5;raise error
    monkeypatch.setattr(Path,'stat',fake_stat);monkeypatch.setattr(Path,'replace',fail)
    with pytest.raises(PermissionError):io.write_json_projection(target,{'next':True},sleep=lambda _:pytest.fail('readonly retry'))
    assert len(calls)==1 and target.read_text()=='previous'

def test_temp_collision_preserves_foreign_file(tmp_path,monkeypatch):
    target=tmp_path/'status.json';foreign=tmp_path/'status.json.collision.tmp';foreign.write_text('foreign')
    monkeypatch.setattr(io.uuid,'uuid4',lambda:SimpleNamespace(hex='collision'))
    with pytest.raises(FileExistsError):io.write_json_projection(target,{'next':True})
    assert foreign.read_text()=='foreign' and not target.exists()

def test_serializer_failure_cleans_only_verified_original_temp(tmp_path,monkeypatch):
    target=tmp_path/'status.json'
    def fail(value,stream,**kwargs):raise ValueError('fixture serialization')
    monkeypatch.setattr(io.json,'dump',fail)
    with pytest.raises(ValueError):io.write_json_projection(target,{'next':True})
    assert not list(tmp_path.iterdir())

def test_swapped_temp_after_serializer_failure_is_preserved(tmp_path,monkeypatch):
    target=tmp_path/'status.json';saved=[]
    def swapped(value,stream,**kwargs):
        temporary=Path(stream.name);stream.close()
        temporary.rename(tmp_path/'original-owned-file')
        temporary.write_text('foreign-replacement');saved.append(temporary)
        raise ValueError('fixture serialization')
    monkeypatch.setattr(io.json,'dump',swapped)
    with pytest.raises(ValueError):io.write_json_projection(target,{'next':True})
    assert saved[0].read_text()=='foreign-replacement' and not target.exists()

def test_missing_descriptor_identity_never_unlinks(tmp_path,monkeypatch):
    target=tmp_path/'status.json'
    monkeypatch.setattr(io.os,'fstat',lambda fd:(_ for _ in ()).throw(OSError('fixture identity unavailable')))
    with pytest.raises(OSError):io.write_json_projection(target,{'next':True})
    assert len(list(tmp_path.iterdir()))==1 and not target.exists()

def test_other_io_error_is_immediately_visible(tmp_path,monkeypatch):
    target=tmp_path/'status.json';target.write_text('previous')
    def fail(self,path):raise OSError(28,'fixture disk full')
    monkeypatch.setattr(Path,'replace',fail)
    with pytest.raises(OSError):io.write_json_projection(target,{'next':True},sleep=lambda _:pytest.fail('disk full retry'))
    assert target.read_text()=='previous' and list(tmp_path.iterdir())==[target]

@pytest.mark.skipif(sys.platform!='win32',reason='native Windows destination sharing')
@pytest.mark.parametrize('share',[3,7])
def test_native_open_destination_then_close_allows_same_payload_retry(tmp_path,monkeypatch,share):
    target=tmp_path/'status.json';target.write_text('previous')
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=[ctypes.c_wchar_p,ctypes.c_uint,ctypes.c_uint,ctypes.c_void_p,ctypes.c_uint,ctypes.c_uint,ctypes.c_void_p]
    kernel.CreateFileW.restype=ctypes.c_void_p;kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    handle=kernel.CreateFileW(str(target),0x80000000,share,None,3,0x80,None)
    assert handle!=ctypes.c_void_p(-1).value
    held=[True];errors=[];attempts=[];original=Path.replace;serializations=[];dump=io.json.dump
    def count_dump(*args,**kwargs):serializations.append(1);return dump(*args,**kwargs)
    def replace(self,path):
        attempts.append(self)
        try:return original(self,path)
        except OSError as error:errors.append(error.winerror);raise
    def release(delay):
        assert kernel.CloseHandle(handle);held[0]=False
    monkeypatch.setattr(Path,'replace',replace)
    monkeypatch.setattr(io.json,'dump',count_dump)
    try:io.write_json_projection(target,{'next':True},sleep=release)
    finally:
        if held[0]:kernel.CloseHandle(handle)
    assert errors==[5] and len(attempts)==2 and attempts[0]==attempts[1] and len(serializations)==1
    assert json.loads(target.read_text())=={'next':True}
