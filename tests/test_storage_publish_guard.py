"""Actual native handles and renames in isolated temporary directories only."""
import ctypes
import hashlib
import os
from pathlib import Path
import sys
import time
import struct
import json
import subprocess

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import storage_publish_guard as native
import windows_read_guard as read_native

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='native Windows file identity')


def guard(tmp_path, members):
    return native.RenameGuards(tmp_path, members, deadline=time.monotonic() + 20)


@pytest.mark.parametrize('entrypoint',['guard','move_fixture'])
def test_worker_bootstrap_keeps_native_partial_acquisition_until_process_exit(tmp_path,entrypoint):
    source=tmp_path/'present.db'
    source.write_bytes(b'bootstrap fixture')
    script=r'''
import sys,time,ctypes
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import storage_publish_guard as native
root=Path(sys.argv[2]);source=root/'present.db'
owned=None
try:
    if sys.argv[3]=='guard':
        owned=native.RenameGuards(root,{'first':source,'missing':root/'missing.db'},deadline=time.monotonic()+10)
        owned.acquire_retained()
    else:
        sys.path.insert(0,sys.argv[4])
        from storage_move_endpoint_child import run
        from types import SimpleNamespace
        paths={key:str(root/name) for key,name in (
            ('source_main','present.db'),('source_wal','missing.db'),('archive_shm','archive-shm'),
            ('archive_wal','archive-wal'),('archive_main','archive-main'),('installed_main','installed'))}
        run(SimpleNamespace(_shared_deadline=None),None,
            {'generation':{'members':{'source_main':{},'source_wal':{}},'paths':paths},
             'directories':None,'sentinels':None},str(root),'a'*32,1,1,'BootstrapFailure')
    raise AssertionError('missing acquisition admitted')
except OSError:pass
if owned is None:
    assert len(native._RETAINED_GUARDS)==1
    owned=native._RETAINED_GUARDS[0]
first_key='first' if sys.argv[3]=='guard' else 'source_main'
missing_key='missing' if sys.argv[3]=='guard' else 'source_wal'
assert owned.close_fault and owned in native._RETAINED_GUARDS
first=owned.members[first_key]['handle']
assert first is not None and owned.members[missing_key]['handle'] is None
assert owned._info(first)==owned.members[first_key]['identity']
assert owned.directories and owned.sentinels
assert all(m['handle'] is not None for g in (owned.directories,owned.sentinels) for m in g.values())
opened=owned.api.CreateFileW(str(source),0x40000000,5,None,3,0x80,None)
assert opened in (None,ctypes.c_void_p(-1).value) and ctypes.get_last_error()==32
calls=[]
actual_close=owned.api.CloseHandle
def counted(handle):calls.append(handle);return actual_close(handle)
owned.api.CloseHandle=counted
try:owned.acquire_retained();raise AssertionError('bootstrap retried')
except ValueError as error:assert str(error)=='publish_retained_acquisition_consumed'
try:owned.close();raise AssertionError('fault cleanup admitted')
except OSError as error:assert str(error)=='publish_native_close_unconfirmed'
assert not calls and owned.members[first_key]['handle']==first
assert owned._info(first)==owned.members[first_key]['identity']
print('NATIVE_PARTIAL_BOOTSTRAP_RETAINED')
'''
    result=subprocess.run([sys.executable,'-I','-c',script,str(Path(__file__).parents[1]/'scripts'),str(tmp_path),
                           entrypoint,str(Path(__file__).parent)],
                          capture_output=True,text=True,timeout=15,creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'NATIVE_PARTIAL_BOOTSTRAP_RETAINED' in result.stdout
    assert source.read_bytes()==b'bootstrap fixture'


def test_held_identity_digest_and_unicode_rename(tmp_path):
    source = tmp_path / 'original.db'
    payload = b'original fixture' * 100000
    source.write_bytes(payload)
    target = tmp_path / ('archive-' + chr(0x1F512) + '.db')
    with guard(tmp_path, {'main': source}) as owned:
        before = dict(owned.members['main']['identity'])
        assert owned.digest('main') == hashlib.sha256(payload).hexdigest()
        owned.move('main', target)
        assert owned.members['main']['identity'] == before
        assert owned.digest('main') == hashlib.sha256(payload).hexdigest()
        assert not source.exists()
    assert target.read_bytes() == payload
    assert not owned.auxiliary and not native._RETAINED_GUARDS


def test_writer_excluded_across_move(tmp_path):
    source = tmp_path / 'source.db'
    source.write_bytes(b'fixture')
    target = tmp_path / 'archive.db'
    with guard(tmp_path, {'main': source}) as owned:
        for path in (source, target):
            if path == target:
                owned.move('main', target)
            handle = owned.api.CreateFileW(str(path), 0x40000000, 5, None, 3, 0x80, None)
            if handle not in (None, ctypes.c_void_p(-1).value):
                owned.api.CloseHandle(handle)
                pytest.fail('writer unexpectedly admitted')
            assert ctypes.get_last_error() == 32
    assert target.read_bytes() == b'fixture'


def test_destination_never_overwritten(tmp_path):
    source, target = tmp_path / 'source.db', tmp_path / 'existing.db'
    source.write_bytes(b'original')
    target.write_bytes(b'existing')
    with guard(tmp_path, {'main': source}) as owned:
        with pytest.raises(ValueError, match='destination_exists'):
            owned.move('main', target)
    assert source.read_bytes() == b'original' and target.read_bytes() == b'existing'


def test_foreign_rename_or_replacement_blocks_move(tmp_path):
    source, foreign, destination = (tmp_path / name for name in ('source.db', 'foreign.db', 'archive.db'))
    source.write_bytes(b'original')
    with guard(tmp_path, {'main': source}) as owned:
        os.rename(source, foreign)
        source.write_bytes(b'replacement')
        with pytest.raises(ValueError, match='native_path_changed'):
            owned.move('main', destination)
        assert not destination.exists()
    assert foreign.read_bytes() == b'original' and source.read_bytes() == b'replacement'


def test_alias_and_hardlink_are_not_accepted(tmp_path):
    source, alias = tmp_path / 'source.db', tmp_path / 'alias.db'
    source.write_bytes(b'fixture')
    with pytest.raises(ValueError, match='alias_or_volume'):
        with guard(tmp_path, {'one': source, 'two': source}):
            pass
    os.link(source, alias)
    with pytest.raises(ValueError, match='exclusive_regular_file'):
        with guard(tmp_path, {'one': source}):
            pass
    assert not native._RETAINED_GUARDS


def test_parent_handoff_to_no_delete_guard_has_no_writer_gap(tmp_path):
    source, target = tmp_path / 'source.db', tmp_path / 'installed.db'
    source.write_bytes(b'fixture')
    owned = guard(tmp_path, {'main': source})
    with owned:
        # This duplicate stands for the parent guard, using the same native
        # identity. Read+Delete sharing admits the child's DELETE handle.
        parent = owned.api.CreateFileW(str(source), 0x80000000, 5, None, 3, 0x80, None)
        assert parent not in (None, ctypes.c_void_p(-1).value)
        try:
            owned.move('main', target)
            with pytest.raises(OSError):
                with read_native.ReadFiles([target], deadline=time.monotonic() + 5):
                    pass
            owned.close()
            # Native child DELETE handles are now closed; the parent still
            # excludes writers while taking the stronger no-delete guard.
            with read_native.ReadFiles([target], deadline=time.monotonic() + 5) as stronger:
                assert stronger.evidence[0]['volume'] == owned.members['main']['identity']['volume']
                assert stronger.evidence[0]['file_id'] == owned.members['main']['identity']['file_id']
                assert target.read_bytes() == b'fixture'
        finally:
            assert owned.api.CloseHandle(parent)


def test_outside_root_never_opened(tmp_path):
    outside = tmp_path.parent / (tmp_path.name + '-outside')
    outside.write_bytes(b'fixture')
    with pytest.raises(ValueError, match='outside_root'):
        with guard(tmp_path, {'main': outside}):
            pass
    assert outside.read_bytes() == b'fixture'


def test_destination_directory_is_pinned_against_rename(tmp_path):
    source = tmp_path / 'source.db'
    source.write_bytes(b'fixture')
    archive = tmp_path / 'archive'
    archive.mkdir()
    target = archive / 'source.db'
    with native.RenameGuards(tmp_path, {'main': source}, deadline=time.monotonic() + 20,
                             destinations=(target,)) as owned:
        with pytest.raises(OSError):
            os.rename(archive, tmp_path / 'other')
        owned.move('main', target)
    assert target.read_bytes() == b'fixture'


def test_unbound_destination_is_not_admitted(tmp_path):
    source = tmp_path / 'source.db'
    source.write_bytes(b'fixture')
    target, other = tmp_path / 'bound.db', tmp_path / 'unbound.db'
    with native.RenameGuards(tmp_path, {'main': source}, deadline=time.monotonic() + 20,
                             destinations=(target,)) as owned:
        with pytest.raises(ValueError, match='destination_not_bound'):
            owned.move('main', other)
    assert source.read_bytes() == b'fixture' and not other.exists()


def test_nonempty_sentinel_rejects_junction_conversion(tmp_path):
    source = tmp_path / 'source.db'
    source.write_bytes(b'fixture')
    archive, redirect = tmp_path / 'archive', tmp_path / 'redirect'
    archive.mkdir()
    redirect.mkdir()
    target = archive / 'source.db'
    with native.RenameGuards(tmp_path, {'main': source}, deadline=time.monotonic() + 20,
                             destinations=(target,)) as owned:
        sentinels = [p for p in owned.sentinels if p.parent == archive]
        assert len(sentinels) == 1 and sentinels[0].stat().st_size == 0
        handle = owned.api.CreateFileW(str(archive), 0x40000000, 7, None, 3, 0x2200000, None)
        assert handle not in (None, ctypes.c_void_p(-1).value)
        try:
            substitute = (chr(92) + '??' + chr(92) + str(redirect)).encode('utf-16-le')
            display = str(redirect).encode('utf-16-le')
            names = substitute + bytes(2) + display + bytes(2)
            payload = struct.pack('<IHHHHHH', 0xA0000003, 8 + len(names), 0,
                                  0, len(substitute), len(substitute) + 2, len(display)) + names
            api = owned.api.DeviceIoControl
            api.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32,
                            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p]
            api.restype = ctypes.c_int
            buffer, returned = ctypes.create_string_buffer(payload), ctypes.c_uint32()
            ok = api(handle, 0x900A4, buffer, len(payload), None, 0, ctypes.byref(returned), None)
            assert not ok
            # A restricted token may reject the operation earlier; malformed
            # reparse data is not accepted as evidence of namespace protection.
            assert ctypes.get_last_error() in (145, 5, 1314)
        finally:
            assert owned.api.CloseHandle(handle)
        owned.assert_bound()
        owned.move('main', target)
    assert target.read_bytes() == b'fixture'


@pytest.mark.parametrize('value', [True, float('inf'), float('nan'), time.monotonic() - 1])
def test_invalid_deadline(value, tmp_path):
    with pytest.raises(ValueError, match='deadline'):
        native.RenameGuards(tmp_path, {'main': tmp_path / 'none'}, deadline=value)


@pytest.mark.parametrize('returned,completed', [(0x103, 0x103), (0x103, -1),
                                              (0, -1), (-1073741811, -1073741811)])
def test_unconfirmed_native_result_retains_buffers_handles_and_blocks_retry(
        tmp_path, monkeypatch, returned, completed):
    source, target = tmp_path / 'source.db', tmp_path / 'archive.db'
    source.write_bytes(b'fixture')
    owned = guard(tmp_path, {'main': source}).__enter__()
    calls = []

    def isolated_submission(handle, completion, buffer, length, kind):
        # No kernel submission in this regression. Test the completion contract.
        block = ctypes.cast(completion, ctypes.POINTER(native.IOStatus)).contents
        assert block.value.status == -1
        assert kind == 10 and length >= 24 + len(target.name.encode('utf-16-le'))
        block.value.status = completed
        block.information = 0
        calls.append(handle)
        return returned

    monkeypatch.setattr(owned.nt, 'NtSetInformationFile', isolated_submission)
    try:
        with pytest.raises(OSError, match='rename_unconfirmed'):
            owned.move('main', target)
        assert owned.native_operation is not None and owned in native._RETAINED_GUARDS
        held = owned.members['main']['handle']
        with pytest.raises(OSError, match='operation_unconfirmed'):
            owned.__exit__(None, None, None)
        with pytest.raises(OSError, match='operation_unconfirmed'):
            owned.move('main', target)
        assert calls == [held] and owned.members['main']['handle'] == held
        assert owned._info(held) == owned.members['main']['identity']
        writer = owned.api.CreateFileW(str(source), 0x40000000, 5, None, 3, 0x80, None)
        assert writer in (None, ctypes.c_void_p(-1).value)
        assert ctypes.get_last_error() == 32
        assert source.exists() and not target.exists()
    finally:
        # Only the test double ran: there is no outstanding kernel operation.
        owned.native_operation = None
        owned.close()


def test_success_requires_both_zero_statuses_but_not_information_count(tmp_path, monkeypatch):
    source, target = tmp_path / 'source.db', tmp_path / 'archive.db'
    source.write_bytes(b'fixture')
    with guard(tmp_path, {'main': source}) as owned:
        actual = owned.nt.NtSetInformationFile

        def submit(*args):
            returned = actual(*args)
            ctypes.cast(args[1], ctypes.POINTER(native.IOStatus)).contents.information = 7
            return returned

        monkeypatch.setattr(owned.nt, 'NtSetInformationFile', submit)
        owned.move('main', target)
        assert owned.native_operation is None and owned not in native._RETAINED_GUARDS
    assert target.read_bytes() == b'fixture'


@pytest.mark.parametrize('after_rename', [False, True])
def test_submission_exception_keeps_operation_and_guards(tmp_path, monkeypatch, after_rename):
    source, target = tmp_path / 'source.db', tmp_path / 'archive.db'
    source.write_bytes(b'fixture')
    owned = guard(tmp_path, {'main': source}).__enter__()
    actual = owned.nt.NtSetInformationFile

    def submit(*args):
        if after_rename:
            assert actual(*args) == 0
        raise RuntimeError('isolated_submission_exception')

    monkeypatch.setattr(owned.nt, 'NtSetInformationFile', submit)
    try:
        with pytest.raises(RuntimeError, match='isolated_submission'):
            owned.move('main', target)
        with pytest.raises(OSError, match='operation_unconfirmed'):
            owned.close()
        assert owned in native._RETAINED_GUARDS and owned.native_operation is not None
        assert target.exists() == after_rename
        assert source.exists() != after_rename
    finally:
        # The double either never submitted or synchronously completed; no I/O
        # is outstanding. This test-only release is not a recovery interface.
        owned.native_operation = None
        owned.close()


def test_postcheck_failure_keeps_parent_guard_after_child_context_closes(tmp_path, monkeypatch):
    source, target = tmp_path / 'source.db', tmp_path / 'archive.db'
    source.write_bytes(b'fixture')
    owned = guard(tmp_path, {'main': source})
    owned.__enter__()
    parent = owned.api.CreateFileW(str(source), 0x80000000, 5, None, 3, 0x80, None)
    assert parent not in (None, ctypes.c_void_p(-1).value)
    actual = owned.assert_bound
    calls = []

    def check():
        calls.append(True)
        if len(calls) == 2:
            raise ValueError('isolated_postcheck_failure')
        actual()

    monkeypatch.setattr(owned, 'assert_bound', check)
    try:
        with pytest.raises(ValueError, match='postcheck_failure'):
            try:
                owned.move('main', target)
            finally:
                owned.__exit__(None, None, None)
        assert target.exists() and not source.exists()
        writer = owned.api.CreateFileW(str(target), 0x40000000, 5, None, 3, 0x80, None)
        assert writer in (None, ctypes.c_void_p(-1).value)
        assert ctypes.get_last_error() == 32
    finally:
        owned.close()
        assert owned.api.CloseHandle(parent)


def test_parent_guard_survives_actual_worker_termination(tmp_path):
    source, target = tmp_path / 'source.db', tmp_path / 'installed.db'
    source.write_bytes(b'fixture')
    controller = guard(tmp_path, {'main': source})
    parent = controller.api.CreateFileW(str(source), 0x80000000, 5, None, 3, 0x80, None)
    assert parent not in (None, ctypes.c_void_p(-1).value)
    before = controller._info(parent)
    ready = tmp_path / 'worker-ready.json'
    program = '''
import sys, os, time, json
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from storage_publish_guard import RenameGuards
root, source, target, ready = map(Path, sys.argv[2:])
with RenameGuards(root, {'main':source}, deadline=time.monotonic()+20, destinations=(target,)) as guard:
    guard.move('main', target)
    ready.write_text(json.dumps({'pid':os.getpid(), 'identity':guard.members['main']['identity']}))
    time.sleep(20)
'''
    # Launch the base interpreter directly: no venv launcher/base-PID ambiguity.
    process = subprocess.Popen([sys._base_executable, '-I', '-c', program,
                                str(Path(__file__).parents[1] / 'scripts'),
                                str(tmp_path), str(source), str(target), str(ready)],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.01)
        assert ready.exists(), 'worker failed before native rename'
        value = json.loads(ready.read_text())
        assert value == {'pid':process.pid, 'identity':before}
        process.kill()
        process.wait(timeout=5)
        assert controller._info(parent) == before
        writer = controller.api.CreateFileW(str(target), 0x40000000, 5, None, 3, 0x80, None)
        assert writer in (None, ctypes.c_void_p(-1).value)
        assert ctypes.get_last_error() == 32
        with read_native.ReadFiles([target], deadline=time.monotonic()+5) as stronger:
            assert stronger.evidence[0]['file_id'] == before['file_id']
            assert stronger.evidence[0]['volume'] == before['volume']
            assert target.read_bytes() == b'fixture'
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        assert controller.api.CloseHandle(parent)
