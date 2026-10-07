from __future__ import annotations

import json
import socket
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from .models import Assessment, SecuritySignals, TokenSnapshot, utc_now
from .compact_storage import DictionaryStorage, detect_format, validate_store_schema

_DECISION_BUS_SOCKET = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
_DECISION_BUS_TARGETS = (("127.0.0.1", 48125), ("127.0.0.1", 49225), ("127.0.0.1", 49425))


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS snapshots (
  id INTEGER PRIMARY KEY, captured_at TEXT NOT NULL, address TEXT NOT NULL,
  symbol TEXT NOT NULL, price_usd REAL NOT NULL, payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_address_time ON snapshots(address, captured_at DESC);
CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY, captured_at TEXT NOT NULL, address TEXT NOT NULL,
  symbol TEXT NOT NULL, decision TEXT NOT NULL, score INTEGER NOT NULL,
  confidence INTEGER NOT NULL, reasons_json TEXT NOT NULL, components_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decisions_address_time ON decisions(address, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_decisions_captured_address_id ON decisions(captured_at,address,id);
CREATE TABLE IF NOT EXISTS positions (
  id INTEGER PRIMARY KEY, address TEXT NOT NULL, symbol TEXT NOT NULL,
  status TEXT NOT NULL, entry_price REAL NOT NULL, quantity REAL NOT NULL,
  remaining_quantity REAL NOT NULL, high_price REAL NOT NULL,
  opened_at TEXT NOT NULL, closed_at TEXT, realized_pnl REAL NOT NULL DEFAULT 0,
  partial_1_done INTEGER NOT NULL DEFAULT 0, partial_2_done INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_open_position ON positions(address) WHERE status='OPEN';
CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY, occurred_at TEXT NOT NULL, position_id INTEGER NOT NULL,
  side TEXT NOT NULL, price REAL NOT NULL, quantity REAL NOT NULL,
  gross_usd REAL NOT NULL, cost_usd REAL NOT NULL, reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outcomes (
  decision_id INTEGER NOT NULL, horizon_minutes INTEGER NOT NULL,
  measured_at TEXT NOT NULL, entry_price REAL NOT NULL, exit_price REAL NOT NULL,
  return_pct REAL NOT NULL, PRIMARY KEY(decision_id, horizon_minutes)
);
CREATE TABLE IF NOT EXISTS model_registry (
  model_family TEXT NOT NULL, model_version TEXT NOT NULL, created_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'SHADOW', config_json TEXT NOT NULL,
  PRIMARY KEY(model_family, model_version)
);
CREATE TABLE IF NOT EXISTS learning_predictions (
  id INTEGER PRIMARY KEY, model_family TEXT NOT NULL, model_version TEXT NOT NULL,
  address TEXT NOT NULL, symbol TEXT NOT NULL, predicted_at TEXT NOT NULL,
  horizon_minutes INTEGER NOT NULL, entry_price REAL NOT NULL, entry_liquidity REAL NOT NULL,
  p5 REAL NOT NULL, p10 REAL NOT NULL, p25 REAL NOT NULL, p_rug REAL NOT NULL,
  p_stagnation REAL NOT NULL, uncertainty REAL NOT NULL, expected_utility REAL NOT NULL,
  reasons_json TEXT NOT NULL,
  UNIQUE(model_family,model_version,address,predicted_at,horizon_minutes)
);
CREATE INDEX IF NOT EXISTS idx_learning_address_time ON learning_predictions(address, predicted_at DESC);
CREATE TABLE IF NOT EXISTS learning_outcomes (
  prediction_id INTEGER PRIMARY KEY, measured_at TEXT NOT NULL,
  hit_5 INTEGER NOT NULL, hit_10 INTEGER NOT NULL, hit_25 INTEGER NOT NULL,
  rug INTEGER NOT NULL, stagnant INTEGER NOT NULL,
  max_return_pct REAL NOT NULL, min_return_pct REAL NOT NULL, return_pct REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS cookie_ledger (
  id INTEGER PRIMARY KEY, prediction_id INTEGER NOT NULL UNIQUE,
  model_family TEXT NOT NULL, issued_at TEXT NOT NULL,
  total_reward REAL NOT NULL, components_json TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS learning_predictions_no_update BEFORE UPDATE ON learning_predictions
BEGIN SELECT RAISE(ABORT, 'learning_predictions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS learning_predictions_no_delete BEFORE DELETE ON learning_predictions
BEGIN SELECT RAISE(ABORT, 'learning_predictions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS learning_outcomes_no_update BEFORE UPDATE ON learning_outcomes
BEGIN SELECT RAISE(ABORT, 'learning_outcomes are append-only'); END;
CREATE TRIGGER IF NOT EXISTS learning_outcomes_no_delete BEFORE DELETE ON learning_outcomes
BEGIN SELECT RAISE(ABORT, 'learning_outcomes are append-only'); END;
CREATE TRIGGER IF NOT EXISTS cookie_ledger_no_update BEFORE UPDATE ON cookie_ledger
BEGIN SELECT RAISE(ABORT, 'cookie_ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS cookie_ledger_no_delete BEFORE DELETE ON cookie_ledger
BEGIN SELECT RAISE(ABORT, 'cookie_ledger is append-only'); END;
CREATE TABLE IF NOT EXISTS account_state (
  id INTEGER PRIMARY KEY CHECK(id=1), cash_usd REAL NOT NULL,
  initial_equity REAL NOT NULL, high_water_equity REAL NOT NULL,
  day_anchor TEXT NOT NULL, day_start_equity REAL NOT NULL,
  week_anchor TEXT NOT NULL, week_start_equity REAL NOT NULL,
  kill_reason TEXT
);
CREATE TABLE IF NOT EXISTS schema_meta (
  key TEXT PRIMARY KEY, value TEXT NOT NULL
);
"""


def _period_keys(iso_timestamp: str) -> tuple[str, str]:
    value = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
    iso = value.isocalendar()
    return value.date().isoformat(), f"{iso.year}-W{iso.week:02d}"


class Store:
    def __init__(self, path: Path, initial_cash: float = 1000.0):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path, timeout=10.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout=10000")
        try:
            if detect_format(self.conn) is None:
                self.conn.executescript(SCHEMA)
                self._dictionary_storage = None
            else:
                validate_store_schema(self.conn, SCHEMA)
                self.conn.execute('PRAGMA journal_mode=WAL')
                self._dictionary_storage = DictionaryStorage(self.conn)
        except Exception:
            self.conn.close()
            raise
        now = utc_now()
        day, week = _period_keys(now)
        self.conn.execute(
            "INSERT OR IGNORE INTO account_state VALUES(1,?,?,?,?,?,?,?,NULL)",
            (initial_cash, initial_cash, initial_cash, day, initial_cash, week, initial_cash),
        )
        self.conn.commit()
        self._migrate_net_pnl_basis_v039()
        self._ensure_v0310_forward_validation_baseline()

    def _migrate_net_pnl_basis_v039(self) -> None:
        """Repair derived position PnL to include the BUY-side execution cost.

        The immutable trade ledger has always debited BUY gross_usd from cash,
        but older position.realized_pnl used the post-cost token quantity times
        entry price as basis. That omitted the BUY cost from reported PnL.
        Rebuilding from BUY/SELL ledger rows is deterministic and idempotent.
        """
        key = "position_pnl_includes_buy_cost_v0_3_9"
        row = self.conn.execute("SELECT value FROM schema_meta WHERE key=?", (key,)).fetchone()
        if row is not None:
            return
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute("SELECT value FROM schema_meta WHERE key=?", (key,)).fetchone()
            if row is None:
                positions = self.conn.execute("SELECT id,quantity FROM positions ORDER BY id").fetchall()
                for pos in positions:
                    buy = self.conn.execute(
                        "SELECT quantity,gross_usd FROM trades WHERE position_id=? AND side='BUY' ORDER BY id LIMIT 1",
                        (pos["id"],),
                    ).fetchone()
                    if buy is None or float(buy["quantity"] or 0) <= 0:
                        continue
                    buy_qty = float(buy["quantity"])
                    buy_gross = float(buy["gross_usd"])
                    sells = self.conn.execute(
                        "SELECT quantity,gross_usd,cost_usd FROM trades WHERE position_id=? AND side='SELL' ORDER BY id",
                        (pos["id"],),
                    ).fetchall()
                    realized = 0.0
                    for sell in sells:
                        q = float(sell["quantity"])
                        allocated_basis = buy_gross * (q / buy_qty)
                        sell_net = float(sell["gross_usd"]) - float(sell["cost_usd"])
                        realized += sell_net - allocated_basis
                    self.conn.execute(
                        "UPDATE positions SET realized_pnl=? WHERE id=?",
                        (realized, pos["id"]),
                    )
                self.conn.execute(
                    "INSERT INTO schema_meta(key,value) VALUES(?,?)", (key, utc_now())
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise


    def _ensure_v0310_forward_validation_baseline(self) -> None:
        """Freeze the legacy-history boundary for the standard-only forward sample.

        The marker is created once when a v0.3.10 process first opens an upgraded
        database. Later reports can therefore separate legacy mixed-lane history
        from trades opened under the standard-execution / probe-shadow policy.
        """
        key = "forward_validation_v0_3_10_baseline_position_id"
        row = self.conn.execute("SELECT value FROM schema_meta WHERE key=?", (key,)).fetchone()
        if row is not None:
            return
        max_id = int(self.conn.execute("SELECT COALESCE(MAX(id),0) AS n FROM positions").fetchone()["n"] or 0)
        self.conn.execute("INSERT INTO schema_meta(key,value) VALUES(?,?)", (key, str(max_id)))
        self.conn.execute(
            "INSERT OR IGNORE INTO schema_meta(key,value) VALUES(?,?)",
            ("forward_validation_v0_3_10_started_at", utc_now()),
        )
        self.conn.commit()

    def forward_validation_v0310(self) -> dict:
        row = self.conn.execute(
            "SELECT value FROM schema_meta WHERE key='forward_validation_v0_3_10_baseline_position_id'"
        ).fetchone()
        baseline = int(row["value"]) if row else 0
        started = self.conn.execute(
            "SELECT value FROM schema_meta WHERE key='forward_validation_v0_3_10_started_at'"
        ).fetchone()
        positions = [dict(r) for r in self.conn.execute(
            "SELECT * FROM positions WHERE id>? ORDER BY id", (baseline,)
        )]
        closed = [r for r in positions if r["status"] == "CLOSED"]
        pnls = [float(r["realized_pnl"]) for r in closed]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        gp, gl = sum(wins), -sum(losses)
        lanes: dict[str, int] = {}
        for r in positions:
            lane = self._entry_lane_for_position(int(r["id"]))
            lanes[lane] = lanes.get(lane, 0) + 1
        return {
            "baseline_position_id": baseline,
            "started_at": str(started["value"]) if started else None,
            "new_positions": len(positions),
            "closed_positions": len(closed),
            "open_positions": len(positions) - len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "realized_pnl_usd": round(sum(pnls), 4),
            "profit_factor": round(gp / gl, 3) if gl > 0 else None,
            "lanes": lanes,
            "policy": "STANDARD execution only; EARLY_PROBE remains shadow-only",
        }

    def close(self) -> None:
        self.conn.close()

    def record_snapshot(self, snapshot: TokenSnapshot) -> None:
        values=(snapshot.captured_at, snapshot.address, snapshot.symbol, snapshot.price_usd,
                json.dumps(snapshot.as_dict(), separators=(",", ":")))
        if self._dictionary_storage is not None:
            self._dictionary_storage.snapshot((None,*values))
        else:
            self.conn.execute(
                "INSERT INTO snapshots(captured_at,address,symbol,price_usd,payload_json) VALUES(?,?,?,?,?)",values)
        self.conn.commit()

    def latest_snapshot(self, address: str) -> TokenSnapshot | None:
        row = self.conn.execute(
            "SELECT payload_json FROM snapshots WHERE address=? ORDER BY id DESC LIMIT 1", (address,)
        ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        sec = payload.get("security") or {}
        payload["security"] = SecuritySignals(**{
            key: sec.get(key) for key in SecuritySignals.__dataclass_fields__
        })
        allowed = TokenSnapshot.__dataclass_fields__
        return TokenSnapshot(**{key: value for key, value in payload.items() if key in allowed})

    def snapshot_at_or_before(self, address: str, captured_at: str) -> TokenSnapshot | None:
        """Return the last stored market snapshot at/before a timestamp.

        The low-latency watcher uses this to recover the exact pair that was
        present when a position was opened, instead of following a later
        most-liquid-pair switch for the same mint.
        """
        row = self.conn.execute(
            "SELECT payload_json FROM snapshots WHERE address=? AND captured_at<=? ORDER BY id DESC LIMIT 1",
            (address, captured_at),
        ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        sec = payload.get("security") or {}
        payload["security"] = SecuritySignals(**{
            key: sec.get(key) for key in SecuritySignals.__dataclass_fields__
        })
        allowed = TokenSnapshot.__dataclass_fields__
        return TokenSnapshot(**{key: value for key, value in payload.items() if key in allowed})

    def record_decision(self, snapshot: TokenSnapshot, result: Assessment, extra: Iterable[str] = (),
                        decision_override: str | None = None) -> None:
        reasons = list(result.hard_failures) + list(extra) + list(result.warnings)
        decision = decision_override or result.decision
        values=(snapshot.captured_at, snapshot.address, snapshot.symbol, decision, result.score,
                result.confidence, json.dumps(reasons), json.dumps(result.components))
        if self._dictionary_storage is not None:
            self._dictionary_storage.decision((None,*values))
        else:
            self.conn.execute(
                "INSERT INTO decisions(captured_at,address,symbol,decision,score,confidence,reasons_json,components_json) VALUES(?,?,?,?,?,?,?,?)",values)
        self.conn.commit()
        if decision == "WATCH":
            try:
                payload = json.dumps({
                    "address": snapshot.address, "captured_at": snapshot.captured_at,
                    "symbol": snapshot.symbol, "decision": decision,
                    "emitted_at": utc_now(),
                }, separators=(",", ":")).encode("utf-8")
                for target in _DECISION_BUS_TARGETS:
                    try:
                        _DECISION_BUS_SOCKET.sendto(payload, target)
                    except OSError:
                        continue
            except (OSError, TypeError, ValueError):
                pass

    def recent_pass_count(self, address: str, limit: int = 2) -> int:
        rows = self.conn.execute(
            "SELECT decision FROM decisions WHERE address=? ORDER BY id DESC LIMIT ?", (address, limit)
        ).fetchall()
        return sum(1 for row in rows if row["decision"] == "PASS") if len(rows) == limit else 0

    def account(self) -> sqlite3.Row:
        return self.conn.execute("SELECT * FROM account_state WHERE id=1").fetchone()

    def open_positions(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM positions WHERE status='OPEN' ORDER BY id").fetchall()

    def open_position(self, address: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM positions WHERE status='OPEN' AND address=?", (address,)
        ).fetchone()

    def risk_exit_reentry_block_reason(self, address: str) -> str | None:
        """Return the latest terminal risk-exit reason for this exact token address.

        A Solana mint address uniquely identifies the token. Entries are already
        constrained to the first-15-minute launch window, so once that same mint
        has been force-closed for a rug/security reason, reopening it during the
        remaining launch window would only create churn.
        """
        row = self.conn.execute(
            """SELECT t.reason FROM trades t
            JOIN positions p ON p.id=t.position_id
            WHERE p.address=? AND t.side='SELL' AND t.reason IN (
                'rug_shield_liquidity_collapse',
                'rug_shield_price_flow_collapse',
                'safety_gate_failed'
            )
            ORDER BY t.id DESC LIMIT 1""",
            (address,),
        ).fetchone()
        return str(row["reason"]) if row else None

    def execute_buy(self, snapshot: TokenSnapshot, gross_usd: float, cost_usd: float,
                    reason: str = "first15_qualified_entry") -> int:
        qty = (gross_usd - cost_usd) / snapshot.price_usd
        cur = self.conn.execute(
            "INSERT INTO positions(address,symbol,status,entry_price,quantity,remaining_quantity,high_price,opened_at) VALUES(?,?,'OPEN',?,?,?,?,?)",
            (snapshot.address, snapshot.symbol, snapshot.price_usd, qty, qty, snapshot.price_usd, snapshot.captured_at),
        )
        pid = int(cur.lastrowid)
        self.conn.execute("UPDATE account_state SET cash_usd=cash_usd-? WHERE id=1", (gross_usd,))
        self.conn.execute(
            "INSERT INTO trades(occurred_at,position_id,side,price,quantity,gross_usd,cost_usd,reason) VALUES(?,?,?,?,?,?,?,?)",
            (snapshot.captured_at, pid, "BUY", snapshot.price_usd, qty, gross_usd, cost_usd, reason),
        )
        self.conn.commit()
        return pid

    def execute_sell(self, position_id: int, snapshot: TokenSnapshot, quantity: float,
                     cost_pct: float, reason: str) -> None:
        pos = self.conn.execute("SELECT * FROM positions WHERE id=?", (position_id,)).fetchone()
        if pos is None:
            return
        quantity = min(quantity, pos["remaining_quantity"])
        gross = quantity * snapshot.price_usd
        cost = gross * cost_pct / 100
        net = gross - cost
        buy = self.conn.execute(
            "SELECT quantity,gross_usd FROM trades WHERE position_id=? AND side='BUY' ORDER BY id LIMIT 1",
            (position_id,),
        ).fetchone()
        if buy is not None and float(buy["quantity"] or 0) > 0:
            basis = float(buy["gross_usd"]) * (quantity / float(buy["quantity"]))
        else:
            basis = quantity * pos["entry_price"]
        pnl = net - basis
        remaining = pos["remaining_quantity"] - quantity
        status = "CLOSED" if remaining <= 1e-12 else "OPEN"
        self.conn.execute(
            "UPDATE positions SET remaining_quantity=?,status=?,closed_at=CASE WHEN ?='CLOSED' THEN ? ELSE closed_at END,realized_pnl=realized_pnl+? WHERE id=?",
            (max(0.0, remaining), status, status, snapshot.captured_at, pnl, position_id),
        )
        self.conn.execute("UPDATE account_state SET cash_usd=cash_usd+? WHERE id=1", (net,))
        self.conn.execute(
            "INSERT INTO trades(occurred_at,position_id,side,price,quantity,gross_usd,cost_usd,reason) VALUES(?,?,?,?,?,?,?,?)",
            (snapshot.captured_at, position_id, "SELL", snapshot.price_usd, quantity, gross, cost, reason),
        )
        self.conn.commit()

    def update_position_flags(self, position_id: int, high_price: float | None = None,
                              partial_1: bool | None = None, partial_2: bool | None = None) -> None:
        pos = self.conn.execute("SELECT * FROM positions WHERE id=?", (position_id,)).fetchone()
        self.conn.execute(
            "UPDATE positions SET high_price=?,partial_1_done=?,partial_2_done=? WHERE id=?",
            (high_price if high_price is not None else pos["high_price"],
             int(partial_1) if partial_1 is not None else pos["partial_1_done"],
             int(partial_2) if partial_2 is not None else pos["partial_2_done"], position_id),
        )
        self.conn.commit()

    def update_equity_state(self, equity: float, kill_reason: str | None = None) -> None:
        row = self.account()
        now = utc_now()
        day, week = _period_keys(now)
        new_day = row["day_anchor"] != day
        new_week = row["week_anchor"] != week
        day_start = equity if new_day else row["day_start_equity"]
        week_start = equity if new_week else row["week_start_equity"]
        active_kill = row["kill_reason"]
        if active_kill == "daily_loss_limit" and new_day:
            active_kill = None
        if active_kill == "weekly_loss_limit" and new_week:
            active_kill = None
        if kill_reason:
            active_kill = kill_reason
        self.conn.execute(
            "UPDATE account_state SET high_water_equity=?,day_anchor=?,day_start_equity=?,week_anchor=?,week_start_equity=?,kill_reason=? WHERE id=1",
            (max(row["high_water_equity"], equity), day, day_start, week, week_start, active_kill),
        )
        self.conn.commit()

    def _entry_lane_for_position(self, position_id: int) -> str:
        row = self.conn.execute(
            "SELECT reason FROM trades WHERE position_id=? AND side='BUY' ORDER BY id LIMIT 1", (position_id,)
        ).fetchone()
        if row and row["reason"] == "first15_qualified_entry":
            return "STANDARD"
        if row and row["reason"] == "early_probe_entry":
            return "EARLY_PROBE"
        return "UNKNOWN"

    def _rug_shield_summary(self) -> dict:
        rows = self.conn.execute(
            """SELECT p.*, t.reason AS rug_reason, t.price AS rug_exit_price,
            t.occurred_at AS rug_exit_at
            FROM positions p
            JOIN trades t ON t.position_id=p.id AND t.side='SELL'
            WHERE p.status='CLOSED' AND t.reason LIKE 'rug_shield_%'
            ORDER BY p.id"""
        ).fetchall()
        # A position should only have one terminal rug-shield exit, but dedupe by
        # position id defensively so partial exits never double-count PnL.
        by_position: dict[int, sqlite3.Row] = {}
        for row in rows:
            by_position[int(row["id"])] = row
        positions = list(by_position.values())
        pnls = [float(row["realized_pnl"]) for row in positions]
        details = []
        for row in positions[-5:]:
            opened = datetime.fromisoformat(str(row["opened_at"]).replace("Z", "+00:00"))
            exited = datetime.fromisoformat(str(row["rug_exit_at"]).replace("Z", "+00:00"))
            details.append({
                "id": int(row["id"]),
                "symbol": row["symbol"],
                "lane": self._entry_lane_for_position(int(row["id"])),
                "reason": row["rug_reason"],
                "pnl_usd": round(float(row["realized_pnl"]), 4),
                "entry_price": row["entry_price"],
                "exit_price": row["rug_exit_price"],
                "held_seconds": round((exited - opened).total_seconds(), 1),
            })
        token_groups: dict[str, dict] = {}
        for row in positions:
            address = str(row["address"])
            group = token_groups.setdefault(address, {
                "address": address, "symbol": row["symbol"], "positions": 0,
                "wins": 0, "losses": 0, "realized_pnl_usd": 0.0,
            })
            pnl = float(row["realized_pnl"])
            group["positions"] += 1
            group["wins"] += int(pnl > 0)
            group["losses"] += int(pnl < 0)
            group["realized_pnl_usd"] += pnl
        by_token = sorted(token_groups.values(), key=lambda item: (-item["positions"], item["address"]))
        for item in by_token:
            item["realized_pnl_usd"] = round(float(item["realized_pnl_usd"]), 4)

        return {
            "positions": len(positions),
            "wins": sum(1 for pnl in pnls if pnl > 0),
            "losses": sum(1 for pnl in pnls if pnl < 0),
            "realized_pnl_usd": round(sum(pnls), 4),
            "mean_pnl_usd": round(sum(pnls) / len(pnls), 4) if pnls else None,
            "recent": details,
            "by_token": by_token[:10],
            "counterfactual_saved_pnl_usd": None,
            "note": "Actual realized PnL only; avoided loss is not estimated without a valid counterfactual.",
        }

    def _reentry_guard_summary(self) -> dict:
        positions = [dict(row) for row in self.conn.execute(
            "SELECT * FROM positions ORDER BY address,id"
        )]
        by_address: dict[str, list[dict]] = {}
        for row in positions:
            by_address.setdefault(str(row["address"]), []).append(row)

        first_closed: list[float] = []
        reentry_closed: list[float] = []
        reentry_positions = 0
        for rows in by_address.values():
            for index, row in enumerate(rows):
                if index > 0:
                    reentry_positions += 1
                if row["status"] != "CLOSED":
                    continue
                target = first_closed if index == 0 else reentry_closed
                target.append(float(row["realized_pnl"]))

        blocked_rows = self.conn.execute(
            "SELECT address FROM decisions WHERE reasons_json LIKE '%rug_reentry_blocked%'"
        ).fetchall()
        risk_locked = self.conn.execute(
            """SELECT COUNT(DISTINCT p.address) AS n FROM positions p
            JOIN trades t ON t.position_id=p.id AND t.side='SELL'
            WHERE t.reason IN ('rug_shield_liquidity_collapse',
                               'rug_shield_price_flow_collapse',
                               'safety_gate_failed')"""
        ).fetchone()["n"]

        total = len(positions)
        return {
            "unique_traded_tokens": len(by_address),
            "total_positions": total,
            "historical_reentry_positions": reentry_positions,
            "historical_reentry_rate_pct": round(100 * reentry_positions / total, 2) if total else 0.0,
            "risk_exit_locked_tokens": int(risk_locked or 0),
            "blocked_decisions": len(blocked_rows),
            "unique_blocked_tokens": len({str(row["address"]) for row in blocked_rows}),
            "first_entries": {
                "closed": len(first_closed),
                "realized_pnl_usd": round(sum(first_closed), 4),
                "mean_pnl_usd": round(sum(first_closed) / len(first_closed), 4) if first_closed else None,
            },
            "reentries": {
                "closed": len(reentry_closed),
                "realized_pnl_usd": round(sum(reentry_closed), 4),
                "mean_pnl_usd": round(sum(reentry_closed) / len(reentry_closed), 4) if reentry_closed else None,
            },
            "policy": "Exact token address is blocked from re-entry after rug_shield_* or safety_gate_failed within the launch window.",
        }

    @staticmethod
    def _profit_concentration(pnls: list[float]) -> dict:
        positive = sorted((p for p in pnls if p > 0), reverse=True)
        losses = [p for p in pnls if p < 0]
        net = sum(pnls)
        gross_profit = sum(positive)
        gross_loss = -sum(losses)
        best = positive[0] if positive else 0.0
        top3 = sum(positive[:3])
        gp_without_best = max(0.0, gross_profit - best)
        pf_without_best = gp_without_best / gross_loss if gross_loss > 0 else None
        return {
            "best_trade_share_of_net_pnl_pct": round(100 * best / net, 2) if net > 0 and best > 0 else None,
            "best_trade_share_of_gross_profit_pct": round(100 * best / gross_profit, 2) if gross_profit > 0 else None,
            "top3_share_of_gross_profit_pct": round(100 * top3 / gross_profit, 2) if gross_profit > 0 else None,
            "net_pnl_without_best_trade_usd": round(net - best, 4) if positive else round(net, 4),
            "profit_factor_without_best_trade": round(pf_without_best, 3) if pf_without_best is not None else None,
        }

    def exit_shadow_summary(self, cost_pct: float = 1.0) -> list[dict]:
        """Replay closed positions through several *shadow-only* profit-lock policies.

        These policies never place trades. They reuse recorded snapshots so we can
        compare exit shapes before changing the real PAPER execution logic.
        """
        policies = [
            ("baseline_35_trail8", 0.35, 0.35, 8.0),
            ("lock50_trail7", 0.50, 0.25, 7.0),
            ("lock65_trail6", 0.65, 0.20, 6.0),
            ("runner40_trail5", 0.40, 0.30, 5.0),
        ]
        positions = self.conn.execute(
            "SELECT * FROM positions WHERE status='CLOSED' ORDER BY id"
        ).fetchall()
        aggregate = {name: [] for name, *_ in policies}
        for pos in positions:
            buy = self.conn.execute(
                "SELECT gross_usd FROM trades WHERE position_id=? AND side='BUY' ORDER BY id LIMIT 1", (pos["id"],)
            ).fetchone()
            if not buy or float(buy["gross_usd"]) <= 0:
                continue
            path = self.conn.execute(
                "SELECT captured_at,price_usd FROM snapshots WHERE address=? AND captured_at>=? AND captured_at<=? "
                "ORDER BY captured_at,id",
                (pos["address"], pos["opened_at"], pos["closed_at"]),
            ).fetchall()
            terminal = self.conn.execute(
                "SELECT price FROM trades WHERE position_id=? AND side='SELL' ORDER BY id DESC LIMIT 1", (pos["id"],)
            ).fetchone()
            if not terminal:
                continue
            entry = float(pos["entry_price"])
            initial_qty = float(pos["quantity"])
            gross_in = float(buy["gross_usd"])
            terminal_price = float(terminal["price"])
            for name, p1_frac, p2_frac, trail_pct in policies:
                remaining = initial_qty
                proceeds = 0.0
                p1_done = False
                p2_done = False
                high = entry
                exited = False
                for snap in path:
                    price = float(snap["price_usd"])
                    if price <= 0:
                        continue
                    high = max(high, price)
                    if not p1_done and price >= entry * 1.15:
                        q = min(remaining, initial_qty * p1_frac)
                        proceeds += q * price * (1 - cost_pct / 100)
                        remaining -= q
                        p1_done = True
                    if remaining > 0 and not p2_done and price >= entry * 1.30:
                        q = min(remaining, initial_qty * p2_frac)
                        proceeds += q * price * (1 - cost_pct / 100)
                        remaining -= q
                        p2_done = True
                    if remaining > 0 and high >= entry * 1.10 and price <= high * (1 - trail_pct / 100):
                        proceeds += remaining * price * (1 - cost_pct / 100)
                        remaining = 0.0
                        exited = True
                        break
                if remaining > 0:
                    proceeds += remaining * terminal_price * (1 - cost_pct / 100)
                pnl = proceeds - gross_in
                aggregate[name].append(pnl)
        output = []
        for name, values in aggregate.items():
            if not values:
                output.append({"policy": name, "positions": 0})
                continue
            output.append({
                "policy": name,
                "positions": len(values),
                "simulated_pnl_usd": round(sum(values), 4),
                "mean_pnl_usd": round(sum(values) / len(values), 4),
                "worst_position_usd": round(min(values), 4),
                "profitable_positions": sum(1 for value in values if value > 0),
            })
        output.sort(key=lambda row: row.get("simulated_pnl_usd", float("-inf")), reverse=True)
        return output

    def _paper_equity_before_trade(self, trade_id: int, occurred_at: str) -> float:
        """Reconstruct marked PAPER equity immediately before a ledger trade."""
        initial = float(self.account()["initial_equity"])
        cash = initial
        holdings: dict[int, float] = {}
        rows = self.conn.execute(
            """SELECT id,occurred_at,position_id,side,quantity,gross_usd,cost_usd
            FROM trades WHERE occurred_at<? OR (occurred_at=? AND id<?)
            ORDER BY occurred_at,id""",
            (occurred_at, occurred_at, trade_id),
        ).fetchall()
        for trade in rows:
            pid = int(trade["position_id"])
            q = float(trade["quantity"])
            if trade["side"] == "BUY":
                cash -= float(trade["gross_usd"])
                holdings[pid] = holdings.get(pid, 0.0) + q
            elif trade["side"] == "SELL":
                cash += float(trade["gross_usd"]) - float(trade["cost_usd"])
                holdings[pid] = max(0.0, holdings.get(pid, 0.0) - q)
        marked = 0.0
        for pid, remaining in holdings.items():
            if remaining <= 1e-12:
                continue
            pos = self.conn.execute("SELECT address,entry_price FROM positions WHERE id=?", (pid,)).fetchone()
            if pos is None:
                continue
            snap = self.conn.execute(
                "SELECT price_usd FROM snapshots WHERE address=? AND captured_at<=? ORDER BY captured_at DESC,id DESC LIMIT 1",
                (pos["address"], occurred_at),
            ).fetchone()
            price = float(snap["price_usd"]) if snap is not None else float(pos["entry_price"])
            marked += remaining * price
        return cash + marked

    def probe_exposure_shadow_summary(self) -> dict:
        """Cap-only linear sensitivity using each trade's actual executed notional.

        Unlike v0.3.7/v0.3.8, mixed historical probe sizes are not all treated
        as if they had been 0.75% positions. We derive each trade's net return
        from its immutable BUY gross and corrected realized PnL, then normalize
        that return to the reconstructed entry equity for each candidate cap.
        """
        rows = self.conn.execute(
            """SELECT p.*,b.id AS buy_trade_id,b.occurred_at AS buy_at,b.gross_usd AS buy_gross
            FROM positions p
            JOIN trades b ON b.position_id=p.id AND b.side='BUY' AND b.reason='early_probe_entry'
            WHERE p.status='CLOSED' ORDER BY p.id"""
        ).fetchall()
        non_probe_pnl = float(self.conn.execute(
            """SELECT COALESCE(SUM(p.realized_pnl),0) AS pnl FROM positions p
            WHERE p.status='CLOSED' AND p.id NOT IN (
              SELECT position_id FROM trades WHERE side='BUY' AND reason='early_probe_entry'
            )"""
        ).fetchone()["pnl"] or 0.0)

        observations = []
        actual_cap_buckets: dict[str, int] = {}
        for row in rows:
            gross = float(row["buy_gross"] or 0.0)
            if gross <= 0:
                continue
            equity = self._paper_equity_before_trade(int(row["buy_trade_id"]), str(row["buy_at"]))
            actual_pct = (100 * gross / equity) if equity > 0 else 0.0
            bucket = f"{round(actual_pct, 2):.2f}%"
            actual_cap_buckets[bucket] = actual_cap_buckets.get(bucket, 0) + 1
            observations.append({
                "pnl": float(row["realized_pnl"]),
                "gross": gross,
                "equity": equity,
                "return_on_gross": float(row["realized_pnl"]) / gross,
            })

        policies = []
        for pct in (0.25, 0.40, 0.55, 0.75):
            scaled = [obs["return_on_gross"] * (obs["equity"] * pct / 100) for obs in observations]
            policies.append({
                "probe_cap_pct": pct,
                "closed_probe_positions": len(scaled),
                "simulated_probe_pnl_usd": round(sum(scaled), 4),
                "simulated_total_pnl_usd": round(non_probe_pnl + sum(scaled), 4),
                "worst_probe_position_usd": round(min(scaled), 4) if scaled else None,
                "note": "Cap-only linear sensitivity from actual BUY gross and corrected net PnL; ignores slippage/path/portfolio-cap counterfactuals.",
            })

        tiers = {"security_confirmed": [], "security_unverified_or_partial": []}
        for row in rows:
            snap = self.snapshot_at_or_before(str(row["address"]), str(row["opened_at"]))
            confirmed = False
            if snap is not None:
                sec = snap.security
                confirmed = bool(
                    sec.checked
                    and sec.mint_authority_disabled is True
                    and sec.freeze_authority_disabled is True
                    and sec.honeypot is not True
                    and sec.non_transferable is not True
                    and sec.transfer_hook_present is not True
                    and sec.malicious_creator is not True
                    and (sec.top10_holder_pct is None or sec.top10_holder_pct < 45)
                    and (sec.creator_holder_pct is None or sec.creator_holder_pct <= 5)
                )
            key = "security_confirmed" if confirmed else "security_unverified_or_partial"
            tiers[key].append(float(row["realized_pnl"]))
        tier_summary = {}
        for key, values in tiers.items():
            tier_summary[key] = {
                "closed": len(values),
                "wins": sum(1 for v in values if v > 0),
                "losses": sum(1 for v in values if v < 0),
                "realized_pnl_usd": round(sum(values), 4),
                "worst_position_usd": round(min(values), 4) if values else None,
            }
        return {
            "execution_policy_v0_3_10": {
                "probe_execution_enabled": False,
                "probe_shadow_learning_enabled": True,
                "historical_cap_sensitivity_retained": True,
                "entry_thresholds_changed": False,
            },
            "historical_security_tiers": tier_summary,
            "actual_executed_probe_notional_pct_buckets": actual_cap_buckets,
            "fixed_cap_shadows": policies,
        }

    def accounting_audit(self) -> dict:
        closed = self.conn.execute("SELECT id,realized_pnl FROM positions WHERE status='CLOSED' ORDER BY id").fetchall()
        ledger_total = 0.0
        buy_costs = 0.0
        sell_costs = 0.0
        for pos in closed:
            buy = self.conn.execute(
                "SELECT gross_usd,cost_usd FROM trades WHERE position_id=? AND side='BUY' ORDER BY id LIMIT 1",
                (pos["id"],),
            ).fetchone()
            sells = self.conn.execute(
                "SELECT gross_usd,cost_usd FROM trades WHERE position_id=? AND side='SELL' ORDER BY id",
                (pos["id"],),
            ).fetchall()
            if buy is None:
                continue
            buy_gross = float(buy["gross_usd"])
            buy_costs += float(buy["cost_usd"])
            sell_net = 0.0
            for sell in sells:
                sell_net += float(sell["gross_usd"]) - float(sell["cost_usd"])
                sell_costs += float(sell["cost_usd"])
            ledger_total += sell_net - buy_gross
        position_total = sum(float(row["realized_pnl"]) for row in closed)
        migrated = self.conn.execute(
            "SELECT value FROM schema_meta WHERE key='position_pnl_includes_buy_cost_v0_3_9'"
        ).fetchone()
        return {
            "net_pnl_basis": "BUY gross debited + SELL net credited; includes both execution-cost sides",
            "position_realized_pnl_usd": round(position_total, 4),
            "ledger_reconstructed_pnl_usd": round(ledger_total, 4),
            "reconciliation_difference_usd": round(position_total - ledger_total, 8),
            "closed_trade_buy_costs_usd": round(buy_costs, 4),
            "closed_trade_sell_costs_usd": round(sell_costs, 4),
            "closed_trade_total_costs_usd": round(buy_costs + sell_costs, 4),
            "historical_pnl_rebuilt_from_ledger": migrated is not None,
        }

    def learning_settlement_health(self) -> dict:
        unresolved = int(self.conn.execute(
            """SELECT COUNT(*) AS n FROM learning_predictions p
            LEFT JOIN learning_outcomes o ON o.prediction_id=p.id
            WHERE o.prediction_id IS NULL"""
        ).fetchone()["n"] or 0)
        mature = int(self.conn.execute(
            """SELECT COUNT(*) AS n FROM learning_predictions p
            LEFT JOIN learning_outcomes o ON o.prediction_id=p.id
            WHERE o.prediction_id IS NULL
              AND EXISTS (
                SELECT 1 FROM snapshots s WHERE s.address=p.address
                AND s.captured_at >= strftime(
                    '%Y-%m-%dT%H:%M:%f+00:00', p.predicted_at,
                    '+' || p.horizon_minutes || ' minutes'
                )
              )"""
        ).fetchone()["n"] or 0)
        return {
            "unresolved_predictions": unresolved,
            "mature_resolvable_backlog": mature,
            "queue_starvation_fixed": True,
        }

    def compact_report(self, paper_equity: float, cost_pct: float = 1.0) -> dict:
        closed = [dict(row) for row in self.conn.execute("SELECT * FROM positions WHERE status='CLOSED' ORDER BY id")]
        open_pos = [dict(row) for row in self.conn.execute("SELECT * FROM positions WHERE status='OPEN' ORDER BY id")]
        pnls = [float(row["realized_pnl"]) for row in closed]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        gross_profit = sum(wins)
        gross_loss = -sum(losses)
        lanes: dict[str, list[float]] = {}
        for row in closed:
            lane = self._entry_lane_for_position(int(row["id"]))
            lanes.setdefault(lane, []).append(float(row["realized_pnl"]))
        lane_stats = []
        for lane, values in lanes.items():
            lane_stats.append({
                "lane": lane,
                "closed": len(values),
                "wins": sum(1 for value in values if value > 0),
                "win_rate_pct": round(100 * sum(1 for value in values if value > 0) / len(values), 2),
                "realized_pnl_usd": round(sum(values), 4),
                "profit_factor": None if not any(value < 0 for value in values) else round(
                    sum(value for value in values if value > 0) / -sum(value for value in values if value < 0), 3
                ),
            })
        exit_rows = self.conn.execute(
            "SELECT reason,COUNT(*) AS n FROM trades WHERE side='SELL' GROUP BY reason ORDER BY n DESC,reason"
        ).fetchall()
        largest_losses = sorted(closed, key=lambda row: float(row["realized_pnl"]))[:3]
        account = dict(self.account())
        return {
            "paper_equity": round(paper_equity, 2),
            "initial_equity": round(float(account["initial_equity"]), 2),
            "high_water_equity": round(float(account["high_water_equity"]), 2),
            "kill_reason": account["kill_reason"],
            "trade_stats": {
                "closed_positions": len(closed),
                "open_positions": len(open_pos),
                "wins": len(wins),
                "losses": len(losses),
                "win_rate_pct": round(100 * len(wins) / len(closed), 2) if closed else None,
                "realized_pnl_usd": round(sum(pnls), 4),
                "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss > 0 else None,
                "best_position_usd": round(max(pnls), 4) if pnls else None,
                "worst_position_usd": round(min(pnls), 4) if pnls else None,
            },
            "lane_stats": sorted(lane_stats, key=lambda row: row["lane"]),
            "exit_reasons": [{"reason": row["reason"], "count": row["n"]} for row in exit_rows],
            "largest_losses": [
                {"id": row["id"], "symbol": row["symbol"], "pnl_usd": round(float(row["realized_pnl"]), 4)}
                for row in largest_losses if float(row["realized_pnl"]) < 0
            ],
            "open": [
                {"id": row["id"], "symbol": row["symbol"], "entry_price": row["entry_price"], "opened_at": row["opened_at"]}
                for row in open_pos
            ],
            "rug_shield": self._rug_shield_summary(),
            "reentry_guard": self._reentry_guard_summary(),
            "profit_concentration": self._profit_concentration(pnls),
            "accounting_audit": self.accounting_audit(),
            "forward_validation_v0_3_10": self.forward_validation_v0310(),
            "sample_gate": {
                "closed_positions": len(closed),
                "minimum_before_parameter_change": 50,
                "preferred_before_parameter_change": 100,
                "parameter_change_gate_met": len(closed) >= 50,
            },
            "shadow_exit_policies": self.exit_shadow_summary(cost_pct),
            "probe_exposure_shadows": self.probe_exposure_shadow_summary(),
            "learning_settlement": self.learning_settlement_health(),
            "learning": self.learning_summary(),
            "machine_intelligence": __import__(
                "mykonos_meme_scout.machine_intelligence", fromlist=["machine_shadow_status"]
            ).machine_shadow_status(self),
        }

    def dashboard_data(self) -> dict:
        account = dict(self.account())
        positions = [dict(r) for r in self.conn.execute("SELECT * FROM positions ORDER BY id DESC LIMIT 50")]
        decisions = [dict(r) for r in self.conn.execute("SELECT * FROM decisions ORDER BY id DESC LIMIT 100")]
        trades = [dict(r) for r in self.conn.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 100")]
        outcome_count = self.conn.execute("SELECT COUNT(*) AS n FROM outcomes").fetchone()["n"]
        return {"account": account, "positions": positions, "decisions": decisions,
                "trades": trades, "outcome_count": outcome_count,
                "learning": self.learning_summary()}

    def tracked_addresses(self, limit: int = 100, retention_minutes: int = 35) -> list[str]:
        addresses: list[str] = []
        for row in self.conn.execute("SELECT address FROM positions WHERE status='OPEN' ORDER BY id DESC"):
            addresses.append(str(row["address"]))
        # Hot-path tracking only needs the newest unresolved launches. The old
        # DISTINCT query scanned millions of historical predictions every cycle
        # before Python truncated the result to 'limit'.
        unresolved = self.conn.execute(
            """SELECT p.address FROM learning_predictions p
            LEFT JOIN learning_outcomes o ON o.prediction_id=p.id
            WHERE o.prediction_id IS NULL ORDER BY p.id DESC LIMIT ?""",
            (max(int(limit) * 10, 200),),
        ).fetchall()
        addresses.extend(str(row["address"]) for row in unresolved)
        unique = list(dict.fromkeys(addresses))
        if len(unique) >= limit:
            return unique[:limit]
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=retention_minutes)).isoformat()
        decision_table='_decision_records' if self._dictionary_storage is not None else 'decisions'
        recent = self.conn.execute(
            "SELECT address,MAX(id) AS last_id FROM "+decision_table+" INDEXED BY idx_decisions_captured_address_id WHERE captured_at>=? GROUP BY address ORDER BY last_id DESC",
            (cutoff,),
        ).fetchall()
        addresses.extend(str(row["address"]) for row in recent)
        return list(dict.fromkeys(addresses))[:limit]

    def label_mature_outcomes(self, horizons: tuple[int, ...] = (1, 3, 5, 10, 15),
                              batch_limit: int = 500) -> int:
        inserted = 0
        decisions = self.conn.execute(
            """SELECT d.id,d.captured_at,d.address FROM decisions d
            LEFT JOIN outcomes o ON o.decision_id=d.id
            WHERE d.decision IN ('PASS','PROBE','PROBE_SHADOW')
            GROUP BY d.id HAVING COUNT(o.horizon_minutes) < ?
            ORDER BY d.id LIMIT ?""",
            (len(horizons), max(1, int(batch_limit))),
        ).fetchall()
        for decision in decisions:
            start = datetime.fromisoformat(str(decision["captured_at"]).replace("Z", "+00:00"))
            entry = self.conn.execute(
                "SELECT price_usd FROM snapshots WHERE address=? AND captured_at>=? ORDER BY captured_at LIMIT 1",
                (decision["address"], decision["captured_at"]),
            ).fetchone()
            if not entry or entry["price_usd"] <= 0:
                continue
            for horizon in horizons:
                target_iso = (start + timedelta(minutes=horizon)).isoformat()
                measured = self.conn.execute(
                    "SELECT captured_at,price_usd FROM snapshots WHERE address=? AND captured_at>=? ORDER BY captured_at LIMIT 1",
                    (decision["address"], target_iso),
                ).fetchone()
                if not measured or measured["price_usd"] <= 0:
                    continue
                ret = (float(measured["price_usd"]) / float(entry["price_usd"]) - 1) * 100
                cur = self.conn.execute(
                    "INSERT OR IGNORE INTO outcomes VALUES(?,?,?,?,?,?)",
                    (decision["id"], horizon, measured["captured_at"], entry["price_usd"], measured["price_usd"], ret),
                )
                inserted += cur.rowcount
                if inserted and inserted % 25 == 0:
                    self.conn.commit()
        self.conn.commit()
        return inserted

    def register_model(self, family: str, version: str, config: dict) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO model_registry(model_family,model_version,created_at,status,config_json) VALUES(?,?,?,'SHADOW',?)",
            (family, version, utc_now(), json.dumps(config, sort_keys=True)),
        )
        self.conn.commit()

    def latest_learning_prediction_at(self, address: str, version: str) -> str | None:
        row = self.conn.execute(
            "SELECT MAX(predicted_at) AS t FROM learning_predictions WHERE address=? AND model_version=?",
            (address, version),
        ).fetchone()
        return str(row["t"]) if row and row["t"] else None

    def record_learning_prediction(self, snapshot: TokenSnapshot, prediction: object,
                                   horizon_minutes: int, version: str, commit: bool = True) -> None:
        values=(getattr(prediction, "family"), version, snapshot.address, snapshot.symbol,
             snapshot.captured_at, horizon_minutes, snapshot.price_usd, snapshot.liquidity_usd,
             getattr(prediction, "p5"), getattr(prediction, "p10"), getattr(prediction, "p25"),
             getattr(prediction, "p_rug"), getattr(prediction, "p_stagnation"),
             getattr(prediction, "uncertainty"), getattr(prediction, "expected_utility"),
             json.dumps(getattr(prediction, "reasons")))
        if self._dictionary_storage is not None:
            self._dictionary_storage.prediction((None,*values))
        else:
            self.conn.execute(
                """INSERT OR IGNORE INTO learning_predictions(
                model_family,model_version,address,symbol,predicted_at,horizon_minutes,
                entry_price,entry_liquidity,p5,p10,p25,p_rug,p_stagnation,
                uncertainty,expected_utility,reasons_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",values)
        if commit:
            self.conn.commit()

    def label_and_reward_learning_predictions(self, batch_limit: int = 500) -> int:
        from .learning import cookie_components

        # v0.3.6 could starve forever on the oldest unresolved rows when those
        # addresses never received a terminal snapshot. Select only predictions
        # that are actually mature *and* have an observable terminal snapshot.
        unresolved = self.conn.execute(
            """SELECT p.* FROM learning_predictions p
            LEFT JOIN learning_outcomes o ON o.prediction_id=p.id
            WHERE o.prediction_id IS NULL
              AND EXISTS (
                SELECT 1 FROM snapshots s
                WHERE s.address=p.address
                  AND s.captured_at >= strftime(
                      '%Y-%m-%dT%H:%M:%f+00:00', p.predicted_at,
                      '+' || p.horizon_minutes || ' minutes'
                  )
              )
            ORDER BY p.id DESC LIMIT ?""",
            (max(1, int(batch_limit)),),
        ).fetchall()
        settled = 0
        for prediction in unresolved:
            start = datetime.fromisoformat(str(prediction["predicted_at"]).replace("Z", "+00:00"))
            cutoff = (start + timedelta(minutes=int(prediction["horizon_minutes"]))).isoformat()
            terminal = self.conn.execute(
                "SELECT captured_at FROM snapshots WHERE address=? AND captured_at>=? ORDER BY captured_at LIMIT 1",
                (prediction["address"], cutoff),
            ).fetchone()
            if terminal is None:
                continue
            measured_cutoff = terminal["captured_at"]
            rows = self.conn.execute(
                """SELECT captured_at,price_usd,payload_json FROM snapshots
                WHERE address=? AND captured_at>=? AND captured_at<=? ORDER BY captured_at""",
                (prediction["address"], prediction["predicted_at"], measured_cutoff),
            ).fetchall()
            if not rows or float(prediction["entry_price"]) <= 0:
                continue
            entry = float(prediction["entry_price"])
            base_liquidity = max(float(prediction["entry_liquidity"]), 1.0)
            hit5 = hit10 = hit25 = rug = False
            returns: list[float] = []
            final_return = 0.0
            measured_at = rows[0]["captured_at"]
            for row in rows:
                ret = (float(row["price_usd"]) / entry - 1) * 100
                returns.append(ret)
                final_return = ret
                measured_at = row["captured_at"]
                payload = json.loads(row["payload_json"])
                liquidity = float(payload.get("liquidity_usd") or 0)
                security = payload.get("security") or {}
                rug_now = (
                    liquidity <= base_liquidity * .20
                    or security.get("honeypot") is True
                    or security.get("non_transferable") is True
                    or ret <= -40
                )
                if rug_now:
                    rug = True
                    break
                hit5 = hit5 or ret >= 5
                hit10 = hit10 or ret >= 10
                hit25 = hit25 or ret >= 25
            max_return = max(returns)
            min_return = min(returns)
            stagnant = not rug and max_return < 3 and min_return > -10
            cur = self.conn.execute(
                """INSERT OR IGNORE INTO learning_outcomes(
                prediction_id,measured_at,hit_5,hit_10,hit_25,rug,stagnant,
                max_return_pct,min_return_pct,return_pct) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (prediction["id"], measured_at, int(hit5), int(hit10), int(hit25),
                 int(rug), int(stagnant), max_return, min_return, final_return),
            )
            if cur.rowcount == 0:
                continue
            outcome = {
                "hit_10": int(hit10), "rug": int(rug), "return_pct": final_return,
                "min_return_pct": min_return,
            }
            components = cookie_components(dict(prediction), outcome)
            self.conn.execute(
                """INSERT INTO cookie_ledger(prediction_id,model_family,issued_at,total_reward,components_json)
                VALUES(?,?,?,?,?)""",
                (prediction["id"], prediction["model_family"], utc_now(), components["total"],
                 json.dumps(components, sort_keys=True)),
            )
            settled += 1
            if settled % 25 == 0:
                self.conn.commit()
        self.conn.commit()
        return settled

    def learning_reward_rows(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM cookie_ledger ORDER BY id").fetchall()

    def learning_summary(self) -> dict:
        registry_rows = int(self.conn.execute("SELECT COUNT(*) AS n FROM model_registry").fetchone()["n"])
        model_versions = int(self.conn.execute(
            "SELECT COUNT(DISTINCT model_version) AS n FROM model_registry"
        ).fetchone()["n"])
        active_models = int(self.conn.execute(
            "SELECT COUNT(DISTINCT model_family) AS n FROM model_registry WHERE status='SHADOW'"
        ).fetchone()["n"])
        active_versions = [dict(r) for r in self.conn.execute(
            """SELECT model_version,COUNT(DISTINCT model_family) AS families
            FROM model_registry WHERE status='SHADOW'
            GROUP BY model_version ORDER BY model_version"""
        )]
        active_row = self.conn.execute(
            "SELECT model_version FROM model_registry ORDER BY created_at DESC,rowid DESC LIMIT 1"
        ).fetchone()
        active_version = str(active_row["model_version"]) if active_row else None
        predictions = self.conn.execute("SELECT COUNT(*) AS n FROM learning_predictions").fetchone()["n"]
        resolved = self.conn.execute("SELECT COUNT(*) AS n FROM learning_outcomes").fetchone()["n"]
        cookies = self.conn.execute("SELECT COUNT(*) AS n FROM cookie_ledger").fetchone()["n"]
        by_horizon = [dict(row) for row in self.conn.execute(
            """SELECT horizon_minutes,COUNT(*) AS predictions,
            SUM(CASE WHEN id IN (SELECT prediction_id FROM learning_outcomes) THEN 1 ELSE 0 END) AS resolved
            FROM learning_predictions GROUP BY horizon_minutes ORDER BY horizon_minutes"""
        )]
        leaderboard = [dict(row) for row in self.conn.execute(
            """SELECT model_family,COUNT(*) AS resolved,ROUND(AVG(total_reward),4) AS mean_reward
            FROM cookie_ledger GROUP BY model_family ORDER BY mean_reward DESC"""
        )]
        return {
            "registered_models": active_models,
            "active_models": active_models,
            "active_model_version": active_version,
            "active_model_versions": active_versions,
            "registry_rows_historical": registry_rows,
            "model_versions_historical": model_versions,
            "predictions": predictions, "resolved": resolved, "cookies": cookies,
            "horizons": by_horizon, "leaderboard": leaderboard,
        }
