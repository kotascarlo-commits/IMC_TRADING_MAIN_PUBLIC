"""Cold Python client for IMCStorageChannel; no publication authority.

Native peer identity and bounded framing are transport facts. The host still
proves Job membership, mutex ownership, ACTIVE generation and each operation.
Unconfirmed I/O retains all native memory/handles. No reconnect or send retry.
"""
import ctypes
from contextlib import contextmanager, nullcontext
import math
import os
import re
import struct
import threading
import time
from storage_publish_deadline import Deadline


_RETAINED = []


class _Overlapped(ctypes.Structure):
    _fields_ = [('internal',ctypes.c_size_t),('internal_high',ctypes.c_size_t),
                ('offset',ctypes.c_uint32),('offset_high',ctypes.c_uint32),
                ('event',ctypes.c_void_p)]


class _FileTime(ctypes.Structure):
    _fields_ = [('low',ctypes.c_uint32),('high',ctypes.c_uint32)]


class Client:
    def __init__(self, nonce, role, *, parent_pid, parent_birth, seconds=None, deadline=None):
        if os.name!='nt':raise OSError('publish_channel_windows_required')
        if (ctypes.sizeof(ctypes.c_void_p)!=8 or ctypes.sizeof(_Overlapped)!=32
                or _Overlapped.offset.offset!=16 or _Overlapped.event.offset!=24
                or ctypes.sizeof(_FileTime)!=8):
            raise ValueError('publish_channel_abi')
        if (type(nonce) is not str or re.fullmatch('[a-f0-9]{32}',nonce) is None
                or type(role) is not str or re.fullmatch('[a-z][a-z0-9_]{0,31}',role) is None
                or type(parent_pid) is not int or not 0<parent_pid<=2147483647
                or type(parent_birth) is not int or not 0<parent_birth<2**63
                or (deadline is None and (type(seconds) not in (int,float)
                    or not math.isfinite(seconds) or not 0<seconds<=21600))
                or (deadline is not None and (type(deadline) is not Deadline or seconds is not None))):
            raise ValueError('publish_channel_config')
        self._shared_deadline=deadline
        self._nonce=nonce;self._role=role
        self._deadline=time.monotonic()+seconds if deadline is None else None
        self._lock=threading.Lock()
        self._state_lock=threading.RLock()
        self._fault_lock=threading.RLock()
        self._fault_requested=threading.Event()
        self.faulted=False;self.closed=False;self._current=None
        self._states={'peer':'EMPTY','pipe':'EMPTY'}
        self._handles={'peer':None,'pipe':None}
        self._pid=parent_pid;self._birth=parent_birth
        self.api=ctypes.WinDLL('kernel32',use_last_error=True)
        signatures={
            'OpenProcess':([ctypes.c_uint32,ctypes.c_int,ctypes.c_uint32],ctypes.c_void_p),
            'GetProcessTimes':([ctypes.c_void_p]+[ctypes.POINTER(_FileTime)]*4,ctypes.c_int),
            'CreateFileW':([ctypes.c_wchar_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p,
                            ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p],ctypes.c_void_p),
            'GetNamedPipeServerProcessId':([ctypes.c_void_p,ctypes.POINTER(ctypes.c_uint32)],ctypes.c_int),
            'WaitForSingleObject':([ctypes.c_void_p,ctypes.c_uint32],ctypes.c_uint32),
            'CreateEventW':([ctypes.c_void_p,ctypes.c_int,ctypes.c_int,ctypes.c_wchar_p],ctypes.c_void_p),
            'ReadFile':([ctypes.c_void_p,ctypes.c_void_p,ctypes.c_uint32,
                         ctypes.POINTER(ctypes.c_uint32),ctypes.c_void_p],ctypes.c_int),
            'WriteFile':([ctypes.c_void_p,ctypes.c_void_p,ctypes.c_uint32,
                          ctypes.POINTER(ctypes.c_uint32),ctypes.c_void_p],ctypes.c_int),
            'GetOverlappedResult':([ctypes.c_void_p,ctypes.c_void_p,
                                    ctypes.POINTER(ctypes.c_uint32),ctypes.c_int],ctypes.c_int),
            'GetProcessHeap':([],ctypes.c_void_p),
            'HeapAlloc':([ctypes.c_void_p,ctypes.c_uint32,ctypes.c_size_t],ctypes.c_void_p),
            'HeapFree':([ctypes.c_void_p,ctypes.c_uint32,ctypes.c_void_p],ctypes.c_int),
            'CloseHandle':([ctypes.c_void_p],ctypes.c_int)}
        for name,(arguments,result) in signatures.items():
            function=getattr(self.api,name);function.argtypes=arguments;function.restype=result
        _RETAINED.append(self)
        try:
            self._check()
            self._heap=self.api.GetProcessHeap()
            if not self._heap:raise OSError('publish_channel_memory')
            peer=self.api.OpenProcess(0x101000,False,parent_pid)
            self._own('peer',peer)
            birth,exit,kernel,user=(_FileTime() for _ in range(4))
            if not self.api.GetProcessTimes(peer,ctypes.byref(birth),ctypes.byref(exit),
                                            ctypes.byref(kernel),ctypes.byref(user)):
                self._error()
            if ((birth.high<<32)|birth.low) != parent_birth:
                raise ValueError('publish_channel_peer_birth')
            self._check()
            pipe=self.api.CreateFileW('\\\\.\\pipe\\imc-storage-'+nonce+'-'+role,
                0xC0000000,0,None,3,0x40000000,None)
            self._own('pipe',pipe)
            self._peer()
        except BaseException:
            self._fail()
            raise

    def _fail(self):
        with self._fault_lock:
            if not self.closed:self._fault_requested.set()
        with self._state_lock:
            if not self.closed:self.faulted=True

    def _cleanup_check(self):
        if self.faulted or self.closed or self._fault_requested.is_set():raise ValueError('publish_channel_unconfirmed')
        self._time_check()
        if self.faulted or self.closed or self._fault_requested.is_set():raise ValueError('publish_channel_unconfirmed')

    def _time_check(self):
        if self._shared_deadline is not None:self._shared_deadline.check()
        elif time.monotonic()>=self._deadline:raise TimeoutError('publish_channel_deadline')

    def _error(self):
        code=ctypes.get_last_error()
        raise OSError('publish_channel_native_'+str(code))

    def _own(self,name,handle):
        if handle in (None,ctypes.c_void_p(-1).value):self._error()
        self._handles[name]=handle;self._states[name]='OWNED'

    def _check(self):
        if self.faulted or self.closed or self._fault_requested.is_set():raise ValueError('publish_channel_unconfirmed')
        self._time_check()
        if self.faulted or self.closed or self._fault_requested.is_set():raise ValueError('publish_channel_unconfirmed')
        if self._states['peer']=='OWNED':
            status=self.api.WaitForSingleObject(self._handles['peer'],0)
            if status==0:raise ValueError('publish_channel_peer_exited')
            if status!=258:self._error()

    def _peer(self):
        self._check();actual=ctypes.c_uint32()
        if not self.api.GetNamedPipeServerProcessId(self._handles['pipe'],ctypes.byref(actual)):
            self._error()
        if actual.value!=self._pid:raise ValueError('publish_channel_peer_pid')
        self._check()

    @contextmanager
    def _operation(self, *, cleanup=False):
        if not self._lock.acquire(blocking=False):
            self._fail()
            raise ValueError('publish_channel_concurrent')
        try:
            self._cleanup_check() if cleanup else self._check()
            yield
        except BaseException:
            self._fail()
            raise
        finally:self._lock.release()

    def _allocate(self,length):
        if self._current is not None:raise ValueError('publish_channel_operation_pending')
        operation={'buffer':None,'overlap':None,'overlap_memory':None,'event':None,'event_state':'EMPTY',
                   'confirmed':False,'submitted':False,'length':length}
        self._current=operation
        # Native allocations have no Python finalizer. Even interpreter
        # teardown cannot free memory still referenced by pending kernel I/O.
        operation['buffer']=self.api.HeapAlloc(self._heap,8,length)
        if not operation['buffer']:raise OSError('publish_channel_memory')
        operation['overlap_memory']=self.api.HeapAlloc(self._heap,8,32)
        if not operation['overlap_memory']:raise OSError('publish_channel_memory')
        operation['overlap']=_Overlapped.from_address(operation['overlap_memory'])
        event=self.api.CreateEventW(None,True,False,None)
        if event in (None,ctypes.c_void_p(-1).value):self._error()
        operation['event']=event;operation['event_state']='OWNED'
        operation['overlap'].event=event
        return operation

    def _complete(self,operation,immediate,code):
        if not immediate and code!=997:raise OSError('publish_channel_native_'+str(code))
        count=ctypes.c_uint32()
        while True:
            self._check()
            if not immediate:
                status=self.api.WaitForSingleObject(operation['event'],0)
                if status==258:time.sleep(.002);continue
                if status!=0:self._error()
            if self.api.GetOverlappedResult(self._handles['pipe'],
                    ctypes.byref(operation['overlap']),ctypes.byref(count),False):break
            error=ctypes.get_last_error()
            if error==996:immediate=False;time.sleep(.002);continue
            raise OSError('publish_channel_native_'+str(error))
        operation['confirmed']=True;self._check()
        return count.value

    def _close(self,handle,states,key,*,cleanup_check=None,submission=None):
        self._cleanup_check()
        if states[key]!='OWNED':raise ValueError('publish_channel_close_state')
        if cleanup_check is not None:cleanup_check()
        with submission() if submission is not None else nullcontext():
            if cleanup_check is not None:self._cleanup_check();cleanup_check()
            states[key]='CLOSING'
            try:
                if not self.api.CloseHandle(handle):self._error()
                states[key]='CLOSED'
                if cleanup_check is not None and states is self._states:self._handles[key]=None
            except BaseException:
                states[key]='FAULT';self._fail()
                raise
        if cleanup_check is not None:self._cleanup_check();cleanup_check()

    def _release(self,operation):
        if not operation['confirmed']:raise ValueError('publish_channel_completion_unconfirmed')
        self._close(operation['event'],operation,'event_state')
        operation['event']=None
        for key in ('buffer','overlap_memory'):
            self._cleanup_check()
            if not self.api.HeapFree(self._heap,0,operation[key]):
                self._fail()
                raise RuntimeError('publish_channel_free_unconfirmed')
            operation[key]=None
        operation['overlap']=None
        self._current=None

    def _read_exact(self,length):
        chunks=[];remaining=length
        while remaining:
            self._peer();operation=self._allocate(remaining);count=ctypes.c_uint32()
            self._check()
            operation['submitted']=True
            immediate=self.api.ReadFile(self._handles['pipe'],operation['buffer'],remaining,
                ctypes.byref(count),ctypes.byref(operation['overlap']))
            code=0 if immediate else ctypes.get_last_error()
            read=self._complete(operation,immediate,code)
            if not 0<read<=remaining:raise ValueError('publish_channel_read_progress')
            chunks.append(ctypes.string_at(operation['buffer'],read));remaining-=read
            self._release(operation);self._peer()
        return b''.join(chunks)

    def read(self):
        with self._operation():
            self._peer();length=struct.unpack('<I',self._read_exact(4))[0]
            if not 0<length<=131072:raise ValueError('publish_channel_frame_size')
            body=self._read_exact(length);self._peer()
            return body

    def write(self,body):
        with self._operation():
            self._peer()
            if type(body) is not bytes or not 0<len(body)<=131072:
                raise ValueError('publish_channel_frame_size')
            frame=struct.pack('<I',len(body))+body
            operation=self._allocate(len(frame));ctypes.memmove(operation['buffer'],frame,len(frame))
            self._check();count=ctypes.c_uint32();operation['submitted']=True
            immediate=self.api.WriteFile(self._handles['pipe'],operation['buffer'],len(frame),
                ctypes.byref(count),ctypes.byref(operation['overlap']))
            code=0 if immediate else ctypes.get_last_error()
            written=self._complete(operation,immediate,code)
            if written!=len(frame):raise ValueError('publish_channel_write_partial')
            self._release(operation);self._peer()

    def close(self,*,cleanup_check=None,submission=None):
        if ((cleanup_check is None)!=(submission is None) or
                cleanup_check is not None and (not callable(cleanup_check) or not callable(submission))):
            self._fail();raise ValueError('publish_channel_cleanup_binding')
        with self._operation(cleanup=True):
            if self._current is not None:raise ValueError('publish_channel_close_unconfirmed')
            if cleanup_check is not None:self._peer();cleanup_check()
            for key in ('pipe','peer'):
                if self._states[key]=='OWNED':
                    if cleanup_check is not None and key=='peer':self._check()
                    self._close(self._handles[key],self._states,key,cleanup_check=cleanup_check,submission=submission)
                    self._handles[key]=None
            with self._state_lock:
                self._cleanup_check()
                if cleanup_check is not None:cleanup_check()
                with self._fault_lock:
                    self._cleanup_check()
                    self.closed=True;_RETAINED.remove(self)
