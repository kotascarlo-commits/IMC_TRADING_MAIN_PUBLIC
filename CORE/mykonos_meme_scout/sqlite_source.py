"""WAL-aware, query-only extraction from idle SQLite research sources."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def readonly_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        # BEGIN alone is deferred; this read actually fixes the SQL snapshot.
        connection.execute("SELECT COUNT(1) FROM sqlite_schema").fetchone()
    except Exception:
        connection.close()
        raise
    return connection


def source_file_state(path: Path) -> dict:
    state = {}
    for name, file in (("main", path), ("wal", Path(str(path) + "-wal"))):
        try:
            stat = file.stat()
            state[name] = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        except FileNotFoundError:
            if name == "main":
                raise
            state[name] = None
    return state


def _source_hashes(path: Path, expected: dict) -> dict:
    if source_file_state(path) != expected:
        raise RuntimeError("SQLite source changed; export refused")
    hashes = {"main": sha256_file(path),
              "wal": sha256_file(Path(str(path) + "-wal")) if expected["wal"] is not None else None}
    if source_file_state(path) != expected:
        raise RuntimeError("SQLite source changed during hashing; export refused")
    return hashes


@contextmanager
def verified_source(path: Path):
    """Require an idle source through every feature/label query.

    A fixed WAL-aware SQL snapshot and two physical main/WAL hash passes.
    SQLite may update/create SHM. This is not a writer lock and cannot detect
    every transient change that is subsequently undone. Publish results only
    after successful context exit; the verification flag is false until then.
    """
    path = Path(path).resolve()
    state = source_file_state(path)
    connection = readonly_connection(path)
    try:
        hashes = _source_hashes(path, state)
        fingerprint = hashlib.sha256(json.dumps(
            {"version": 2, "main_sha256": hashes["main"], "wal_sha256": hashes["wal"]},
            sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        integrity = {"sha256": hashes["main"], "wal_sha256": hashes["wal"],
                     "source_fingerprint": fingerprint, "source_fingerprint_version": 2,
                     "wal_included": True, "source_main_wal_verified_unchanged": False,
                     "note": "Idle-source before/after checks; SHM bookkeeping may change."}
        yield connection, integrity
        if _source_hashes(path, state) != hashes:
            raise RuntimeError("SQLite source contents changed; export refused")
        integrity["source_main_wal_verified_unchanged"] = True
    finally:
        connection.close()
