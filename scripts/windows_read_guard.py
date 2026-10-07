"""Owned Windows read handles for an offline SQLite audit, never a writer.

Main/WAL sharing excludes ordinary new write/delete opens. Existing mappings
and SHM bookkeeping are outside this guarantee. This is not an ACL change.
"""
import ctypes
import json
import math
import os
import subprocess
import threading
import time
from pathlib import Path

_RETAINED_GUARDS = []


def child_exited(process):
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    api.WaitForSingleObject.restype = ctypes.c_uint32
    return process.poll() is not None and api.WaitForSingleObject(int(process._handle), 0) == 0


class ReadFiles:
    def __init__(self, paths, *, deadline=None):
        if os.name != 'nt':
            raise OSError('windows_read_guard_required')
        self.deadline = time.monotonic()+180 if deadline is None else deadline
        if type(self.deadline) not in (int, float) or not math.isfinite(self.deadline):
            raise ValueError('invalid_read_guard_deadline')
        self.paths = tuple(Path(p).resolve(strict=True) for p in paths)
        self.handles = []
        self.evidence = []
        self.children = []
        self.workers = []
        self.close_fault = False
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        self.api.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
        self.api.CreateFileW.restype = ctypes.c_void_p
        self.api.CloseHandle.argtypes = [ctypes.c_void_p]
        self.api.CloseHandle.restype = ctypes.c_int
        self.api.GetFileInformationByHandleEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        self.api.GetFileInformationByHandleEx.restype = ctypes.c_int
        self.api.GetFinalPathNameByHandleW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
        self.api.GetFinalPathNameByHandleW.restype = ctypes.c_uint32

    def __enter__(self):
        class FileID(ctypes.Structure):
            _fields_ = [('volume', ctypes.c_uint64), ('identifier', ctypes.c_ubyte * 16)]
        try:
            for path in self.paths:
                if time.monotonic() >= self.deadline:
                    raise TimeoutError('read_guard_deadline')
                before = path.stat()
                handle = self.api.CreateFileW(str(path), 0x80000000, 1, None, 3, 0x80, None)
                if handle in (None, ctypes.c_void_p(-1).value):
                    raise ctypes.WinError(ctypes.get_last_error())
                self.handles.append(handle)
                info = FileID()
                if not self.api.GetFileInformationByHandleEx(handle, 18, ctypes.byref(info), ctypes.sizeof(info)):
                    raise ctypes.WinError(ctypes.get_last_error())
                buffer = ctypes.create_unicode_buffer(32768)
                length = self.api.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
                if not 0 < length < len(buffer):
                    raise OSError('read_guard_native_path_unknown')
                native = buffer.value
                if native.startswith('\\\\?\\UNC\\'):
                    native = '\\\\' + native[8:]
                elif native.startswith('\\\\?\\'):
                    native = native[4:]
                if os.path.normcase(os.path.normpath(native)) != os.path.normcase(str(path)):
                    raise OSError('read_guard_native_path_changed')
                after = path.stat()
                identifier = int.from_bytes(bytes(info.identifier), 'little')
                if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino) or identifier != after.st_ino:
                    raise OSError('read_guard_file_identity_changed')
                if any(e['file_id'] == identifier and e['volume'] == info.volume for e in self.evidence):
                    raise OSError('read_guard_duplicate_file')
                self.evidence.append({'path': str(path), 'volume': info.volume, 'file_id': identifier,
                                      'share_read_only': True})
            return self
        except BaseException:
            self.close()
            raise

    def close(self):
        try:
            exited = all(child_exited(child) for child in self.children)
        except Exception:
            exited = False
        if not exited:
            if self not in _RETAINED_GUARDS:
                _RETAINED_GUARDS.append(self)
            raise RuntimeError('read_guard_child_shutdown_unconfirmed')
        try:
            workers_exited = all(not worker.is_alive() for worker in self.workers)
        except Exception:
            workers_exited = False
        if not workers_exited:
            if self not in _RETAINED_GUARDS:
                _RETAINED_GUARDS.append(self)
            raise RuntimeError('read_guard_stream_shutdown_unconfirmed')
        if self.close_fault:
            raise OSError('read_guard_close_unconfirmed')
        unconfirmed = []
        for handle in reversed(self.handles):
            try:
                closed = bool(self.api.CloseHandle(handle))
            except Exception:
                closed = False
            if not closed:
                unconfirmed.append(handle)
        self.handles[:] = list(reversed(unconfirmed))
        if unconfirmed:
            self.close_fault = True  # no blind retry of a possibly reused handle
            if self not in _RETAINED_GUARDS:
                _RETAINED_GUARDS.append(self)
            raise OSError('read_guard_close_unconfirmed')
        self.children.clear()
        self.workers.clear()
        if self in _RETAINED_GUARDS:
            _RETAINED_GUARDS.remove(self)

    def __exit__(self, *args):
        self.close()


def readonly_cli(cli, database, sql, init_file, *, guards, seconds=180):
    """Captured single native child with an explicit, confirmed shutdown.

    Calls share the guard's original cooperative deadline. OS creation, file
    calls and cleanup can overrun; this is not a hard whole-operation promise.
    The owning controller must confirm all guard/child cleanup before restart.
    """
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 1 < seconds <= 900:
        raise ValueError('invalid_readonly_cli_boundary')
    cli, database, init_file = (Path(p).resolve(strict=True) for p in (cli, database, init_file))
    wal = Path(str(database)+'-wal')
    required = {database} | ({wal.resolve(strict=True)} if wal.exists() else set())
    if (type(guards) is not ReadFiles or guards.close_fault
            or len(guards.handles) != len(guards.paths) or not required.issubset(guards.paths)
            or time.monotonic() >= guards.deadline):
        raise ValueError('invalid_readonly_cli_boundary')
    prefix = ('PRAGMA query_only=ON; PRAGMA busy_timeout=1000; PRAGMA cache_size=-32768; '
              'PRAGMA mmap_size=0; BEGIN; SELECT count(1) AS schema_objects FROM sqlite_schema; ')
    started = time.monotonic()
    payload = (prefix+sql+'; ROLLBACK;').encode()
    if len(payload) > 65536:
        raise ValueError('audit_cli_query_size')
    deadline = min(guards.deadline, time.monotonic()+seconds)
    # The init file is protected before its size is checked, until native exit.
    with ReadFiles([init_file, cli], deadline=guards.deadline) as auxiliary:
        if init_file.stat().st_size != 0 or time.monotonic() >= deadline:
            raise ValueError('invalid_readonly_cli_boundary')
        output, error = bytearray(), bytearray()
        lock, overflow, io_error = threading.Lock(), threading.Event(), threading.Event()
        def read(stream, buffer):
            try:
                while True:
                    chunk = stream.read(4096)
                    if not chunk: break
                    with lock:
                        room = 1048576-len(output)-len(error)
                        buffer.extend(chunk[:max(0, room)])
                        if len(chunk) > room:
                            overflow.set(); break
            except Exception:
                io_error.set()
        def write():
            try:
                offset = 0
                while offset < len(payload):
                    written = process.stdin.write(payload[offset:offset+1024])
                    if not written: raise OSError('audit_cli_input_closed')
                    offset += written
            except Exception:
                io_error.set()
            finally:
                process.stdin.close()
        # All fallible preparation precedes native creation. Stream references
        # are resolved only after Popen succeeds and cleanup protection starts.
        threads = [threading.Thread(target=lambda: read(process.stdout, output), daemon=True),
                   threading.Thread(target=lambda: read(process.stderr, error), daemon=True),
                   threading.Thread(target=write, daemon=True)]
        guards.workers.extend(threads)
        auxiliary.workers.extend(threads)
        timed_out = False
        process = subprocess.Popen([str(cli), '-readonly', '-batch', '-json', '-bail', '-init', str(init_file), str(database)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
                                   creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True)
        try:
            guards.children.append(process)
            auxiliary.children.append(process)
            for thread in threads: thread.start()
            while process.poll() is None:
                if overflow.is_set() or io_error.is_set() or time.monotonic() >= deadline-1:
                    timed_out = time.monotonic() >= deadline-1
                    process.kill()
                    break
                overflow.wait(.01)
            process.wait(timeout=max(.01, deadline-time.monotonic()))
            if not child_exited(process):
                raise RuntimeError('audit_cli_shutdown_unconfirmed')
            for thread in threads:
                thread.join(timeout=max(0, deadline-time.monotonic()))
            if any(thread.is_alive() for thread in threads):
                raise RuntimeError('audit_cli_stream_shutdown_unconfirmed')
            results = []
            if not overflow.is_set() and not io_error.is_set():
                text = bytes(output).decode('utf-8', errors='strict')
                decoder = json.JSONDecoder()
                while text.strip():
                    if time.monotonic() >= deadline:
                        timed_out = True; break
                    try:
                        result, end = decoder.raw_decode(text.lstrip())
                    except ValueError:
                        if timed_out: break
                        raise ValueError('audit_cli_response_invalid') from None
                    if not isinstance(result, list):
                        raise ValueError('audit_cli_response_invalid')
                    results.append(result)
                    text = text.lstrip()[end:]
            timed_out = timed_out or time.monotonic() >= deadline
            return {'returncode': process.returncode, 'timed_out': timed_out,
                    'complete': process.returncode == 0 and not timed_out and not overflow.is_set() and not io_error.is_set(),
                    'output_limit_exceeded': overflow.is_set(), 'io_error': io_error.is_set(),
                    'captured_bytes': len(output)+len(error), 'shared_deadline': guards.deadline,
                    'child_exit_confirmed': True, 'elapsed_seconds': round(time.monotonic()-started, 3),
                    'results': results, 'stderr_present': bool(error)}
        finally:
            # Covers all post-spawn setup, partial start and read/parse errors.
            # The same original deadline bounds kill/wait/join; unknown cleanup
            # leaves both the data and auxiliary guards retained.
            if process.poll() is None:
                process.kill()
            process.wait(timeout=max(.01, deadline-time.monotonic()))
            for thread in threads:
                if thread.ident is not None:
                    thread.join(timeout=max(0, deadline-time.monotonic()))
            if child_exited(process) and not any(thread.is_alive() for thread in threads):
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None: stream.close()
