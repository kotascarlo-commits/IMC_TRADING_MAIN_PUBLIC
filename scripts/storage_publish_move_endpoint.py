"""Cold native four-move worker; Root alone owns the persistent journal.

There is no CLI or production authority here. Root separately validates copy
evidence, Gate/ledger, Jobs and each durable INTENT/DONE. A request from that
authenticated native Root is a command, never a visible-file fsync receipt.
Faults retain remaining or uncertain guards. Source release needs a separate
authenticated command; this endpoint never grants whole-Job cleanup or restart.
"""
import ctypes
import os
from pathlib import Path
import re
import threading
from contextlib import contextmanager

from storage_publish_authority import ParentAuthority
from storage_publish_channel import Client, _FileTime
from storage_publish_deadline import Deadline
from storage_publish_guard import RenameGuards
from storage_publish_manifest import decode, encoded
from storage_publish_transaction import MoveChecks, MOVES
from storage_publish_writer import context, digest


_RETAINED=[]


class MoveEndpoint:
    def __init__(self,value,binding,*,project_root,client,authority,guards):
        if (type(client) is not Client or type(authority) is not ParentAuthority
                or type(guards) is not RenameGuards or authority._client is not client
                or type(client._shared_deadline) is not Deadline
                or guards._shared_deadline is not client._shared_deadline
                or guards.attached_directories is None or guards.attached_sentinels is None):
            raise ValueError('publish_endpoint_native_binding')
        value=decode(encoded(value));binding=context(binding,role='rename')
        root=Path(project_root).resolve(strict=True)
        if (binding['publish_nonce']!=client._nonce or client._role!='rename'
                or binding['generation_sha256']!=digest(encoded(value))
                or binding['parent_pid']!=client._pid or binding['parent_birth']!=client._birth
                or binding['worker_pid']!=os.getpid() or authority._snapshot['nonce']!=binding['publish_nonce']
                or authority._marker!=root/'RUNTIME/maintenance-state.json'):
            raise ValueError('publish_endpoint_context')
        api=client.api;api.GetCurrentProcess.argtypes=[];api.GetCurrentProcess.restype=ctypes.c_void_p
        birth,exit,kernel,user=(_FileTime() for _ in range(4))
        if (not api.GetProcessTimes(api.GetCurrentProcess(),ctypes.byref(birth),ctypes.byref(exit),
                                   ctypes.byref(kernel),ctypes.byref(user))
                or ((birth.high<<32)|birth.low)!=binding['worker_birth']):
            raise ValueError('publish_endpoint_worker_birth')
        self._client=client;self._authority=authority;self._guards=guards
        self._deadline=client._shared_deadline;self._context_sha=digest(encoded(binding))
        self._sequence=0;self._nonces=set();self._intents=set()
        self._completed=0;self.phase='MOVING';self.close_attempted=False
        self.source_closed=False;self.release_ack_completed=False
        self._release_request_sha256=None;self.finish_attempted=False;self.finish_completed=False
        self._fault_requested=threading.Event()
        self._decision_lock=threading.RLock()
        self._lock=threading.RLock();self._busy=False;self.faulted=False
        self._fault_lock=threading.RLock()
        self._checks=None
        self._lock.acquire();self._busy=True
        _RETAINED.append(self)
        try:
            self._checks=MoveChecks(value,guards,project_root=str(root),authority_check=self._fresh,
                journal_directory=root/'.local-tools'/('storage-publish-'+binding['publish_nonce'])/'journal',
                require_journal_pin=False)
            self._checks.preflight()
            self._fresh()
        except BaseException:self._fault();raise
        finally:self._busy=False;self._lock.release()

    def _fresh(self):
        if self.faulted or self._fault_requested.is_set():raise ValueError('publish_endpoint_faulted')
        self._deadline.check();self._authority.check();self._guards.assert_bound()
        if self.faulted or self._fault_requested.is_set():raise ValueError('publish_endpoint_faulted')
        self._deadline.check()
        return True

    @property
    def context_sha256(self):return self._context_sha

    def _fault(self):
        with self._decision_lock:self._fault_requested.set()
        with self._fault_lock:self.faulted=True;self.phase='FAULT';self.finish_completed=False

    @contextmanager
    def _submission(self):
        # A native mutation or ACK permitted here is already in flight. A
        # concurrent fault waits for it; execution/delivery may have occurred.
        with self._fault_lock:
            if self.faulted or self._fault_requested.is_set():raise ValueError('publish_endpoint_faulted')
            self._deadline.check();self._guards.check_deadlines()
            yield

    def serve_one(self):
        # Receive, native mutation and response constitute one serialized step.
        # The same authenticated Client used by ParentAuthority receives it.
        if not self._lock.acquire(blocking=False):
            self._fault();raise ValueError('publish_endpoint_concurrent')
        try:
            if self._busy:
                self._fault();raise ValueError('publish_endpoint_reentrant')
            self._busy=True
            try:
                self._fresh()
                if self._sequence>=4:raise ValueError('publish_endpoint_finished')
                body=self._client.read();request=decode(body);i=self._sequence
                keys={'schema_version','kind','context_sha256','ipc_sequence','intent_sequence',
                      'request_nonce','intent_sha256','step'}
                if (type(request) is not dict or set(request)!=keys or encoded(request)!=body
                        or type(request['schema_version']) is not int or request['schema_version']!=2
                        or request['kind']!='MOVE_REQUEST' or request['context_sha256']!=self._context_sha
                        or type(request['ipc_sequence']) is not int or request['ipc_sequence']!=i+1
                        or type(request['intent_sequence']) is not int or request['intent_sequence']!=2*i
                        or request['step']!=MOVES[i][0]
                        or type(request['request_nonce']) is not str
                        or re.fullmatch('[a-f0-9]{32}',request['request_nonce']) is None
                        or request['request_nonce'] in self._nonces
                        or type(request['intent_sha256']) is not str
                        or re.fullmatch('[a-f0-9]{64}',request['intent_sha256']) is None
                        or request['intent_sha256'] in self._intents):
                    raise ValueError('publish_endpoint_request')
                self._nonces.add(request['request_nonce']);self._intents.add(request['intent_sha256'])
                self._checks._check()
                step,key,destination=MOVES[i]
                self._checks._absent(self._checks.value['paths'][destination])
                if self._checks.value['members'][key] is None:
                    self._checks._absent(self._checks.value['paths'][key])
                # Consume before the sole native call; there is no resend path.
                self._sequence+=1
                self._fresh()
                if self._checks.value['members'][key] is not None:
                    self._guards.move(key,self._checks.value['paths'][destination],submission=self._submission)
                if step=='INSTALL_MAIN':self._checks._installed=True
                self._checks._moved_sources.append(key)
                self._checks._check()
                proof=self._checks._proof(key,destination,done=True)
                response=encoded({'schema_version':2,'kind':'MOVE_COMPLETED',
                    'context_sha256':self._context_sha,'ipc_sequence':i+1,'intent_sequence':2*i,
                    'request_nonce':request['request_nonce'],'request_sha256':digest(body),
                    'intent_sha256':request['intent_sha256'],'step':step,'evidence':proof,
                    'native_handles_closed':False,'restart_allowed':False})
                self._fresh()
                with self._submission():self._client.write(response)
                self._fresh()
                with self._submission():
                    self._completed+=1
                    if self._completed==4:self.phase='MOVES_COMPLETE'
                return response
            except BaseException:self._fault();raise
            finally:self._busy=False
        finally:self._lock.release()

    def _release_authority(self):
        # Source pins are closing; ParentAuthority's independently owned marker,
        # thread and mutex probes remain fully checked until Source ACK ends.
        if self.faulted or self._fault_requested.is_set():raise ValueError('publish_endpoint_faulted')
        self._deadline.check();self._guards.check();self._authority.check()
        self._guards.check();self._deadline.check()
        if self.faulted or self._fault_requested.is_set():raise ValueError('publish_endpoint_faulted')
        return True

    def serve_release(self):
        """One native Root command after its durable INSTALL_MAIN-DONE.

        The authenticated command conveys Root's decision; neither this hash
        nor a visible journal file independently proves its fsync completion.
        Source cleanup never certifies channel, Authority or whole-Job cleanup.
        """
        if not self._lock.acquire(blocking=False):
            self._fault();raise ValueError('publish_endpoint_concurrent')
        try:
            if self._busy:
                self._fault();raise ValueError('publish_endpoint_reentrant')
            self._busy=True
            try:
                self._release_authority()
                if (self.phase!='MOVES_COMPLETE' or self._completed!=4 or self._sequence!=4
                        or self.close_attempted):raise ValueError('publish_endpoint_release_phase')
                self._fresh()
                body=self._client.read();request=decode(body)
                fields={'schema_version','kind','context_sha256','ipc_sequence','journal_sequence',
                        'journal_step','journal_kind','journal_sha256','request_nonce'}
                if (type(request) is not dict or set(request)!=fields or encoded(request)!=body
                        or type(request['schema_version']) is not int or request['schema_version']!=2
                        or request['kind']!='SOURCE_GUARD_RELEASE' or request['context_sha256']!=self._context_sha
                        or type(request['ipc_sequence']) is not int or request['ipc_sequence']!=5
                        or type(request['journal_sequence']) is not int or request['journal_sequence']!=7
                        or request['journal_step']!='INSTALL_MAIN' or request['journal_kind']!='DONE'
                        or type(request['journal_sha256']) is not str
                        or re.fullmatch('[a-f0-9]{64}',request['journal_sha256']) is None
                        or type(request['request_nonce']) is not str
                        or re.fullmatch('[a-f0-9]{32}',request['request_nonce']) is None
                        or request['request_nonce'] in self._nonces):
                    raise ValueError('publish_endpoint_release_request')
                self._nonces.add(request['request_nonce']);self._sequence=5
                self._release_request_sha256=digest(body)
                self.close_attempted=True;self.phase='SOURCE_CLOSING'
                self._fresh();self._checks._check()
                self._guards.release_source(authority_check=self._release_authority,submission=self._submission)
                self.source_closed=self._guards.release_closed
                if not self.source_closed:raise ValueError('publish_endpoint_source_cleanup_unconfirmed')
                self._release_authority()
                response=encoded({'schema_version':2,'kind':'SOURCE_GUARDS_CLOSED',
                    'context_sha256':self._context_sha,'ipc_sequence':5,'journal_sequence':7,
                    'journal_sha256':request['journal_sha256'],'request_nonce':request['request_nonce'],
                    'request_sha256':digest(body),'source_guard_cleanup':True,
                    'job_cleanup':False,'restart_allowed':False})
                with self._submission():self._client.write(response)
                self._release_authority()
                with self._submission():self.release_ack_completed=True;self.phase='SOURCE_RELEASED'
                return response
            except BaseException:
                # A lost response or post-close fault can leave no Source FDs.
                self.source_closed=self._guards.release_closed
                self._fault();raise
            finally:self._busy=False
        finally:self._lock.release()

    def _cleanup_guard(self):
        # Authority/query/channel handles are being dismantled. Only resource-
        # independent fault/deadline checks are valid during this phase.
        if self.faulted or self._fault_requested.is_set():raise ValueError('publish_endpoint_faulted')
        self._deadline.check();self._guards.check_deadlines()
        if self.faulted or self._fault_requested.is_set():raise ValueError('publish_endpoint_faulted')
        return True

    def serve_finish(self):
        """Root requests worker cleanup; only Root's native Exit0/Job proves it.

        No final ACK, Source re-close, whole-Job grant or restart permission.
        Parent liveness is sampled while its native handles are still usable.
        """
        if not self._lock.acquire(blocking=False):
            self._fault();raise ValueError('publish_endpoint_concurrent')
        try:
            if self._busy:self._fault();raise ValueError('publish_endpoint_reentrant')
            self._busy=True
            try:
                self._cleanup_guard()
                if (self.phase!='SOURCE_RELEASED' or self._completed!=4 or self._sequence!=5
                        or not self.source_closed or not self.release_ack_completed or self.finish_attempted):
                    raise ValueError('publish_endpoint_finish_phase')
                self._release_authority()
                body=self._client.read();self._release_authority();request=decode(body)
                keys={'schema_version','kind','context_sha256','ipc_sequence','request_nonce','release_request_sha256'}
                if (type(request) is not dict or set(request)!=keys or encoded(request)!=body
                        or type(request['schema_version']) is not int or request['schema_version']!=2
                        or request['kind']!='WORKER_FINISH' or request['context_sha256']!=self._context_sha
                        or type(request['ipc_sequence']) is not int or request['ipc_sequence']!=6
                        or request['release_request_sha256']!=self._release_request_sha256
                        or type(request['request_nonce']) is not str
                        or re.fullmatch('[a-f0-9]{32}',request['request_nonce']) is None
                        or request['request_nonce'] in self._nonces):
                    raise ValueError('publish_endpoint_finish_request')
                self._nonces.add(request['request_nonce']);self._sequence=6
                self.finish_attempted=True;self.phase='WORKER_CLEANING'
                self._release_authority()
                self._authority.close(cleanup_check=self._cleanup_guard,submission=self._submission)
                self._cleanup_guard()
                self._client.close(cleanup_check=self._cleanup_guard,submission=self._submission)
                with self._submission():
                    self._cleanup_guard()
                    if (not self._authority.closed or self._authority.faulted
                            or not self._client.closed or self._client.faulted or self._client._current is not None
                            or any(state!='CLOSED' for state in self._authority._states.values())
                            or any(state!='CLOSED' for state in self._client._states.values())):
                        raise ValueError('publish_endpoint_worker_cleanup_unconfirmed')
                    # Fault registration shares this short decision lock. It
                    # never waits for a permitted native Close to complete.
                    with self._decision_lock:
                        self._cleanup_guard()
                        self.phase='WORKER_CLEANED';self.finish_completed=True
                return True
            except BaseException:self._fault();raise
            finally:self._busy=False
        finally:self._lock.release()
