"""Bounded error evidence without exception messages, locals or source text.

Used by the independent copy contract only on its failure path.
"""
import os
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {os.path.normcase(str(ROOT / relative)): Path(relative).name for relative in (
    'scripts/storage_copy_contract.py', 'scripts/storage_copy_worker.py',
    'scripts/offline_ack_read.py',
    'scripts/windows_read_guard.py', 'CORE/mykonos_meme_scout/storage_copy.py',
    'CORE/mykonos_meme_scout/compact_storage.py')}
CLASSES = {kind: kind.__name__ for kind in (ValueError, TypeError, OSError,
    PermissionError, FileNotFoundError, TimeoutError, RuntimeError,
    sqlite3.DatabaseError, sqlite3.OperationalError, sqlite3.IntegrityError)}
STAGES = {'PROTOCOL', 'WAIT_ACK', 'VALIDATE', 'PROVE', 'FINAL_PUBLISH'}


def evidence(error, stage):
    kind = CLASSES.get(type(error), 'UNKNOWN')
    code = getattr(error, 'winerror', None) if type(error) in CLASSES else None
    if type(code) is not int or code not in (5, 32):
        code = None
    location = None
    frame = error.__traceback__ if type(error) in CLASSES else None
    for _ in range(64):
        if frame is None:
            break
        name = SOURCES.get(os.path.normcase(os.path.abspath(frame.tb_frame.f_code.co_filename)))
        if name is not None and type(frame.tb_lineno) is int and 1 <= frame.tb_lineno <= 10000:
            location = {'file': name, 'line': frame.tb_lineno}
        frame = frame.tb_next
    return {'failure_stage': stage if type(stage) is str and stage in STAGES else 'UNKNOWN',
            'exception_kind': kind, 'failure_location': location, 'win32_error': code}
