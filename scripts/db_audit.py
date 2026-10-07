"""Bounded, read-only SQLite audit. JSON goes to stdout; no cleanup is performed.

WAL is included using mode=ro, never immutable. SQLite may update/create SHM
bookkeeping; read-only refers to database contents. Prefer idle writers: the
read transaction pins a snapshot and may delay checkpoints. File measurements
are outside that snapshot. Time budgets are cooperative, not hard I/O deadlines.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parents[1] / "CORE/data/imc-main.db"
TIMESTAMPS = {
    "snapshots": "captured_at", "decisions": "captured_at",
    "learning_predictions": "predicted_at", "learning_outcomes": "measured_at",
    "cookie_ledger": "issued_at", "outcomes": "measured_at",
    "positions": "opened_at", "trades": "occurred_at",
}
GROWTH_TABLES = {
    "snapshots", "decisions", "learning_predictions", "learning_outcomes", "cookie_ledger"
}


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _files(path: Path) -> dict:
    result = {"measured_utc": _utc(), "outside_sql_snapshot": True, "files": {}}
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(path) + suffix)
        try:
            stat = p.stat()
            result["files"][p.name] = {"bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}
        except FileNotFoundError:
            result["files"][p.name] = None
    return result


def _query(connection: sqlite3.Connection, sql: str, deadline: float) -> list:
    if time.monotonic() >= deadline:
        raise TimeoutError("Audit time budget exhausted")
    connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
    try:
        # Keep the handler installed through fetching; do not return partial rows.
        rows = connection.execute(sql).fetchall()
        if time.monotonic() >= deadline:
            raise TimeoutError("Query exceeded cooperative time budget")
        return rows
    finally:
        connection.set_progress_handler(None, 0)


def audit(path: Path, *, query_seconds: float = 20, total_seconds: float = 120,
          lock_seconds: float = 1, pages: bool = False, growth: bool = False) -> dict:
    for value in (query_seconds, total_seconds, lock_seconds):
        if not math.isfinite(value) or value <= 0:
            raise ValueError("Time budgets must be finite and positive")
    path = path.resolve(strict=True)
    if not path.is_file():
        raise ValueError("Database path must be an existing file")
    started = time.monotonic()
    overall_deadline = started + total_seconds
    report = {
        "source": str(path), "started_utc": _utc(), "sqlite_version": sqlite3.sqlite_version,
        "opened_mode": "ro", "wal_included": True, "complete": True,
        "budgets_seconds": {"query": query_seconds, "total": total_seconds, "lock": lock_seconds},
        "files_before": _files(path), "errors": [], "tables": {},
        "note": "No data writes; SQLite may alter SHM bookkeeping. dbstat bytes are allocated, not reclaimable.",
    }
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True,
                                 timeout=min(lock_seconds, total_seconds))
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA temp_store=MEMORY")
        connection.execute("BEGIN")

        def run(label: str, sql: str) -> list | None:
            try:
                if not connection.in_transaction:
                    raise RuntimeError("Read snapshot lost; refusing to mix snapshots")
                return _query(connection, sql, min(overall_deadline, time.monotonic() + query_seconds))
            except (sqlite3.Error, TimeoutError, RuntimeError) as exc:
                report["complete"] = False
                report["errors"].append({"query": label, "error": str(exc)})
                return None

        schema = run("schema", "SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name")
        if schema is None:
            return report
        report["schema"] = [dict(zip(("type", "name", "table", "sql"), row)) for row in schema]
        for pragma in ("page_size", "page_count", "freelist_count"):
            value = run(pragma, "PRAGMA " + pragma)
            report[pragma] = value[0][0] if value else None

        if pages:
            objects = run("dbstat", "SELECT name,COUNT(*),SUM(pgsize) FROM dbstat GROUP BY name")
            report["object_storage"] = None if objects is None else [
                {"name": name, "pages": count, "allocated_bytes": size,
                 "table": next((row[2] for row in schema if row[1] == name), None)}
                for name, count, size in objects
            ]

        for kind, name, _, _ in schema:
            if kind != "table" or name.startswith("sqlite_"):
                continue
            table = {"row_count": None, "timestamp_range": None}
            report["tables"][name] = table
            # COUNT(*) can use SQLite's single OP_Count, whose B-tree walk
            # bypasses regular VM progress callbacks on very large tables.
            counts = run(name + ":count", f"SELECT COUNT(1) FROM {_identifier(name)}")
            if counts is not None:
                table["row_count"] = counts[0][0]
            timestamp = TIMESTAMPS.get(name)
            if timestamp:
                column = _identifier(timestamp)
                bounds = run(name + ":range", f"SELECT MIN({column}),MAX({column}) FROM {_identifier(name)}")
                if bounds is not None:
                    table["timestamp_range"] = {"column": timestamp, "min": bounds[0][0], "max": bounds[0][1],
                                                "ordering": "stored ISO text, not normalized chronology"}
                if growth and name in GROWTH_TABLES:
                    hours = run(name + ":growth", f"SELECT strftime('%Y-%m-%dT%H:00:00Z',{column}),COUNT(*) "
                                f"FROM {_identifier(name)} GROUP BY 1 ORDER BY 1")
                    table["hourly_rows_utc"] = None
                    if hours is not None:
                        table["hourly_rows_utc"] = [{"hour": h, "rows": n} for h, n in hours if h is not None]
                        table["invalid_timestamp_rows"] = sum(n for h, n in hours if h is None)
                        daily = {}
                        for hour, count in hours:
                            if hour is not None:
                                daily[hour[:10]] = daily.get(hour[:10], 0) + count
                        table["daily_rows_utc"] = daily
        return report
    finally:
        connection.set_progress_handler(None, 0)
        connection.close()
        report["files_after"] = _files(path)
        report["finished_utc"] = _utc()
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    parser.add_argument("--query-seconds", type=float, default=20)
    parser.add_argument("--total-seconds", type=float, default=120)
    parser.add_argument("--lock-seconds", type=float, default=1)
    parser.add_argument("--pages", action="store_true", help="Bounded dbstat scan of allocated object pages")
    parser.add_argument("--growth", action="store_true", help="Exact UTC hourly/daily row counts, potentially expensive")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not args.worker:
        if any(not math.isfinite(v) or v <= 0 for v in (args.query_seconds, args.total_seconds, args.lock_seconds)):
            parser.error("Time budgets must be finite and positive")
        command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--database", str(args.database),
                   "--query-seconds", str(args.query_seconds), "--total-seconds", str(args.total_seconds),
                   "--lock-seconds", str(args.lock_seconds)]
        command += (["--pages"] if args.pages else []) + (["--growth"] if args.growth else [])
        try:
            # subprocess.run kills and waits for only its own child on timeout.
            # The grace period allows normal connection/report shutdown.
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                    timeout=args.total_seconds + args.lock_seconds + 2)
            report = json.loads(result.stdout)
            print(json.dumps(report, indent=2))
            return result.returncode
        except (subprocess.TimeoutExpired, OSError, ValueError) as exc:
            print(json.dumps({"source": str(args.database.resolve()), "complete": False, "tables": {},
                              "errors": [{"query": "audit_worker", "error": str(exc)}],
                              "finished_utc": _utc()}, indent=2))
            return 2
    try:
        report = audit(args.database, query_seconds=args.query_seconds, total_seconds=args.total_seconds,
                       lock_seconds=args.lock_seconds, pages=args.pages, growth=args.growth)
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(json.dumps({"source": str(args.database.resolve()), "complete": False, "tables": {},
                          "errors": [{"query": "audit_initialization", "error": str(exc)}]}))
        return 1
    print(json.dumps(report, indent=2))
    return 0 if report["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
