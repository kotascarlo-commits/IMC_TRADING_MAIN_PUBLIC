"""Native journal worker lifecycle; no Source, Job or restart authority.

Root supplies authenticated requests only after its independent native
admission. Final success still requires Root-held Exit0 before Job cleanup.
"""
from contextlib import contextmanager
import re
import threading

from storage_publish_authority import ParentAuthority
from storage_publish_channel import Client
from storage_publish_parent import ParentReadGuards
from storage_publish_manifest import decode, encoded
from storage_publish_writer_scope import build


_RETAINED=[]


class JournalEndpoint:
    def __init__(self,directory,value,binding,*,project_root,client,authority,scopes):
        if (type(client) is not Client or type(authority) is not ParentAuthority
                or type(scopes) is not ParentReadGuards or authority._client is not client
                or scopes._shared_deadline is not client._shared_deadline):
            raise ValueError('publish_journal_endpoint_binding')
        self._client=client;self._authority=authority;self._scopes=scopes
        self._deadline=client._shared_deadline
        self._lock=threading.RLock();self._fault_lock=threading.RLock()
        self._decision_lock=threading.RLock();self._fault_requested=threading.Event()
        self._busy=False;self.faulted=False;self.phase='WRITING'
        self._completed=0;self._writer=None;self.finish_attempted=False;self.finish_completed=False
        _RETAINED.append(self)
        try:
            self._writer=build(directory,value,binding,project_root=project_root,
                deadline=self._deadline,authority=authority,scopes=scopes)
            self._fresh()
        except BaseException:self._fault();raise

    def _fault(self):
        with self._decision_lock:self._fault_requested.set()
        with self._fault_lock:
            self.faulted=True;self.phase='FAULT';self.finish_completed=False

    def _cleanup_check(self):
        if self.faulted or self._fault_requested.is_set():raise ValueError('publish_journal_endpoint_faulted')
        self._deadline.check();self._scopes.check_deadlines()
        if self.faulted or self._fault_requested.is_set():raise ValueError('publish_journal_endpoint_faulted')
        return True

    def _fresh(self):
        self._cleanup_check();self._authority.check();self._scopes.assert_bound();self._cleanup_check()
        if self._writer is not None and self._writer.faulted:raise ValueError('publish_journal_writer_faulted')
        return True

    def _release_authority(self):
        self._cleanup_check();self._authority.check();self._cleanup_check()
        return True

    @contextmanager
    def _submission(self):
        with self._fault_lock:
            self._cleanup_check()
            yield

    @contextmanager
    def _operation(self):
        if not self._lock.acquire(blocking=False):
            self._fault();raise ValueError('publish_journal_endpoint_concurrent')
        try:
            if self._busy:self._fault();raise ValueError('publish_journal_endpoint_reentrant')
            self._busy=True
            try:yield
            except BaseException:self._fault();raise
            finally:self._busy=False
        finally:self._lock.release()

    @property
    def context_sha256(self):return self._writer.context_sha256

    def serve_one(self):
        with self._operation():
            self._fresh()
            if self.phase!='WRITING' or self._completed>=12:raise ValueError('publish_journal_endpoint_phase')
            body=self._client.read();self._fresh()
            def send(response):
                self._fresh()
                with self._submission():self._client.write(response)
                self._fresh()
            self._writer.process(body,send_response=send)
            self._fresh();self._completed+=1
            if self._completed==12:self.phase='JOURNAL_COMPLETE'

    def serve_finish(self):
        with self._operation():
            self._fresh()
            journal=self._writer._journal
            if (self.phase!='JOURNAL_COMPLETE' or self._completed!=12 or self._writer._sequence!=12
                    or journal.pending is not None or journal.next_step!=6 or self.finish_attempted):
                raise ValueError('publish_journal_endpoint_finish_phase')
            body=self._client.read();self._fresh();request=decode(body)
            keys={'schema_version','kind','context_sha256','ipc_sequence','request_nonce','journal_sha256'}
            if (type(request) is not dict or set(request)!=keys or encoded(request)!=body
                    or type(request['schema_version']) is not int or request['schema_version']!=2
                    or request['kind']!='WORKER_FINISH' or request['context_sha256']!=self.context_sha256
                    or type(request['ipc_sequence']) is not int or request['ipc_sequence']!=13
                    or request['journal_sha256']!=self._writer.previous
                    or type(request['request_nonce']) is not str
                    or re.fullmatch('[a-f0-9]{32}',request['request_nonce']) is None
                    or request['request_nonce'] in self._writer._nonces):
                raise ValueError('publish_journal_endpoint_finish_request')
            self._writer._nonces.add(request['request_nonce']);self.finish_attempted=True
            self.phase='WORKER_CLEANING'
            self._release_authority()
            self._scopes.release_source(authority_check=self._release_authority,submission=self._submission)
            # Writer._check() requires complete scopes; it is no longer valid.
            self._release_authority()
            self._authority.close(cleanup_check=self._cleanup_check,submission=self._submission)
            self._cleanup_check()
            self._client.close(cleanup_check=self._cleanup_check,submission=self._submission)
            with self._submission():
                self._cleanup_check()
                if (not self._scopes.release_closed or self._scopes.close_fault
                        or not self._authority.closed or self._authority.faulted
                        or not self._client.closed or self._client.faulted or self._client._current is not None
                        or any(state!='CLOSED' for state in self._scopes.release_states.values())
                        or any(state!='CLOSED' for state in self._authority._states.values())
                        or any(state!='CLOSED' for state in self._client._states.values())):
                    raise ValueError('publish_journal_endpoint_cleanup_unconfirmed')
                with self._decision_lock:
                    self._cleanup_check();self.phase='WORKER_CLEANED';self.finish_completed=True
            return True
