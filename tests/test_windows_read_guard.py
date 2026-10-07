"""Real native file sharing on tiny fixtures; no IMC processes or data."""
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('windows_read_guard', ROOT / 'scripts/windows_read_guard.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows file sharing')
CLI = ROOT / '.local-tools/sqlite-tools-3530400/sqlite3.exe'


def wal_fixture(tmp_path):
    path = tmp_path / 'source.db'
    code = ("import os,sqlite3,sys; c=sqlite3.connect(sys.argv[1]);c.execute('PRAGMA journal_mode=WAL');"
            "c.execute('CREATE TABLE evidence(value INTEGER)');c.execute('INSERT INTO evidence VALUES(42)');"
            "c.commit();os._exit(0)")
    result = subprocess.run([sys.executable, '-c', code, str(path)], timeout=5)
    assert result.returncode == 0
    wal = Path(str(path)+'-wal')
    assert wal.stat().st_size > 0
    init = tmp_path / 'empty-init.sql'
    init.write_bytes(b'')
    return path, wal, init


def test_guards_allow_actual_wal_read_then_block_writer_until_release(tmp_path):
    if not CLI.exists(): pytest.skip('Exact hash-verified SQLite audit binary unavailable')
    source, wal, init = wal_fixture(tmp_path)
    before = [p.read_bytes() for p in (source, wal)]
    with guard.ReadFiles([source, wal]) as held:
        result = guard.readonly_cli(CLI, source, 'SELECT value FROM evidence', init, guards=held, seconds=5)
        assert result['returncode'] == 0 and result['child_exit_confirmed'] and not result['timed_out']
        assert result['results'][-1] == [{'value': 42}]
        assert len(held.evidence) == 2
        # The native reader has ended. Its SQL locks cannot explain this denial.
        with pytest.raises(sqlite3.OperationalError):
            writer = sqlite3.connect(source, timeout=.1)
            try:
                writer.execute('INSERT INTO evidence VALUES(43)'); writer.commit()
            finally:
                writer.close()
        assert [p.read_bytes() for p in (source, wal)] == before
    with sqlite3.connect(source) as writer:
        writer.execute('INSERT INTO evidence VALUES(43)')
    with sqlite3.connect(source) as reader:
        assert reader.execute('SELECT value FROM evidence ORDER BY value').fetchall() == [(42,), (43,)]


def test_partial_guard_open_failure_releases_earlier_native_handle(tmp_path):
    source = tmp_path / 'source.db'
    source.write_bytes(b'fixture')
    # Existing foreign writer makes the second read-only sharing open fail.
    foreign = tmp_path / 'foreign.db'
    foreign.write_bytes(b'fixture')
    api = guard.ReadFiles([source]).api
    writable = api.CreateFileW(str(foreign), 0x40000000, 3, None, 3, 0x80, None)
    assert writable not in (None, guard.ctypes.c_void_p(-1).value)
    try:
        with pytest.raises(OSError):
            with guard.ReadFiles([source, foreign]): pass
        # A leaked first handle would forbid this new write open.
        source.write_bytes(b'changed')
    finally:
        assert api.CloseHandle(writable)


def test_timed_out_native_reader_is_confirmed_dead_before_guard_release(tmp_path):
    if not CLI.exists(): pytest.skip('Exact hash-verified SQLite audit binary unavailable')
    source, wal, init = wal_fixture(tmp_path)
    with guard.ReadFiles([source, wal]) as held:
        metadata = guard.readonly_cli(CLI, source, 'SELECT value FROM evidence', init, guards=held, seconds=5)
        assert metadata['results'][-1] == [{'value': 42}]
        result = guard.readonly_cli(CLI, source,
            'WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n) SELECT sum(x) FROM n',
            init, guards=held, seconds=2)
        assert result['timed_out'] and result['child_exit_confirmed']
        assert result['elapsed_seconds'] < 3
        assert metadata['child_exit_confirmed']  # completed metadata is retained independently
    with sqlite3.connect(source) as writer:
        writer.execute('INSERT INTO evidence VALUES(43)')


@pytest.mark.parametrize('seconds',[True,False,float('nan'),float('inf'),1,901,'900'])
def test_invalid_cli_budget_rejected_before_file_or_child_access(tmp_path,monkeypatch,seconds):
    def forbidden(*args,**kwargs):raise AssertionError('invalid budget created a child')
    monkeypatch.setattr(guard.subprocess,'Popen',forbidden)
    with pytest.raises(ValueError,match='invalid_readonly_cli_boundary'):
        guard.readonly_cli(tmp_path/'missing.exe',tmp_path/'missing.db','SELECT 1',tmp_path/'missing.sql',guards=None,seconds=seconds)


def test_900_second_child_budget_obeys_short_shared_deadline(tmp_path):
    if not CLI.exists():pytest.skip('Exact hash-verified SQLite audit binary unavailable')
    source,wal,init=wal_fixture(tmp_path)
    import time
    deadline=time.monotonic()+3
    with guard.ReadFiles([source,wal],deadline=deadline) as held:
        result=guard.readonly_cli(CLI,source,
            'WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n) SELECT sum(x) FROM n',
            init,guards=held,seconds=900)
        assert result['shared_deadline']==deadline
        assert result['timed_out'] and result['child_exit_confirmed']
        assert result['elapsed_seconds']<3.5
        assert all(guard.child_exited(child) for child in held.children)
    assert not guard._RETAINED_GUARDS
    with sqlite3.connect(source) as writer:writer.execute('INSERT INTO evidence VALUES(43)')


def test_guard_refuses_release_until_attached_native_child_is_confirmed_dead(tmp_path):
    if not CLI.exists(): pytest.skip('Exact hash-verified SQLite audit binary unavailable')
    source, wal, init = wal_fixture(tmp_path)
    held = guard.ReadFiles([source, wal]).__enter__()
    child = subprocess.Popen([str(CLI), '-readonly', '-batch', '-json', '-bail', '-init', str(init), str(source),
        'WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n) SELECT sum(x) FROM n'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
    held.children.append(child)
    try:
        with pytest.raises(RuntimeError, match='child_shutdown_unconfirmed'): held.close()
        assert held.handles and held in guard._RETAINED_GUARDS
        with pytest.raises(PermissionError):
            with source.open('r+b'): pass
    finally:
        child.kill(); child.wait(timeout=3)
        assert guard.child_exited(child)
        held.close()
    assert not held.handles and held not in guard._RETAINED_GUARDS
    with source.open('r+b'): pass


def test_present_wal_requires_its_own_active_guard(tmp_path):
    if not CLI.exists(): pytest.skip('Exact hash-verified SQLite audit binary unavailable')
    source, wal, init = wal_fixture(tmp_path)
    with guard.ReadFiles([source]) as held:
        with pytest.raises(ValueError, match='invalid_readonly_cli_boundary'):
            guard.readonly_cli(CLI, source, 'SELECT value FROM evidence', init, guards=held, seconds=5)
        assert not held.children


def test_init_script_is_locked_before_native_creation(tmp_path, monkeypatch):
    if not CLI.exists(): pytest.skip('Exact hash-verified SQLite audit binary unavailable')
    source, wal, init = wal_fixture(tmp_path)
    original = subprocess.Popen
    attempted = []
    def create(*args, **kwargs):
        with pytest.raises(PermissionError): init.write_bytes(b'.shell forbidden')
        attempted.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(subprocess, 'Popen', create)
    with guard.ReadFiles([source, wal]) as held:
        result = guard.readonly_cli(CLI, source, 'SELECT value FROM evidence', init, guards=held, seconds=5)
        assert result['complete'] and result['results'][-1] == [{'value': 42}]
    assert attempted == [True] and init.read_bytes() == b''


def test_close_failure_remains_sticky_and_never_blindly_retries_unknown_handle(tmp_path):
    source = tmp_path / 'source.db'
    source.write_bytes(b'fixture')
    held = guard.ReadFiles([source]).__enter__()
    handle = held.handles[0]
    close = held.api.CloseHandle
    calls = []
    held.api.CloseHandle = lambda value: calls.append(value) or 0
    try:
        with pytest.raises(OSError, match='close_unconfirmed'): held.close()
        assert held.handles == [handle] and held in guard._RETAINED_GUARDS
        with pytest.raises(OSError, match='close_unconfirmed'): held.close()
        assert calls == [handle]
        with pytest.raises(PermissionError):
            with source.open('r+b'): pass
    finally:
        # Restore/close only this known fixture handle; production never retries.
        held.api.CloseHandle = close
        assert close(handle)
        held.handles.clear(); guard._RETAINED_GUARDS.remove(held)


def test_exit_probe_exception_retains_native_guards(tmp_path, monkeypatch):
    source = tmp_path / 'source.db'
    source.write_bytes(b'fixture')
    held = guard.ReadFiles([source]).__enter__()
    held.children.append(object())
    def failed(child): raise OSError('probe failed')
    monkeypatch.setattr(guard, 'child_exited', failed)
    try:
        with pytest.raises(RuntimeError, match='child_shutdown_unconfirmed'): held.close()
        assert held.handles and held in guard._RETAINED_GUARDS
    finally:
        held.children.clear(); held.close()


def test_stdout_is_bounded_and_multiple_queries_keep_original_guard_deadline(tmp_path):
    if not CLI.exists(): pytest.skip('Exact hash-verified SQLite audit binary unavailable')
    source, wal, init = wal_fixture(tmp_path)
    with guard.ReadFiles([source, wal]) as held:
        deadline = held.deadline
        first = guard.readonly_cli(CLI, source, 'SELECT value FROM evidence', init, guards=held, seconds=5)
        second = guard.readonly_cli(CLI, source,
            'WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n) SELECT x FROM n',
            init, guards=held, seconds=5)
        assert first['complete'] and first['shared_deadline'] == second['shared_deadline'] == deadline
        assert second['output_limit_exceeded'] and not second['complete']
        assert second['captured_bytes'] <= 1048576 and second['child_exit_confirmed']


def test_ended_child_with_unjoined_reader_retains_both_guards(tmp_path, monkeypatch):
    import threading
    import time
    if not CLI.exists(): pytest.skip('Exact hash-verified SQLite audit binary unavailable')
    source, wal, init = wal_fixture(tmp_path)
    original = threading.Thread
    release = threading.Event()
    created = []
    def factory(*args, **kwargs):
        target, arguments = kwargs['target'], kwargs.get('args', ())
        first = not created
        def run():
            if first: release.wait(4)
            target(*arguments)
        thread = original(target=run, daemon=True)
        created.append(thread)
        return thread
    monkeypatch.setattr(guard.threading, 'Thread', factory)
    held = guard.ReadFiles([source, wal]).__enter__()
    try:
        with pytest.raises(RuntimeError, match='stream_shutdown_unconfirmed'):
            guard.readonly_cli(CLI, source, 'SELECT value FROM evidence', init, guards=held, seconds=2)
        assert all(guard.child_exited(child) for child in held.children)
        assert held.handles and any(t.is_alive() for t in created)
        with pytest.raises(RuntimeError, match='stream_shutdown_unconfirmed'): held.close()
        assert len(guard._RETAINED_GUARDS) == 2
    finally:
        release.set()
        for thread in created: thread.join(2)
        for retained in list(guard._RETAINED_GUARDS): retained.close()
    assert not held.handles and not guard._RETAINED_GUARDS


def test_second_thread_start_failure_stops_owned_child_and_joins_first_reader(tmp_path, monkeypatch):
    import threading
    if not CLI.exists(): pytest.skip('Exact hash-verified SQLite audit binary unavailable')
    source, wal, init = wal_fixture(tmp_path)
    original = threading.Thread.start
    attempted = []
    def start(thread):
        attempted.append(thread)
        if len(attempted) == 2: raise RuntimeError('fixture_thread_start_failed')
        return original(thread)
    monkeypatch.setattr(guard.threading.Thread, 'start', start)
    held = guard.ReadFiles([source, wal]).__enter__()
    try:
        with pytest.raises(RuntimeError, match='fixture_thread_start_failed'):
            guard.readonly_cli(CLI, source, 'SELECT value FROM evidence', init, guards=held, seconds=5)
        assert len(held.children) == 1 and guard.child_exited(held.children[0])
        assert not any(thread.is_alive() for thread in held.workers)
    finally:
        held.close()
    assert not held.handles and not guard._RETAINED_GUARDS


def test_thread_constructor_failure_never_creates_native_child(tmp_path, monkeypatch):
    if not CLI.exists(): pytest.skip('Exact hash-verified SQLite audit binary unavailable')
    source, wal, init = wal_fixture(tmp_path)
    original_thread, original_popen = guard.threading.Thread, subprocess.Popen
    constructed, spawned = [], []
    def thread(*args, **kwargs):
        if constructed: raise RuntimeError('fixture_thread_constructor_failed')
        worker = original_thread(*args, **kwargs)
        constructed.append(worker)
        return worker
    def popen(*args, **kwargs):
        spawned.append(True)
        return original_popen(*args, **kwargs)
    monkeypatch.setattr(guard.threading, 'Thread', thread)
    monkeypatch.setattr(subprocess, 'Popen', popen)
    with guard.ReadFiles([source, wal]) as held:
        with pytest.raises(RuntimeError, match='fixture_thread_constructor_failed'):
            guard.readonly_cli(CLI, source, 'SELECT value FROM evidence', init, guards=held, seconds=5)
        assert spawned == [] and held.children == []
        assert len(constructed) == 1 and constructed[0].ident is None
        assert not held.workers and not guard._RETAINED_GUARDS
        # The auxiliary init/binary guards are already released on this path.
        init.write_bytes(b'')
    assert not held.handles and not guard._RETAINED_GUARDS
    with sqlite3.connect(source) as writer:
        writer.execute('INSERT INTO evidence VALUES(43)')
