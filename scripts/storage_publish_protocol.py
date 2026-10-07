"""Cold supervisor-side publication receipts, without a production entrypoint.

Visible IPC bytes are never authority. A receipt exists only after publication,
native readback and checked handle close have succeeded. Receipts are local,
instance-bound objects; they cannot be reconstructed from JSON after a crash.
A cross-process host still needs a separately authenticated native channel for
the publisher-completion decision and fresh parent/mutex/phase checks.
"""
from __future__ import annotations

import ctypes
from contextlib import contextmanager
from datetime import datetime
import hashlib
import math
import os
from pathlib import Path
import re
import threading
import time
import uuid
import weakref

from offline_ack_read import read_ack_bytes
from storage_publish_manifest import decode, encoded


OPERATIONS = tuple(f'{step}:{kind}' for step in
                   ('ARCHIVE_SHM', 'ARCHIVE_WAL', 'ARCHIVE_MAIN', 'INSTALL_MAIN')
                   for kind in ('INTENT', 'RENAME', 'DONE'))
_UNCERTAIN_STREAMS = []


def _hex(value, length):
    return type(value) is str and re.fullmatch('[a-f0-9]{'+str(length)+'}', value) is not None


def _time(value):
    if type(value) is not str or len(value) > 40:
        raise ValueError('publish_protocol_birth')
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.utcoffset() is None or stamp.utcoffset().total_seconds() != 0:
        raise ValueError('publish_protocol_birth')


def context(value):
    fields = {'publish_nonce', 'generation_sha256', 'envelope_sha256',
              'parent_pid', 'parent_birth', 'worker_pid', 'worker_birth'}
    if (type(value) is not dict or set(value) != fields
            or not _hex(value['publish_nonce'], 32)
            or not all(_hex(value[k], 64) for k in ('generation_sha256', 'envelope_sha256'))
            or any(type(value[k]) is not int or not 0 < value[k] <= 2147483647
                   for k in ('parent_pid', 'worker_pid'))
            or value['parent_pid'] == value['worker_pid']):
        raise ValueError('publish_protocol_context')
    _time(value['parent_birth']); _time(value['worker_birth'])
    return decode(encoded(value))


def _deadline(deadline):
    if type(deadline) not in (int, float) or not math.isfinite(deadline):
        raise ValueError('publish_protocol_deadline')
    if time.monotonic() >= deadline:
        raise TimeoutError('publish_protocol_deadline')


def _publish(path, body, deadline):
    """One attempt only. Visible final file is not a completion receipt."""
    if os.name != 'nt':
        raise OSError('publish_protocol_windows_required')
    _deadline(deadline)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    stream = temporary.open('xb')
    try:
        if stream.write(body) != len(body):
            raise OSError('publish_protocol_short_write')
        stream.flush(); os.fsync(stream.fileno()); _deadline(deadline)
    finally:
        try:
            stream.close()
        except BaseException:
            _UNCERTAIN_STREAMS.append(stream)
            raise RuntimeError('publish_protocol_close_unconfirmed') from None
    _deadline(deadline)
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.MoveFileExW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
    api.MoveFileExW.restype = ctypes.c_int
    # WRITE_THROUGH only. Never replace an existing request/ACK generation.
    if not api.MoveFileExW(str(temporary), str(path), 0x8):
        raise ctypes.WinError(ctypes.get_last_error())
    _deadline(deadline)
    if read_ack_bytes(path, deadline=deadline) != body:
        raise ValueError('publish_protocol_readback')
    _deadline(deadline)


class _Receipt:
    """Opaque local completion object, never serialized or accepted by value."""
    __slots__ = ('__weakref__',)
    def __reduce_ex__(self, protocol):
        raise TypeError('publish_protocol_receipt_not_serializable')

    def __reduce__(self):
        raise TypeError('publish_protocol_receipt_not_serializable')


class Exchange:
    """Supervisor-local, serialized, single-use requests and completions.

    Callers provide an independently bound absolute IPC path and fresh native
    authority/phase check. That callable is not implemented or proven here.
    Any error permanently faults the exchange. No retry/resume/import API.
    """
    def __init__(self, value, *, deadline):
        self._context = context(value)
        _deadline(deadline)
        self._context_sha = hashlib.sha256(encoded(self._context)).hexdigest()
        self._deadline = deadline
        self._lock = threading.RLock()
        self._receipts = weakref.WeakKeyDictionary()
        self._sequence = 0
        self._pending = None
        self._ack_started = False
        self._nonces = set()
        self._faulted = False
        self._busy = False

    @property
    def faulted(self):
        with self._lock:
            return self._faulted

    def _check(self):
        if self._faulted:
            raise ValueError('publish_protocol_faulted')
        _deadline(self._deadline)

    @contextmanager
    def _operation(self):
        with self._lock:
            if self._busy:
                self._faulted = True
                raise ValueError('publish_protocol_reentrant')
            self._busy = True
            try:
                yield
            except BaseException:
                self._faulted = True
                raise
            finally:
                self._busy = False

    def request(self, *, operation, previous_sha256, request_nonce):
        with self._operation():
            try:
                self._check()
                if (self._pending is not None or self._sequence >= len(OPERATIONS)
                        or operation != OPERATIONS[self._sequence]
                        or not _hex(previous_sha256, 64) or not _hex(request_nonce, 32)
                        or request_nonce in self._nonces):
                    raise ValueError('publish_protocol_request')
                # Full context digest binds both native identities and proofs.
                self._pending = {'schema_version': 1, 'kind': 'REQUEST',
                    'context_sha256': self._context_sha, 'sequence': self._sequence,
                    'operation': operation, 'previous_sha256': previous_sha256,
                    'request_nonce': request_nonce}
                self._nonces.add(request_nonce)
                self._ack_started = False
                return decode(encoded(self._pending))
            except BaseException:
                self._faulted = True
                raise

    def publish_ack(self, request, path, *, authority_check):
        with self._operation():
            try:
                self._check()
                if (self._pending is None or self._ack_started or type(request) is not dict
                        or encoded(request) != encoded(self._pending)
                        or not callable(authority_check)):
                    raise ValueError('publish_protocol_request_binding')
                self._ack_started = True
                if authority_check() is not True:
                    raise ValueError('publish_protocol_authority_lost')
                self._check() # Callback may have caught a nested fault.
                path = Path(path)
                if not path.is_absolute():
                    raise ValueError('publish_protocol_absolute_path')
                ack = {**self._pending, 'kind': 'ACK'}
                body = encoded(ack)
                if len(body) > 1024:
                    raise ValueError('publish_protocol_frame_size')
                _publish(path, body, self._deadline)
                if authority_check() is not True:
                    raise ValueError('publish_protocol_authority_lost')
                self._check()
                receipt = _Receipt()
                self._receipts[receipt] = body
                return receipt
            except BaseException:
                self._faulted = True
                raise

    def consume(self, ack_bytes, receipt, *, authority_check):
        with self._operation():
            try:
                self._check()
                if (type(receipt) is not _Receipt or receipt not in self._receipts
                        or type(ack_bytes) is not bytes
                        or self._pending is None):
                    raise ValueError('publish_protocol_completion_missing')
                # Consumption precedes all further checks/actions. Parallel,
                # stale or failed consumption cannot resurrect this receipt.
                published = self._receipts.pop(receipt)
                expected = {**self._pending, 'kind': 'ACK'}
                self._pending = None
                if ack_bytes != published or ack_bytes != encoded(expected):
                    raise ValueError('publish_protocol_ack_binding')
                if not callable(authority_check) or authority_check() is not True:
                    raise ValueError('publish_protocol_authority_lost')
                self._check()
                self._sequence += 1
                return decode(encoded(expected))
            except BaseException:
                self._faulted = True
                raise
