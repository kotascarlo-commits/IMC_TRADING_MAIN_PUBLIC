"""Bounded native ACK reader used by copy workers and local receipts.

Only native sharing/access errors on a READ can retry. The first error fixes
one retry boundary; no parser, identity check, order or send is retried.
Deadlines bound accepted results cooperatively; synchronous native calls and
their mandatory cleanup are not forcibly interrupted by this utility.
"""
import math
import ctypes
import os
from pathlib import Path
import time
from storage_publish_parent import ParentReadGuards
from storage_publish_guard import _RETAINED_GUARDS


class _RetryableRead(Exception):
    def __init__(self, error, failed_at):
        self.error, self.failed_at = error, failed_at


def _retryable(error):
    code = getattr(error, 'winerror', None)
    return isinstance(error, PermissionError) and type(code) is int and code in (5, 32)


def _attempt(path, deadline):
    # Reuse the checked native READ-only handle/metadata implementation. No
    # context entry: ACK reads must not create directory pins or sentinels.
    owner = ParentReadGuards(path.parent, {'ack': path}, deadline=deadline)
    handle = None
    try:
        owner.check()
        try:
            handle = owner._open(path)
        except PermissionError as error:
            failed_at = time.monotonic()
            if _retryable(error): raise _RetryableRead(error, failed_at) from None
            raise
        owner.members['ack']['handle'] = handle
        owner.check()
        before = owner._info(handle)
        owner.api.GetFileType.argtypes = [ctypes.c_void_p]
        owner.api.GetFileType.restype = ctypes.c_uint32
        if owner.api.GetFileType(handle) != 1:
            raise ValueError('ack_read_not_disk')
        class Tag(ctypes.Structure):
            _fields_ = [('attributes', ctypes.c_uint32), ('tag', ctypes.c_uint32)]
        tag = Tag()
        if not owner.api.GetFileInformationByHandleEx(handle, 9, ctypes.byref(tag), ctypes.sizeof(tag)):
            code = ctypes.get_last_error()
            raise ctypes.WinError(code)  # Metadata errors are never retryable.
        if tag.attributes & 0x400 or tag.tag or before['bytes'] > 1024:
            raise ValueError('ack_read_shape_or_size')
        expected = os.path.normcase(str(path))
        if owner._native_path(handle) != expected:
            raise ValueError('ack_read_path')
        owner.check()
        buffer, count = ctypes.create_string_buffer(1025), ctypes.c_uint32()
        if not owner.api.ReadFile(handle, buffer, len(buffer), ctypes.byref(count), None):
            code, failed_at = ctypes.get_last_error(), time.monotonic()
            error = ctypes.WinError(code)
            if _retryable(error): raise _RetryableRead(error, failed_at) from None
            raise error
        owner.check()
        if count.value != before['bytes'] or count.value > 1024:
            raise ValueError('ack_read_byte_count')
        if owner._info(handle) != before or owner._native_path(handle) != expected:
            raise ValueError('ack_read_changed')
        owner.check()
        return buffer.raw[:count.value]
    finally:
        if handle is not None:
            try:
                owner._close(handle)
            except BaseException:
                # _close retains owner/API/raw handle in the native guard pool.
                # This final failure overrides any previously retryable error.
                if owner not in _RETAINED_GUARDS:
                    _RETAINED_GUARDS.append(owner)
                raise RuntimeError('ack_read_close_unconfirmed') from None
            owner.members['ack']['handle'] = None


def read_ack_bytes(path, *, deadline):
    if os.name != 'nt':
        raise OSError('ack_read_windows_required')
    if type(deadline) not in (int, float) or not math.isfinite(deadline):
        raise ValueError('ack_read_deadline')
    retry_until = None
    last_error = None
    path = Path(path).absolute()
    while True:
        now = time.monotonic()
        if now >= deadline:
            raise TimeoutError('ack_read_deadline')
        if retry_until is not None and now >= retry_until:
            raise last_error
        attempt_deadline = min(deadline, retry_until) if retry_until is not None else deadline
        try:
            body = _attempt(path, attempt_deadline)
        except _RetryableRead as failure:
            last_error = failure.error
            if retry_until is None:
                retry_until = min(deadline, failure.failed_at + 1)
            remaining = retry_until - time.monotonic()
            if remaining <= 0:
                raise last_error
            time.sleep(min(.01, remaining))
            continue
        if time.monotonic() >= attempt_deadline:
            raise TimeoutError('ack_read_deadline')
        return body
