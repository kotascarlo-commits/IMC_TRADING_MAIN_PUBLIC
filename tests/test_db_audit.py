"""Audit safety and evidence integrity, entirely on isolated databases."""
import importlib.util
import json
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("db_audit", Path(__file__).parents[1] / "scripts/db_audit.py")
db_audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(db_audit)


def _writer(path):
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE snapshots(id INTEGER PRIMARY KEY,captured_at TEXT)")
    connection.execute("INSERT INTO snapshots VALUES(1,'2026-10-01T23:30:00-02:00')")
    connection.commit()
    return connection


def test_reads_wal_without_data_writes_and_normalizes_growth(tmp_path):
    path = tmp_path / "source.db"
    writer = _writer(path)
    try:
        before = {p: p.read_bytes() for p in (path, Path(str(path) + "-wal"))}
        report = db_audit.audit(path, growth=True)
        assert report["complete"]
        assert report["tables"]["snapshots"]["row_count"] == 1
        assert report["tables"]["snapshots"]["daily_rows_utc"] == {"2026-10-02": 1}
        assert all(p.read_bytes() == contents for p, contents in before.items())
    finally:
        writer.close()


def test_existing_wal_without_shm_can_be_read(tmp_path):
    path = tmp_path / "source.db"
    writer = _writer(path)
    target = tmp_path / "copy.db"
    try:
        shutil.copyfile(path, target)
        shutil.copyfile(str(path) + "-wal", str(target) + "-wal")
        assert not Path(str(target) + "-shm").exists()
        report = db_audit.audit(target)
        assert report["complete"]
        assert report["tables"]["snapshots"]["row_count"] == 1
    finally:
        writer.close()


def test_missing_database_is_not_created(tmp_path):
    path = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        db_audit.audit(path)
    assert not path.exists()


def test_writer_commit_does_not_mix_read_snapshots(tmp_path, monkeypatch):
    path = tmp_path / "source.db"
    writer = _writer(path)
    original = db_audit._query
    try:
        def concurrent_query(connection, sql, deadline):
            rows = original(connection, sql, deadline)
            if "FROM sqlite_schema" in sql:
                writer.execute("INSERT INTO snapshots VALUES(2,'2026-10-02T02:00:00Z')")
                writer.commit()
            return rows
        monkeypatch.setattr(db_audit, "_query", concurrent_query)
        report = db_audit.audit(path)
        assert report["tables"]["snapshots"]["row_count"] == 1
        assert writer.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 2
    finally:
        writer.close()


def test_interrupted_query_is_not_reported_as_zero_or_partial(tmp_path, monkeypatch):
    path = tmp_path / "source.db"
    writer = _writer(path)
    original = db_audit._query
    try:
        def interrupted_count(connection, sql, deadline):
            if "COUNT(1) FROM" in sql:
                raise sqlite3.OperationalError("interrupted")
            return original(connection, sql, deadline)
        monkeypatch.setattr(db_audit, "_query", interrupted_count)
        report = db_audit.audit(path)
        assert not report["complete"]
        assert report["tables"]["snapshots"]["row_count"] is None
        assert any(e["query"] == "snapshots:count" for e in report["errors"])
    finally:
        writer.close()


def test_progress_budget_covers_full_fetch():
    with sqlite3.connect(":memory:") as connection:
        with pytest.raises((sqlite3.OperationalError, TimeoutError)):
            db_audit._query(connection, "WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL "
                            "SELECT x+1 FROM n WHERE x<10000000) SELECT sum(x) FROM n",
                            time.monotonic() + .01)
        assert connection.execute("SELECT 1").fetchone()[0] == 1


def test_exclusive_lock_yields_incomplete_report(tmp_path):
    path = tmp_path / "locked.db"
    writer = sqlite3.connect(path)
    writer.execute("CREATE TABLE snapshots(id INTEGER PRIMARY KEY,captured_at TEXT)")
    writer.commit()
    writer.execute("BEGIN EXCLUSIVE")
    try:
        report = db_audit.audit(path, lock_seconds=.02)
        assert not report["complete"]
        assert report["tables"] == {}
        assert report["errors"][0]["query"] == "schema"
    finally:
        writer.rollback()
        writer.close()


def test_multirow_fetch_interrupt_discards_rows():
    with sqlite3.connect(":memory:") as connection:
        with pytest.raises((sqlite3.OperationalError, TimeoutError)):
            db_audit._query(connection, "WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL "
                            "SELECT x+1 FROM n WHERE x<10000000) SELECT x FROM n",
                            time.monotonic() + .01)
        assert connection.execute("SELECT 1").fetchone()[0] == 1


def test_completed_but_late_fetch_is_discarded(monkeypatch):
    clock = [0.0]
    class Connection:
        handler = None
        def set_progress_handler(self, handler, interval):
            self.handler = handler
        def execute(self, sql):
            return self
        def fetchall(self):
            assert self.handler is not None
            clock[0] = 2.0
            return [(42,)]
    connection = Connection()
    with monkeypatch.context() as patch:
        patch.setattr(db_audit.time, "monotonic", lambda: clock[0])
        with pytest.raises(TimeoutError):
            db_audit._query(connection, "SELECT 42", 1.0)
    assert connection.handler is None


def test_cli_watchdog_emits_no_partial_counts(monkeypatch, capsys, tmp_path):
    def timeout(command, **kwargs):
        assert "--worker" in command
        assert kwargs["timeout"] == 4
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])
    monkeypatch.setattr(db_audit.subprocess, "run", timeout)
    assert db_audit.main(["--database", str(tmp_path / "source.db"), "--total-seconds", "1"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["complete"] is False
    assert report["tables"] == {}
    assert report["errors"][0]["query"] == "audit_worker"
