"""Deterministic initialization races on tiny isolated databases; no execution I/O."""
import errno
import os
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'RUNTIME/EXECUTOR_GATEWAY/GMGN_BRIDGE'))
from execution_lock import initialization_lock
from execution_state import Ledger, StateError, load_config


def test_second_initializer_waits_for_empty_file_owner_commit(tmp_path, monkeypatch):
    path = tmp_path / 'race.db'
    opened, release, second_started = (threading.Event() for _ in range(3))
    original_transaction = Ledger.transaction

    @contextmanager
    def pause_after_file_creation(store):
        if not path.exists():
            sqlite3.connect(path).close()
            opened.set()
            assert release.wait(3), 'test owner was not released'
        with original_transaction(store) as connection:
            yield connection

    def second():
        second_started.set()
        return Ledger(path)

    monkeypatch.setattr(Ledger, 'transaction', pause_after_file_creation)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(Ledger, path)
        try:
            assert opened.wait(3)
            other = pool.submit(second)
            assert second_started.wait(3)
            with pytest.raises(FutureTimeout):
                other.result(timeout=0.1)
        finally:
            release.set()
        stores = [first.result(timeout=3), other.result(timeout=3)]
    with stores[0].transaction() as c:
        session = stores[0].meta(c, 'session')
    with stores[1].transaction() as c:
        assert stores[1].meta(c, 'session') == session


def test_initialization_lock_timeout_leaves_database_untouched(tmp_path):
    path = tmp_path / 'locked.db'
    cfg = load_config()
    cfg['ledger_lock_timeout_seconds'] = 0.05
    with initialization_lock(path, 1):
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(Ledger, path, cfg)
            with pytest.raises(TimeoutError, match='ledger_initialization_timeout'):
                pending.result(timeout=2)
        assert not path.exists()
    assert Ledger(path).path == path


def test_aborted_schema_initialization_releases_lock_and_never_resets(tmp_path, monkeypatch):
    path = tmp_path / 'aborted.db'
    set_meta = Ledger.set_meta

    def fail(c, key, value):
        if key == 'session':
            raise RuntimeError('fixture crash before schema commit')
        set_meta(c, key, value)

    with monkeypatch.context() as patch:
        patch.setattr(Ledger, 'set_meta', staticmethod(fail))
        with pytest.raises(RuntimeError, match='fixture crash'):
            Ledger(path)
    original = path.read_bytes()
    with pytest.raises(StateError, match='incomplete_ledger_schema'):
        Ledger(path)
    assert path.read_bytes() == original
    with initialization_lock(path, 0.1) as existed:
        assert existed


@pytest.mark.parametrize('crash', [False, True])
def test_other_process_lock_survives_sidecar_and_releases_on_exit(tmp_path, crash):
    path, ready = tmp_path / 'process.db', tmp_path / 'ready'
    script = """
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from execution_lock import initialization_lock
with initialization_lock(Path(sys.argv[2]), 2):
    Path(sys.argv[3]).write_text('locked')
    sys.stdin.readline()
    if sys.argv[4] == 'crash': os._exit(23)
"""
    proc = subprocess.Popen(
        [sys.executable, '-c', script, str(ROOT / 'RUNTIME/EXECUTOR_GATEWAY/GMGN_BRIDGE'),
         str(path), str(ready), 'crash' if crash else 'normal'],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 3
        while not ready.exists() and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists(), 'isolated test child did not acquire its lock'
        with pytest.raises(TimeoutError, match='ledger_initialization_timeout'):
            with initialization_lock(path, 0.05):
                pytest.fail('two processes own the initialization lock')
        proc.communicate(input='release\n', timeout=3)
        assert proc.returncode == (23 if crash else 0)
        lock_path = path.with_name(path.name + '.init.lock')
        assert lock_path.exists()
        with initialization_lock(path, 0.1) as existed:
            assert not existed
        # A remaining sidecar is not a stale active lock or permission to reset a DB.
        Ledger(path)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=3)


def test_unexpected_os_lock_error_is_not_retried(tmp_path, monkeypatch):
    if os.name == 'nt':
        import msvcrt
        target, name = msvcrt, 'locking'
    else:
        import fcntl
        target, name = fcntl, 'flock'

    def fail(*args):
        raise OSError(errno.EBADF, 'fixture bad descriptor')

    monkeypatch.setattr(target, name, fail)
    path = tmp_path / 'error.db'
    with pytest.raises(OSError) as caught:
        Ledger(path)
    assert caught.value.errno == errno.EBADF
    assert not path.exists()


@pytest.mark.parametrize('value', [True, 0, -1, float('nan'), float('inf'), 31])
def test_invalid_ledger_lock_timeout_fails_before_file_creation(tmp_path, value):
    cfg = load_config()
    cfg['ledger_lock_timeout_seconds'] = value
    path = tmp_path / 'bad-config.db'
    with pytest.raises(StateError):
        Ledger(path, cfg)
    assert not path.exists()
    assert not path.with_name(path.name + '.init.lock').exists()
