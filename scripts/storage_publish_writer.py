"""Cold single-journal writer endpoint; no CLI, Source I/O or restart permit.

The host owns native channel/job/namespace/phase binding. Its authority callback
must establish those facts; a callback returning True alone is not their proof.
Responses certify this instance's checked writes, never surviving recovery.
"""
from contextlib import contextmanager
import hashlib
import re
import threading

from storage_publish_deadline import Deadline
from storage_publish_manifest import Journal, STEPS, decode, encoded, generation


def digest(body):
    return hashlib.sha256(body).hexdigest()


def context(value, *, role='journal'):
    keys = {'publish_nonce', 'generation_sha256', 'envelope_sha256', 'role',
            'parent_pid', 'parent_birth', 'worker_pid', 'worker_birth'}
    if (role not in ('journal','rename') or type(value) is not dict or set(value) != keys or value['role'] != role
            or type(value['role']) is not str
            or type(value['publish_nonce']) is not str
            or re.fullmatch('[a-f0-9]{32}', value['publish_nonce']) is None
            or any(type(value[k]) is not str or re.fullmatch('[a-f0-9]{64}', value[k]) is None
                   for k in ('generation_sha256', 'envelope_sha256'))
            or any(type(value[k]) is not int or not 0 < value[k] <= 2147483647
                   for k in ('parent_pid', 'worker_pid'))
            or value['parent_pid'] == value['worker_pid']
            or any(type(value[k]) is not int or not 0 < value[k] < 2**63
                   for k in ('parent_birth', 'worker_birth'))):
        raise ValueError('publish_writer_context')
    return decode(encoded(value))


class Writer:
    """One fresh schema2 journal and at most twelve once-only requests.

    No resume/retry API. A failed response write faults this writer even when
    its durable event exists. The parent must treat that completion as unknown.
    IPC sequence starts at 1; journal sequence starts at 0.
    """
    def __init__(self, directory, value, binding, *, project_root, deadline, authority_check,
                 prepared_sentinel=None):
        if type(deadline) is not Deadline or not callable(authority_check):
            raise ValueError('publish_writer_authority')
        # Callback execution cannot alter the generation whose hash was bound.
        value = decode(encoded(value))
        binding = decode(encoded(binding))
        prepared_sentinel = None if prepared_sentinel is None else decode(encoded(prepared_sentinel))
        generation(value, project_root=project_root)
        self._context = context(binding)
        if (binding['generation_sha256'] != digest(encoded(value))
                or binding['publish_nonce'] != value['publish_nonce']):
            raise ValueError('publish_writer_generation')
        self._context_sha = digest(encoded(self._context))
        self._deadline = deadline
        self._authority = authority_check
        self._lock = threading.RLock()
        self._busy = False
        self.faulted = False
        self._sequence = 0
        self._nonces = set()
        self._journal = None
        with self._operation():
            self._check()
            self._journal = Journal(directory, value, project_root=project_root, schema_version=2,
                                    prepared_sentinel=prepared_sentinel)
            self._check()

    @contextmanager
    def _operation(self):
        # Reject another thread immediately; do not hide concurrency by queueing.
        if not self._lock.acquire(blocking=False):
            self.faulted = True
            raise ValueError('publish_writer_concurrent')
        try:
            if self._busy:
                self.faulted = True
                raise ValueError('publish_writer_reentrant')
            self._busy = True
            try:
                yield
            except BaseException:
                self.faulted = True
                raise
            finally:
                self._busy = False
        finally:
            self._lock.release()

    def _check(self):
        if self.faulted:
            raise ValueError('publish_writer_faulted')
        self._deadline.check()
        if self._authority() is not True:
            raise ValueError('publish_writer_authority_lost')
        if self.faulted:
            raise ValueError('publish_writer_faulted')
        self._deadline.check()

    @property
    def previous(self):
        return self._journal.previous

    @property
    def context_sha256(self):
        return self._context_sha

    def process(self, body, *, send_response):
        with self._operation():
            self._check()
            if not callable(send_response) or self._sequence >= 12:
                raise ValueError('publish_writer_finished_or_sender')
            request = decode(body)
            keys = {'schema_version', 'kind', 'context_sha256', 'ipc_sequence',
                    'journal_sequence', 'request_nonce', 'previous_sha256', 'step', 'event_kind', 'evidence'}
            i = self._sequence
            if (type(request) is not dict or set(request) != keys
                    or type(request['schema_version']) is not int or request['schema_version'] != 2
                    or request['kind'] != 'JOURNAL_REQUEST'
                    or type(request['ipc_sequence']) is not int or request['ipc_sequence'] != i + 1
                    or type(request['journal_sequence']) is not int or request['journal_sequence'] != i
                    or request['context_sha256'] != self._context_sha
                    or request['previous_sha256'] != self._journal.previous
                    or request['step'] != STEPS[i // 2]
                    or request['event_kind'] != ('DONE' if i % 2 else 'INTENT')
                    or type(request['request_nonce']) is not str
                    or re.fullmatch('[a-f0-9]{32}', request['request_nonce']) is None
                    or request['request_nonce'] in self._nonces):
                raise ValueError('publish_writer_request')
            # Bounded canonical bytes have exactly one hash on both sides.
            if encoded(request) != body:
                raise ValueError('publish_writer_noncanonical')
            self._nonces.add(request['request_nonce'])
            self._check()
            if i % 2:
                self._journal.done(request['step'], request['evidence'])
            else:
                self._journal.intent(request['step'], request['evidence'])
            self._check()
            response = encoded({'schema_version': 2, 'kind': 'JOURNAL_COMPLETED',
                'context_sha256': self._context_sha, 'ipc_sequence': i + 1,
                'journal_sequence': i, 'request_nonce': request['request_nonce'],
                'request_sha256': digest(body), 'event_sha256': self._journal.previous})
            # No response if write/fsync/checked-close/post-authority is unknown.
            send_response(response)
            self._check()
            self._sequence += 1
            return response
