"""Cold read-only authority snapshots, not a publication controller.

The host additionally proves both Jobs, role/phase, namespace pins, Source
guards and Gate OFF. Mutex queries are point-in-time facts, never a claim of
continuous ownership or an atomic grant for a later native rename.

Parent holds its ACTIVE marker READ/noDELETE only after handover. Workers
close their own marker/query handles before Exit0; Parent closes its marker
guard only after all worker Jobs are cleaned, before the final IDLE replace.
"""
import ctypes
from datetime import datetime
import hashlib
import os
from pathlib import Path
import re
import threading
from contextlib import nullcontext

from maintenance_state import validate
from storage_publish_channel import Client,_FileTime
from storage_publish_deadline import Deadline
from storage_publish_guard import FileID,Standard
from storage_publish_manifest import decode,encoded,identity


MUTEXES=('Global\\IMC_TRADING_MAIN_MAINTENANCE_V1',
         'Global\\IMC_TRADING_MAIN_OFFLINE_AUDIT_V1',
         'Global\\IMC_TRADING_MAIN_RUNTIME_V1')
_RETAINED=[]


def _filetime(text):
    match=re.fullmatch(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,7}))?(?:Z|\+00:00)',text)
    if match is None:raise ValueError('publish_authority_process_birth')
    delta=datetime.fromisoformat(match[1])-datetime(1601,1,1)
    return (delta.days*86400+delta.seconds)*10000000+int((match[2] or '').ljust(7,'0'))


class Basic(ctypes.Structure):
    _fields_=[('count',ctypes.c_int32),('owned',ctypes.c_ubyte),('abandoned',ctypes.c_ubyte)]


class Owner(ctypes.Structure):
    _fields_=[('process',ctypes.c_void_p),('thread',ctypes.c_void_p)]


class Tag(ctypes.Structure):
    _fields_=[('attributes',ctypes.c_uint32),('tag',ctypes.c_uint32)]


class ParentAuthority:
    def __init__(self,client,*,project_root,parent_thread,parent_thread_birth,
                 marker_identity,active_snapshot,private_test_nonce=None):
        if (type(client) is not Client or type(client._shared_deadline) is not Deadline
                or type(parent_thread) is not int or not 0<parent_thread<=2147483647
                or type(parent_thread_birth) is not int or not 0<parent_thread_birth<2**63):
            raise ValueError('publish_authority_binding')
        if (ctypes.sizeof(Basic)!=8 or Basic.owned.offset!=4 or Basic.abandoned.offset!=5
                or ctypes.sizeof(Owner)!=16 or Owner.thread.offset!=8
                or ctypes.sizeof(FileID)!=24 or ctypes.sizeof(Standard)!=24):
            raise ValueError('publish_authority_abi')
        identity(marker_identity)
        snapshot=validate(decode(encoded(active_snapshot)))
        if (snapshot['state']!='ACTIVE' or snapshot['owner_pid']!=client._pid
                or _filetime(snapshot['owner_birth'])!=client._birth
                or marker_identity['bytes']>1024):
            raise ValueError('publish_authority_snapshot')
        root=Path(project_root)
        if (not root.is_absolute() or root.drive.startswith('\\\\')
                or root!=root.resolve(strict=True)):
            raise ValueError('publish_authority_root')
        self._marker=root/'RUNTIME/maintenance-state.json'
        self._expected_marker=decode(encoded(marker_identity))
        self._snapshot=snapshot
        self._client=client;self._deadline=client._shared_deadline
        self._pid=client._pid;self._thread=parent_thread;self._thread_birth=parent_thread_birth
        self._lock=threading.Lock();self._state_lock=threading.RLock()
        self._fault_requested=threading.Event()
        self._fault_lock=threading.RLock()
        self.faulted=False;self.closed=False;self._handles={};self._states={}
        self.api=ctypes.WinDLL('kernel32',use_last_error=True)
        signatures={
            'OpenThread':([ctypes.c_uint32,ctypes.c_int,ctypes.c_uint32],ctypes.c_void_p),
            'GetProcessIdOfThread':([ctypes.c_void_p],ctypes.c_uint32),
            'GetThreadTimes':([ctypes.c_void_p]+[ctypes.POINTER(_FileTime)]*4,ctypes.c_int),
            'WaitForSingleObject':([ctypes.c_void_p,ctypes.c_uint32],ctypes.c_uint32),
            'OpenMutexW':([ctypes.c_uint32,ctypes.c_int,ctypes.c_wchar_p],ctypes.c_void_p),
            'CreateFileW':([ctypes.c_wchar_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p,
                            ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p],ctypes.c_void_p),
            'GetFileInformationByHandleEx':([ctypes.c_void_p,ctypes.c_int,ctypes.c_void_p,ctypes.c_uint32],ctypes.c_int),
            'GetFileType':([ctypes.c_void_p],ctypes.c_uint32),
            'GetFinalPathNameByHandleW':([ctypes.c_void_p,ctypes.c_wchar_p,ctypes.c_uint32,ctypes.c_uint32],ctypes.c_uint32),
            'SetFilePointerEx':([ctypes.c_void_p,ctypes.c_int64,ctypes.c_void_p,ctypes.c_uint32],ctypes.c_int),
            'ReadFile':([ctypes.c_void_p,ctypes.c_void_p,ctypes.c_uint32,ctypes.c_void_p,ctypes.c_void_p],ctypes.c_int),
            'CloseHandle':([ctypes.c_void_p],ctypes.c_int)}
        for name,(arguments,result) in signatures.items():
            function=getattr(self.api,name);function.argtypes=arguments;function.restype=result
        self.nt=ctypes.WinDLL('ntdll')
        self.nt.NtQueryMutant.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_void_p,ctypes.c_uint32,ctypes.c_void_p]
        self.nt.NtQueryMutant.restype=ctypes.c_int32
        if private_test_nonce is not None:
            if type(private_test_nonce) is not str or re.fullmatch('[a-f0-9]{32}',private_test_nonce) is None:
                raise ValueError('publish_authority_fixture_nonce')
            names=tuple('Local\\IMC_PUBLISH_AUTH_'+private_test_nonce+'_'+str(i) for i in range(3))
        else:names=MUTEXES
        _RETAINED.append(self)
        try:
            self._guard()
            self._own('thread',self.api.OpenThread(0x100800,False,parent_thread))
            self._thread_check()
            for i,name in enumerate(names):
                self._guard();self._own('mutex'+str(i),self.api.OpenMutexW(1,False,name))
            self._guard()
            self._own('marker',self.api.CreateFileW(str(self._marker),0x80000000,1,None,3,0x200080,None))
            self.check()
        except BaseException:self._fail();raise

    def _fail(self):
        # Fault registration and final completion have one linearization point.
        # Do not hold this short lock while waiting for native-close completion.
        with self._fault_lock:
            if not self.closed:self._fault_requested.set()
        with self._state_lock:
            if not self.closed:self.faulted=True

    def _guard(self):
        if self.faulted or self.closed or self._fault_requested.is_set():raise ValueError('publish_authority_unconfirmed')
        self._deadline.check()
        if self.faulted or self.closed or self._fault_requested.is_set():raise ValueError('publish_authority_unconfirmed')

    def _error(self):raise OSError('publish_authority_native_'+str(ctypes.get_last_error()))

    def _own(self,key,handle):
        if handle in (None,ctypes.c_void_p(-1).value):self._error()
        self._handles[key]=handle;self._states[key]='OWNED'

    def _thread_check(self):
        self._guard();handle=self._handles['thread']
        if self.api.GetProcessIdOfThread(handle)!=self._pid:raise ValueError('publish_authority_thread_process')
        birth,exit,kernel,user=(_FileTime() for _ in range(4))
        if not self.api.GetThreadTimes(handle,ctypes.byref(birth),ctypes.byref(exit),ctypes.byref(kernel),ctypes.byref(user)):
            self._error()
        if ((birth.high<<32)|birth.low)!=self._thread_birth:raise ValueError('publish_authority_thread_birth')
        status=self.api.WaitForSingleObject(handle,0)
        if status==0:raise ValueError('publish_authority_thread_exited')
        if status!=258:self._error()
        self._guard()

    def _mutex_check(self):
        for i in range(3):
            self._guard();handle=self._handles['mutex'+str(i)]
            basic,owner=Basic(),Owner()
            for kind,info in ((0,basic),(1,owner)):
                length=ctypes.c_uint32()
                status=self.nt.NtQueryMutant(handle,kind,ctypes.byref(info),ctypes.sizeof(info),ctypes.byref(length))
                if status!=0 or length.value!=ctypes.sizeof(info):raise ValueError('publish_authority_mutex_query')
            if (basic.count!=0 or basic.owned!=0 or basic.abandoned!=0
                    or owner.process!=self._pid or owner.thread!=self._thread):
                raise ValueError('publish_authority_mutex_owner')
        self._guard()

    def _marker_info(self,handle):
        identifier,standard,tag=FileID(),Standard(),Tag()
        for kind,info in ((18,identifier),(1,standard),(9,tag)):
            if not self.api.GetFileInformationByHandleEx(handle,kind,ctypes.byref(info),ctypes.sizeof(info)):self._error()
        if (self.api.GetFileType(handle)!=1 or standard.links!=1 or standard.directory
                or standard.delete_pending or not 0<=standard.bytes<=1024 or tag.tag or tag.attributes&0x400):
            raise ValueError('publish_authority_marker_shape')
        buffer=ctypes.create_unicode_buffer(32768)
        length=self.api.GetFinalPathNameByHandleW(handle,buffer,len(buffer),0)
        if not 0<length<len(buffer):raise ValueError('publish_authority_marker_path')
        path=buffer.value
        if path.startswith('\\\\?\\'):path=path[4:]
        if os.path.normcase(path)!=os.path.normcase(str(self._marker)):raise ValueError('publish_authority_marker_path')
        actual={'volume':identifier.volume,'file_id':int.from_bytes(bytes(identifier.identifier),'little'),
                'bytes':standard.bytes}
        if actual!={k:self._expected_marker[k] for k in actual}:raise ValueError('publish_authority_marker_identity')
        return actual

    def _close(self,key,*,cleanup_check=None,submission=None):
        # A close step and its native completion are serialized with the fault
        # transition. A concurrent fault request also prevents final removal;
        # an already authorized native close can finish, remaining handles stay.
        with self._state_lock:
            self._guard()
            if self._states[key]!='OWNED':raise ValueError('publish_authority_close_state')
            if cleanup_check is not None:cleanup_check()
            with submission() if submission is not None else nullcontext():
                if cleanup_check is not None:self._guard();cleanup_check()
                self._states[key]='CLOSING'
                try:
                    if not self.api.CloseHandle(self._handles[key]):self._error()
                    self._handles[key]=None;self._states[key]='CLOSED'
                except BaseException:
                    self._states[key]='FAULT';self.faulted=True;self._fault_requested.set()
                    raise
            if cleanup_check is not None:self._guard();cleanup_check()

    def _marker_check(self):
        handle=self._handles['marker'];before=self._marker_info(handle);self._guard()
        if not self.api.SetFilePointerEx(handle,0,None,0):self._error()
        buffer=ctypes.create_string_buffer(1025);count=ctypes.c_uint32()
        if not self.api.ReadFile(handle,buffer,len(buffer),ctypes.byref(count),None):self._error()
        self._guard();body=buffer.raw[:count.value]
        if count.value!=before['bytes'] or count.value>1024:raise ValueError('publish_authority_marker_bytes')
        if (hashlib.sha256(body).hexdigest()!=self._expected_marker['sha256']
                or encoded(validate(decode(body.removeprefix(b'\xef\xbb\xbf'))))!=encoded(self._snapshot)):
            raise ValueError('publish_authority_marker_snapshot')
        if self._marker_info(handle)!=before:raise ValueError('publish_authority_marker_identity')
        # Retain this fresh path handle on any fault; checked-close only after
        # both the held object and current namespace identity match.
        self._guard()
        self._own('fresh_marker',self.api.CreateFileW(str(self._marker),0x80000000,1,None,3,0x200080,None))
        if self._marker_info(self._handles['fresh_marker'])!=before:raise ValueError('publish_authority_marker_identity')
        self._close('fresh_marker')

    def check(self):
        if not self._lock.acquire(blocking=False):self._fail();raise ValueError('publish_authority_concurrent')
        try:
            self._guard();self._client._peer();self._thread_check();self._mutex_check()
            self._marker_check()
            self._client._peer();self._thread_check();self._mutex_check();self._guard()
            return True
        except BaseException:self._fail();raise
        finally:self._lock.release()

    def close(self,*,cleanup_check=None,submission=None):
        if ((cleanup_check is None)!=(submission is None) or
                cleanup_check is not None and (not callable(cleanup_check) or not callable(submission))):
            self._fail();raise ValueError('publish_authority_cleanup_binding')
        if not self._lock.acquire(blocking=False):self._fail();raise ValueError('publish_authority_concurrent')
        try:
            self._guard()
            if cleanup_check is not None:cleanup_check()
            for key in reversed(tuple(self._handles)):
                if self._states[key]=='OWNED':self._close(key,cleanup_check=cleanup_check,submission=submission)
            with self._state_lock:
                self._guard()
                if cleanup_check is not None:cleanup_check()
                with self._fault_lock:
                    self._guard();self.closed=True;_RETAINED.remove(self)
        except BaseException:self._fail();raise
        finally:self._lock.release()
