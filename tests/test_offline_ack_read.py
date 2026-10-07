"""Native isolated ACK sharing; does not establish the old flake's cause."""
import ctypes
import json
import os
from pathlib import Path
import sys
import threading
import time

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import offline_ack_read as reader
import offline_worker_diagnostics as diagnostics
import storage_copy_worker as worker


def lock(path):
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                               ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    api.CreateFileW.restype = ctypes.c_void_p
    api.CloseHandle.argtypes = [ctypes.c_void_p]
    api.CloseHandle.restype = ctypes.c_int
    handle = api.CreateFileW(str(path), 0x80000000, 0, None, 3, 0x80, None)
    assert handle not in (None, ctypes.c_void_p(-1).value)
    return api, handle


@pytest.mark.skipif(os.name != 'nt', reason='actual native sharing violation')
def test_actual_short_sharing_lock_reproduces_crt_failure_and_wired_ack_recovers(tmp_path):
    path = tmp_path / 'ack'
    body = json.dumps({'nonce': 'a' * 32, 'pid': 7}).encode()
    path.write_bytes(body)
    api, handle = lock(path)
    state = {'handle': handle}
    try:
        with pytest.raises(PermissionError) as failure:
            with path.open('rb') as stream:stream.read(1025)
        assert failure.value.errno == 13 and failure.value.winerror is None
        data = diagnostics.evidence(failure.value, 'WAIT_ACK')
        assert data['win32_error'] is None and data['failure_location'] is None
        with pytest.raises(reader._RetryableRead) as native_failure:
            reader._attempt(path, time.monotonic() + 2)
        assert native_failure.value.error.winerror == 32
        closed = []
        def release():
            current = state['handle']; state['handle'] = None
            closed.append(bool(api.CloseHandle(current)))
        timer = threading.Timer(.05, release)
        timer.start()
        try:
            worker.ack(path, 'a' * 32, 7, time.monotonic() + 2)
        finally:
            timer.join(timeout=1)
        assert closed == [True]
    finally:
        if state['handle'] is not None:
            current = state['handle']; state['handle'] = None
            assert api.CloseHandle(current)


@pytest.mark.skipif(os.name != 'nt', reason='actual native sharing violation')
def test_persistent_lock_cannot_extend_shared_budget(tmp_path):
    path = tmp_path / 'ack'; path.write_bytes(b'{}')
    api, handle = lock(path)
    try:
        began = time.monotonic()
        with pytest.raises((PermissionError, TimeoutError)):
            reader.read_ack_bytes(path, deadline=began + .12)
        assert time.monotonic() - began < .5
    finally:
        assert api.CloseHandle(handle)


def test_first_sharing_error_sets_one_nonextending_retry_limit(tmp_path, monkeypatch):
    path = tmp_path / 'ack'
    clock = [0.0]
    calls = []
    error = PermissionError('secret must never be printed'); error.winerror = 32
    def fail(*args, **kwargs):
        calls.append(clock[0]); raise reader._RetryableRead(error, clock[0])
    monkeypatch.setattr(reader, '_attempt', fail)
    monkeypatch.setattr(reader.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(reader.time, 'sleep', lambda delay: clock.__setitem__(0, clock[0] + delay))
    with pytest.raises(PermissionError): reader.read_ack_bytes(path, deadline=100)
    assert clock[0] <= 1.000001 and max(calls) < 1


@pytest.mark.parametrize('code', [None, 13, 33, 5.0, True])
def test_unapproved_permission_error_is_not_retried(tmp_path, monkeypatch, code):
    calls = []
    error = PermissionError('private data'); error.winerror = code
    def fail(*args, **kwargs): calls.append(1); raise error
    monkeypatch.setattr(reader.ParentReadGuards, '_open', fail)
    with pytest.raises(PermissionError): reader.read_ack_bytes(tmp_path/'ack', deadline=time.monotonic()+2)
    assert len(calls) == 1


def test_missing_file_is_not_retried(tmp_path):
    with pytest.raises(FileNotFoundError):
        reader.read_ack_bytes(tmp_path/'missing', deadline=time.monotonic()+2)


@pytest.mark.parametrize('deadline', [True, float('nan'), float('inf'), -1])
def test_invalid_or_consumed_budget_does_not_open(tmp_path, deadline):
    with pytest.raises((ValueError, TimeoutError)):
        reader.read_ack_bytes(tmp_path/'missing', deadline=deadline)


def test_size_check_rejects_after_one_read(tmp_path):
    path = tmp_path/'ack'; path.write_bytes(b'x'*1025)
    with pytest.raises(ValueError, match='size'):
        reader.read_ack_bytes(path, deadline=time.monotonic()+2)


@pytest.mark.parametrize('error', [ValueError('KEY_SECRET'), OSError('KEY_SECRET'),
                                 Exception('KEY_SECRET')])
def test_diagnostics_never_include_message_path_or_arbitrary_stage(error):
    result = diagnostics.evidence(error, 'KEY_SECRET')
    assert result['failure_stage'] == 'UNKNOWN'
    assert result['failure_location'] is None
    assert result['win32_error'] is None
    assert 'KEY_SECRET' not in json.dumps(result)


def test_custom_error_class_and_unapproved_native_code_are_unknown():
    class CredentialNamedException(PermissionError): pass
    error = CredentialNamedException('KEY_SECRET'); error.winerror = 32
    assert diagnostics.evidence(error, 'WAIT_ACK')['exception_kind'] == 'UNKNOWN'
    assert diagnostics.evidence(error, 'WAIT_ACK')['win32_error'] is None
    ordinary = PermissionError('KEY_SECRET'); ordinary.winerror = 33
    assert diagnostics.evidence(ordinary, 'WAIT_ACK')['win32_error'] is None


def test_unknown_error_cannot_execute_diagnostic_attribute_hooks():
    class ForeignException(Exception):
        def __getattribute__(self, name):
            if name in ('winerror', '__traceback__'):
                raise RuntimeError('KEY_SECRET')
            return super().__getattribute__(name)
    result = diagnostics.evidence(ForeignException('KEY_SECRET'), 'VALIDATE')
    assert result == {'failure_stage': 'VALIDATE', 'exception_kind': 'UNKNOWN',
                      'failure_location': None, 'win32_error': None}


def test_successful_read_cannot_outlive_first_error_retry_limit(tmp_path, monkeypatch):
    clock = [0.0]; calls = []
    error = PermissionError('private'); error.winerror = 32
    def attempt(path, deadline):
        calls.append(deadline)
        if len(calls) == 1: raise reader._RetryableRead(error, clock[0])
        clock[0] = 1.01
        return b'{}'
    monkeypatch.setattr(reader, '_attempt', attempt)
    monkeypatch.setattr(reader.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(reader.time, 'sleep', lambda delay: clock.__setitem__(0, clock[0]+delay))
    with pytest.raises(TimeoutError): reader.read_ack_bytes(tmp_path/'ack', deadline=100)
    assert calls == [100, 1.0]


@pytest.mark.parametrize('method', ['_info', '_native_path'])
def test_metadata_permission_error_is_final_even_when_native_code_is_32(tmp_path, monkeypatch, method):
    path = tmp_path/'ack'; path.write_bytes(b'{}')
    calls = []
    error = PermissionError('private'); error.winerror = 32
    def fail(*args): calls.append(1); raise error
    monkeypatch.setattr(reader.ParentReadGuards, method, fail)
    with pytest.raises(PermissionError): reader.read_ack_bytes(path, deadline=time.monotonic()+2)
    assert len(calls) == 1


@pytest.mark.parametrize('slow_close', [False, True])
def test_readfile_sharing_error_closes_before_retry_and_cleanup_consumes_original_limit(
        tmp_path, monkeypatch, slow_close):
    path = tmp_path/'ack'; path.write_bytes(b'{}')
    actual = reader.ParentReadGuards
    attempts, closed, reads = [], [], []
    clock = [0.0]
    if slow_close:
        monkeypatch.setattr(reader.time, 'monotonic', lambda: clock[0])
    def factory(*args, **kwargs):
        owner = actual(*args, **kwargs)
        attempts.append(owner)
        real_read, real_close = owner.api.ReadFile, owner._close
        def read(*arguments):
            reads.append(1)
            if len(reads) == 1:
                ctypes.set_last_error(32); return 0
            return real_read(*arguments)
        def close(handle):
            real_close(handle)
            closed.append(handle)
            if slow_close: clock[0] = 1.2
        owner.api.ReadFile = read
        owner._close = close
        return owner
    monkeypatch.setattr(reader, 'ParentReadGuards', factory)
    if slow_close:
        with pytest.raises(PermissionError) as failure:
            reader.read_ack_bytes(path, deadline=3)
        assert failure.value.winerror == 32
        assert len(attempts) == len(closed) == 1
    else:
        assert reader.read_ack_bytes(path, deadline=time.monotonic()+2) == b'{}'
        assert len(attempts) == len(closed) == len(reads) == 2
    assert all(owner.members['ack']['handle'] is None for owner in attempts)


@pytest.mark.parametrize('read_failure', [False, True])
@pytest.mark.parametrize('close_failure', ['false', 'exception'])
def test_close_uncertainty_overrides_read_success_or_retry_and_retains_raw_handle(
        tmp_path, monkeypatch, read_failure, close_failure):
    path = tmp_path/'ack'; path.write_bytes(b'{}')
    actual = reader.ParentReadGuards
    owners, closes = [], []
    def factory(*args, **kwargs):
        owner = actual(*args, **kwargs)
        close = owner.api.CloseHandle
        def fail_close(handle):
            closes.append(handle)
            if close_failure == 'exception': raise RuntimeError('private')
            return 0  # Controlled test double NEVER calls native CloseHandle.
        owner.api.CloseHandle = fail_close
        if read_failure:
            def fail_read(*args): ctypes.set_last_error(32); return 0
            owner.api.ReadFile = fail_read
        owners.append((owner, close))
        return owner
    monkeypatch.setattr(reader, 'ParentReadGuards', factory)
    try:
        with pytest.raises(RuntimeError, match='close_unconfirmed'):
            reader.read_ack_bytes(path, deadline=time.monotonic()+2)
        assert len(owners) == 1 and len(closes) == 1
        owner = owners[0][0]
        assert owner in reader._RETAINED_GUARDS
        handle = owner.members['ack']['handle']
        assert handle is not None and owner._info(handle)['bytes'] == 2
    finally:
        # Fault injector did not submit a native close. Release only the known
        # exact fixture handle with the saved real API, never retry uncertainty.
        for owner, real_close in owners:
            handle = owner.members['ack']['handle']
            if handle is not None:
                assert real_close(handle)
                owner.members['ack']['handle'] = None
            if owner in reader._RETAINED_GUARDS: reader._RETAINED_GUARDS.remove(owner)
