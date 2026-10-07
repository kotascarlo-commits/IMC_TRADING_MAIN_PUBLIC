from __future__ import annotations

import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / "imc-resilience.db"

def _conn(timeout: float = 5.0) -> sqlite3.Connection:
    c = sqlite3.connect(DB, timeout=timeout)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
    c.execute("""CREATE TABLE IF NOT EXISTS rate_buckets(
        name TEXT PRIMARY KEY,
        next_allowed REAL NOT NULL DEFAULT 0,
        updated_at REAL NOT NULL DEFAULT 0,
        reason TEXT
    )""")
    c.commit()
    return c

def reserve(name: str, min_gap: float, *, wait: bool = True, max_wait: float = 0.65,
            timeout: float = 5.0) -> float:
    """Atomically reserve one request slot across all IMC processes.

    Returns 0 when the slot is acquired. If wait=False or the required wait is
    larger than max_wait, returns the remaining delay without reserving.
    """
    c = _conn(timeout)
    try:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT next_allowed FROM rate_buckets WHERE name=?", (name,)).fetchone()
        now = time.time()
        next_allowed = float(row[0]) if row else 0.0
        delay = max(0.0, next_allowed - now)
        if delay > 0 and ((not wait) or delay > max_wait):
            c.rollback()
            return delay
        slot = max(now, next_allowed)
        c.execute("""INSERT INTO rate_buckets(name,next_allowed,updated_at,reason)
                     VALUES(?,?,?,?)
                     ON CONFLICT(name) DO UPDATE SET
                       next_allowed=excluded.next_allowed,
                       updated_at=excluded.updated_at,
                       reason=excluded.reason""",
                  (name, slot + float(min_gap), now, "reserve"))
        c.commit()
    finally:
        c.close()
    if delay > 0:
        time.sleep(delay)
    return 0.0

def backoff(name: str, seconds: float, reason: str = "backoff", *, timeout: float = 5.0) -> None:
    c = _conn(timeout)
    try:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT next_allowed FROM rate_buckets WHERE name=?", (name,)).fetchone()
        now = time.time()
        next_allowed = max(float(row[0]) if row else 0.0, now + max(0.0, float(seconds)))
        c.execute("""INSERT INTO rate_buckets(name,next_allowed,updated_at,reason)
                     VALUES(?,?,?,?)
                     ON CONFLICT(name) DO UPDATE SET
                       next_allowed=excluded.next_allowed,
                       updated_at=excluded.updated_at,
                       reason=excluded.reason""",
                  (name, next_allowed, now, reason))
        c.commit()
    finally:
        c.close()

def snapshot() -> dict[str, dict[str, float | str]]:
    c = _conn()
    try:
        now = time.time()
        return {
            str(name): {
                "wait_seconds": round(max(0.0, float(next_allowed) - now), 3),
                "updated_at_epoch": float(updated_at),
                "reason": str(reason or ""),
            }
            for name, next_allowed, updated_at, reason
            in c.execute("SELECT name,next_allowed,updated_at,reason FROM rate_buckets").fetchall()
        }
    finally:
        c.close()
