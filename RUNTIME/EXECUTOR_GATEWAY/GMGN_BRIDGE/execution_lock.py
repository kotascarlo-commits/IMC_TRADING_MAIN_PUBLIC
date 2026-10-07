"""Serialize ledger creation without interpreting a transient empty DB as damage."""
import errno
import os
import time
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def initialization_lock(path, timeout):
    path = Path(path).resolve()
    # Keep this inode: unlinking a lock file can create independent lock owners.
    lock_path = path.with_name(path.name + '.init.lock')
    with lock_path.open('a+b', buffering=0) as handle:
        if os.name == 'nt':
            import msvcrt

            def acquire():
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

            def release():
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            def acquire():
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

            def release():
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

        deadline = time.monotonic() + timeout
        while True:
            try:
                acquire()
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('ledger_initialization_timeout') from exc
                time.sleep(min(0.01, remaining))
        try:
            # The existence observation and the caller's full commit share this lock.
            yield path.exists()
        finally:
            release()
