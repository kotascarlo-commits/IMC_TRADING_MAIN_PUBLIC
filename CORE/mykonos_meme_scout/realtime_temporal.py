from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from .models import TokenSnapshot
from .learning import LearningFamily
from .temporal_intelligence import FEATURE_NAMES, OUTPUT_NAMES, TemporalShadowBundle

REAL_TEMPORAL_SCHEMA_VERSION = 5
REAL_TEMPORAL_LINEAGE = "0.8.11-horizon-aware-challenger-temporal-shadow"
REAL_MARKET_DOMAIN = "REAL_MARKET_PAPER_SHADOW"
OUTCOME_HORIZONS_SECONDS = (10, 30, 60, 180, 300, 600, 900)
EXECUTION_AUTHORITY_PCT = 0


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class RealTemporalObservation:
    session_id: str
    address: str
    pair_address: str
    symbol: str
    captured_at: str
    monotonic_second: int
    price_usd: float
    liquidity_usd: float
    market_cap_usd: float
    volume_5m_usd: float
    buys_5m: int
    sells_5m: int
    price_change_5m_pct: float
    age_seconds: float
    discovery_source: str
    market_data_stale: bool
    feed_source: str
    feed_complete: bool
    feed_latency_ms: float
    security_checked: bool
    hard_risk_seen: bool
    social_data_available: bool = False
    social_mentions_1s: float | None = None
    social_sentiment: float | None = None
    smart_wallet_data_available: bool = False
    smart_wallet_flow_1s_usd: float | None = None

    @classmethod
    def from_snapshot(
        cls, session_id: str, snapshot: TokenSnapshot, monotonic_second: int, *,
        feed_source: str = "unknown", feed_complete: bool = True,
        feed_latency_ms: float = 0.0,
    ) -> "RealTemporalObservation":
        sec = snapshot.security
        hard_risk_seen = bool(
            sec.checked and (
                sec.honeypot is True
                or sec.malicious_creator is True
                or sec.non_transferable is True
                or sec.transfer_hook_present is True
                or sec.mint_authority_disabled is False
                or sec.freeze_authority_disabled is False
            )
        )
        return cls(
            session_id=session_id,
            address=snapshot.address,
            pair_address=snapshot.pair_address,
            symbol=snapshot.symbol,
            captured_at=snapshot.captured_at,
            monotonic_second=monotonic_second,
            price_usd=float(snapshot.price_usd),
            liquidity_usd=float(snapshot.liquidity_usd),
            market_cap_usd=float(snapshot.market_cap_usd),
            volume_5m_usd=float(snapshot.volume_5m_usd),
            buys_5m=int(snapshot.buys_5m),
            sells_5m=int(snapshot.sells_5m),
            price_change_5m_pct=float(snapshot.price_change_5m_pct),
            age_seconds=float(snapshot.age_minutes) * 60.0,
            discovery_source=snapshot.discovery_source,
            market_data_stale=bool(snapshot.market_data_stale),
            feed_source=feed_source,
            feed_complete=bool(feed_complete),
            feed_latency_ms=float(feed_latency_ms),
            security_checked=bool(sec.checked),
            hard_risk_seen=hard_risk_seen,
        )


class RealTemporalStore:
    """Append-only real-market shadow store. It has no execution interface."""

    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS real_temporal_metadata(
          id INTEGER PRIMARY KEY CHECK(id=1), schema_version INTEGER NOT NULL,
          lineage TEXT NOT NULL, data_domain TEXT NOT NULL, created_at TEXT NOT NULL,
          execution_authority_pct INTEGER NOT NULL CHECK(execution_authority_pct=0),
          runtime_decision_input INTEGER NOT NULL CHECK(runtime_decision_input=0),
          eligible_for_real_champion INTEGER NOT NULL CHECK(eligible_for_real_champion=0)
        );
        CREATE TABLE IF NOT EXISTS real_temporal_sessions(
          session_id TEXT PRIMARY KEY, address TEXT NOT NULL, pair_address TEXT NOT NULL,
          symbol TEXT NOT NULL, pair_created_at_ms INTEGER NOT NULL,
          discovered_at TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT,
          stop_reason TEXT, discovery_source TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS real_temporal_ticks(
          id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
          address TEXT NOT NULL, pair_address TEXT NOT NULL, symbol TEXT NOT NULL,
          captured_at TEXT NOT NULL, monotonic_second INTEGER NOT NULL,
          price_usd REAL NOT NULL, liquidity_usd REAL NOT NULL, market_cap_usd REAL NOT NULL,
          volume_5m_usd REAL NOT NULL, buys_5m INTEGER NOT NULL, sells_5m INTEGER NOT NULL,
          price_change_5m_pct REAL NOT NULL, age_seconds REAL NOT NULL,
          discovery_source TEXT NOT NULL, market_data_stale INTEGER NOT NULL,
          feed_source TEXT NOT NULL, feed_complete INTEGER NOT NULL, feed_latency_ms REAL NOT NULL,
          security_checked INTEGER NOT NULL, hard_risk_seen INTEGER NOT NULL,
          social_data_available INTEGER NOT NULL, social_mentions_1s REAL, social_sentiment REAL,
          smart_wallet_data_available INTEGER NOT NULL, smart_wallet_flow_1s_usd REAL,
          raw_checksum TEXT NOT NULL,
          UNIQUE(session_id, monotonic_second)
        );
        CREATE INDEX IF NOT EXISTS idx_real_temporal_ticks_address_time
          ON real_temporal_ticks(address,captured_at);
        CREATE TABLE IF NOT EXISTS real_temporal_predictions(
          prediction_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, address TEXT NOT NULL,
          observed_at TEXT NOT NULL, observed_second INTEGER NOT NULL, window_seconds INTEGER NOT NULL,
          model_lineage TEXT NOT NULL, model_sha256 TEXT NOT NULL, feature_checksum TEXT NOT NULL,
          p5 REAL NOT NULL, p10 REAL NOT NULL, p25 REAL NOT NULL, p_rug REAL NOT NULL,
          p_stagnation REAL NOT NULL, inference_at TEXT NOT NULL,
          execution_authority_pct INTEGER NOT NULL CHECK(execution_authority_pct=0),
          UNIQUE(session_id,observed_second,window_seconds,model_sha256)
        );
        CREATE TABLE IF NOT EXISTS real_temporal_outcomes(
          prediction_id TEXT NOT NULL, horizon_seconds INTEGER NOT NULL,
          settled_at TEXT NOT NULL, start_price_usd REAL NOT NULL, end_price_usd REAL NOT NULL,
          return_pct REAL NOT NULL, mfe_pct REAL NOT NULL, mae_pct REAL NOT NULL,
          hit_5 INTEGER NOT NULL, hit_10 INTEGER NOT NULL, hit_25 INTEGER NOT NULL,
          rug_or_liquidity_collapse INTEGER NOT NULL, stagnant INTEGER NOT NULL,
          PRIMARY KEY(prediction_id,horizon_seconds)
        );
        CREATE TABLE IF NOT EXISTS real_shadow_predictions(
          prediction_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, address TEXT NOT NULL,
          observed_at TEXT NOT NULL, observed_second INTEGER NOT NULL, horizon_seconds INTEGER NOT NULL,
          model_kind TEXT NOT NULL CHECK(model_kind IN ('temporal','snapshot','ensemble','temporal_horizon_challenger','horizon_adaptive_ensemble')),
          model_lineage TEXT NOT NULL, model_sha256 TEXT NOT NULL, feature_checksum TEXT NOT NULL,
          p5 REAL NOT NULL, p10 REAL NOT NULL, p25 REAL NOT NULL, p_rug REAL NOT NULL,
          p_stagnation REAL NOT NULL, expected_utility REAL NOT NULL, inference_at TEXT NOT NULL,
          execution_authority_pct INTEGER NOT NULL CHECK(execution_authority_pct=0),
          immutable INTEGER NOT NULL DEFAULT 1 CHECK(immutable=1),
          UNIQUE(session_id,observed_second,horizon_seconds,model_kind,model_sha256)
        );
        CREATE TABLE IF NOT EXISTS real_shadow_outcomes(
          prediction_id TEXT PRIMARY KEY, settled_at TEXT NOT NULL, start_price_usd REAL NOT NULL,
          end_price_usd REAL NOT NULL, return_pct REAL NOT NULL, mfe_pct REAL NOT NULL, mae_pct REAL NOT NULL,
          hit_5 INTEGER NOT NULL, hit_10 INTEGER NOT NULL, hit_25 INTEGER NOT NULL,
          rug_or_liquidity_collapse INTEGER NOT NULL, stagnant INTEGER NOT NULL,
          target_second INTEGER NOT NULL, settlement_complete INTEGER NOT NULL CHECK(settlement_complete IN (0,1)),
          actual_end_second INTEGER NOT NULL, coverage_ratio REAL NOT NULL,
          max_gap_seconds INTEGER NOT NULL, endpoint_lag_seconds INTEGER NOT NULL,
          settlement_policy TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS real_shadow_settlement_rejections(
          prediction_id TEXT PRIMARY KEY, evaluated_at TEXT NOT NULL, reason TEXT NOT NULL,
          target_second INTEGER NOT NULL, actual_end_second INTEGER NOT NULL,
          coverage_ratio REAL NOT NULL, max_gap_seconds INTEGER NOT NULL,
          endpoint_lag_seconds INTEGER NOT NULL, settlement_policy TEXT NOT NULL,
          immutable INTEGER NOT NULL DEFAULT 1 CHECK(immutable=1)
        );
        CREATE INDEX IF NOT EXISTS idx_real_temporal_ticks_session_second
          ON real_temporal_ticks(session_id,monotonic_second);
        CREATE INDEX IF NOT EXISTS idx_real_shadow_predictions_session_target
          ON real_shadow_predictions(session_id,observed_second,horizon_seconds);
        CREATE TRIGGER IF NOT EXISTS trg_shadow_predictions_no_update
          BEFORE UPDATE ON real_shadow_predictions BEGIN SELECT RAISE(ABORT,'shadow predictions are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS trg_shadow_predictions_no_delete
          BEFORE DELETE ON real_shadow_predictions BEGIN SELECT RAISE(ABORT,'shadow predictions are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS trg_shadow_rejections_no_update
          BEFORE UPDATE ON real_shadow_settlement_rejections BEGIN SELECT RAISE(ABORT,'shadow settlement rejections are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS trg_shadow_rejections_no_delete
          BEFORE DELETE ON real_shadow_settlement_rejections BEGIN SELECT RAISE(ABORT,'shadow settlement rejections are immutable'); END;
        """)
        # v0.8.11 migration: older v0.8.x databases restricted model_kind to
        # temporal/snapshot/ensemble. Rebuild only this append-only prediction
        # table so the new shadow challenger can coexist without changing old rows.
        shadow_sql = self.conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='real_shadow_predictions'"
        ).fetchone()[0]
        if "temporal_horizon_challenger" not in str(shadow_sql):
            self.conn.executescript("""
            DROP TRIGGER IF EXISTS trg_shadow_predictions_no_update;
            DROP TRIGGER IF EXISTS trg_shadow_predictions_no_delete;
            DROP INDEX IF EXISTS idx_real_shadow_predictions_session_target;
            ALTER TABLE real_shadow_predictions RENAME TO real_shadow_predictions_v0810;
            CREATE TABLE real_shadow_predictions(
              prediction_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, address TEXT NOT NULL,
              observed_at TEXT NOT NULL, observed_second INTEGER NOT NULL, horizon_seconds INTEGER NOT NULL,
              model_kind TEXT NOT NULL CHECK(model_kind IN ('temporal','snapshot','ensemble','temporal_horizon_challenger','horizon_adaptive_ensemble')),
              model_lineage TEXT NOT NULL, model_sha256 TEXT NOT NULL, feature_checksum TEXT NOT NULL,
              p5 REAL NOT NULL, p10 REAL NOT NULL, p25 REAL NOT NULL, p_rug REAL NOT NULL,
              p_stagnation REAL NOT NULL, expected_utility REAL NOT NULL, inference_at TEXT NOT NULL,
              execution_authority_pct INTEGER NOT NULL CHECK(execution_authority_pct=0),
              immutable INTEGER NOT NULL DEFAULT 1 CHECK(immutable=1),
              UNIQUE(session_id,observed_second,horizon_seconds,model_kind,model_sha256)
            );
            INSERT INTO real_shadow_predictions SELECT * FROM real_shadow_predictions_v0810;
            DROP TABLE real_shadow_predictions_v0810;
            CREATE INDEX idx_real_shadow_predictions_session_target
              ON real_shadow_predictions(session_id,observed_second,horizon_seconds);
            CREATE TRIGGER trg_shadow_predictions_no_update
              BEFORE UPDATE ON real_shadow_predictions BEGIN SELECT RAISE(ABORT,'shadow predictions are immutable'); END;
            CREATE TRIGGER trg_shadow_predictions_no_delete
              BEFORE DELETE ON real_shadow_predictions BEGIN SELECT RAISE(ABORT,'shadow predictions are immutable'); END;
            """)
        # v0.8.14 migration: add the horizon-gated adaptive ensemble kind while
        # preserving all immutable historical predictions and outcomes.
        shadow_sql = self.conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='real_shadow_predictions'"
        ).fetchone()[0]
        if "horizon_adaptive_ensemble" not in str(shadow_sql):
            self.conn.executescript("""
            DROP TRIGGER IF EXISTS trg_shadow_predictions_no_update;
            DROP TRIGGER IF EXISTS trg_shadow_predictions_no_delete;
            DROP INDEX IF EXISTS idx_real_shadow_predictions_session_target;
            ALTER TABLE real_shadow_predictions RENAME TO real_shadow_predictions_v0813;
            CREATE TABLE real_shadow_predictions(
              prediction_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, address TEXT NOT NULL,
              observed_at TEXT NOT NULL, observed_second INTEGER NOT NULL, horizon_seconds INTEGER NOT NULL,
              model_kind TEXT NOT NULL CHECK(model_kind IN ('temporal','snapshot','ensemble','temporal_horizon_challenger','horizon_adaptive_ensemble')),
              model_lineage TEXT NOT NULL, model_sha256 TEXT NOT NULL, feature_checksum TEXT NOT NULL,
              p5 REAL NOT NULL, p10 REAL NOT NULL, p25 REAL NOT NULL, p_rug REAL NOT NULL,
              p_stagnation REAL NOT NULL, expected_utility REAL NOT NULL, inference_at TEXT NOT NULL,
              execution_authority_pct INTEGER NOT NULL CHECK(execution_authority_pct=0),
              immutable INTEGER NOT NULL DEFAULT 1 CHECK(immutable=1),
              UNIQUE(session_id,observed_second,horizon_seconds,model_kind,model_sha256)
            );
            INSERT INTO real_shadow_predictions SELECT * FROM real_shadow_predictions_v0813;
            DROP TABLE real_shadow_predictions_v0813;
            CREATE INDEX idx_real_shadow_predictions_session_target
              ON real_shadow_predictions(session_id,observed_second,horizon_seconds);
            CREATE TRIGGER trg_shadow_predictions_no_update
              BEFORE UPDATE ON real_shadow_predictions BEGIN SELECT RAISE(ABORT,'shadow predictions are immutable'); END;
            CREATE TRIGGER trg_shadow_predictions_no_delete
              BEFORE DELETE ON real_shadow_predictions BEGIN SELECT RAISE(ABORT,'shadow predictions are immutable'); END;
            """)
        self.conn.execute(
            "INSERT OR IGNORE INTO real_temporal_metadata VALUES(1,?,?,?,?,0,0,0)",
            (REAL_TEMPORAL_SCHEMA_VERSION, REAL_TEMPORAL_LINEAGE, REAL_MARKET_DOMAIN, utc_now()),
        )
        self.conn.execute(
            "UPDATE real_temporal_metadata SET schema_version=?,lineage=? WHERE id=1 AND execution_authority_pct=0 AND runtime_decision_input=0 AND eligible_for_real_champion=0",
            (REAL_TEMPORAL_SCHEMA_VERSION, REAL_TEMPORAL_LINEAGE),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def start_session(self, snapshot: TokenSnapshot) -> str:
        key = f"{snapshot.address}|{snapshot.pair_address}|{snapshot.pair_created_at_ms}|{snapshot.captured_at}"
        session_id = "real-" + _sha(key)[:24]
        self.conn.execute(
            """INSERT OR IGNORE INTO real_temporal_sessions
            (session_id,address,pair_address,symbol,pair_created_at_ms,discovered_at,started_at,discovery_source)
            VALUES(?,?,?,?,?,?,?,?)""",
            (session_id, snapshot.address, snapshot.pair_address, snapshot.symbol,
             int(snapshot.pair_created_at_ms), snapshot.captured_at, utc_now(), snapshot.discovery_source),
        )
        self.conn.commit()
        return session_id

    def append_tick(self, obs: RealTemporalObservation) -> bool:
        payload = json.dumps(asdict(obs), sort_keys=True, separators=(",", ":"))
        cur = self.conn.execute(
            """INSERT OR IGNORE INTO real_temporal_ticks(
            session_id,address,pair_address,symbol,captured_at,monotonic_second,price_usd,liquidity_usd,
            market_cap_usd,volume_5m_usd,buys_5m,sells_5m,price_change_5m_pct,age_seconds,
            discovery_source,market_data_stale,feed_source,feed_complete,feed_latency_ms,
            security_checked,hard_risk_seen,social_data_available,social_mentions_1s,social_sentiment,
            smart_wallet_data_available,smart_wallet_flow_1s_usd,raw_checksum)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (obs.session_id, obs.address, obs.pair_address, obs.symbol, obs.captured_at,
             obs.monotonic_second, obs.price_usd, obs.liquidity_usd, obs.market_cap_usd,
             obs.volume_5m_usd, obs.buys_5m, obs.sells_5m, obs.price_change_5m_pct, obs.age_seconds,
             obs.discovery_source, int(obs.market_data_stale), obs.feed_source, int(obs.feed_complete),
             obs.feed_latency_ms, int(obs.security_checked), int(obs.hard_risk_seen),
             int(obs.social_data_available), obs.social_mentions_1s, obs.social_sentiment,
             int(obs.smart_wallet_data_available), obs.smart_wallet_flow_1s_usd, _sha(payload)),
        )
        self.conn.commit()
        return cur.rowcount == 1

    def finish_session(self, session_id: str, reason: str) -> None:
        self.conn.execute(
            "UPDATE real_temporal_sessions SET ended_at=?,stop_reason=? WHERE session_id=? AND ended_at IS NULL",
            (utc_now(), reason, session_id),
        )
        self.conn.commit()

    def status(self) -> dict:
        q = self.conn.execute
        sessions = int(q("SELECT COUNT(*) n FROM real_temporal_sessions").fetchone()["n"])
        ticks = int(q("SELECT COUNT(*) n FROM real_temporal_ticks").fetchone()["n"])
        predictions = int(q("SELECT COUNT(*) n FROM real_temporal_predictions").fetchone()["n"])
        outcomes = int(q("SELECT COUNT(*) n FROM real_temporal_outcomes").fetchone()["n"])
        shadow_predictions = int(q("SELECT COUNT(*) n FROM real_shadow_predictions").fetchone()["n"])
        shadow_outcomes = int(q("SELECT COUNT(*) n FROM real_shadow_outcomes").fetchone()["n"])
        shadow_rejections = int(q("SELECT COUNT(*) n FROM real_shadow_settlement_rejections").fetchone()["n"])
        distinct_tokens = int(q("SELECT COUNT(DISTINCT address) n FROM real_temporal_sessions").fetchone()["n"])
        return {
            "version": "0.8.11", "data_domain": REAL_MARKET_DOMAIN,
            "sessions": sessions, "distinct_tokens": distinct_tokens, "ticks": ticks,
            "predictions": predictions, "settled_outcomes": outcomes,
            "shadow_predictions": shadow_predictions, "shadow_settled_outcomes": shadow_outcomes,
            "shadow_settlement_rejections": shadow_rejections,
            "execution_authority_pct": 0, "runtime_decision_input": False,
            "eligible_for_real_champion": False,
        }


class RealTemporalCollector:
    """Cadence controller around an injected market fetcher; never opens/closes positions."""

    def __init__(self, store: RealTemporalStore, fetcher: Callable[[Sequence[tuple[str, str]]], tuple[list[TokenSnapshot], dict]], evaluator: "RealShadowEvaluator | None" = None):
        self.store = store
        self.fetcher = fetcher
        self.evaluator = evaluator

    def collect(self, seeds: Iterable[TokenSnapshot], *, duration_seconds: int = 60, cadence_seconds: float = 1.0,
                inference_window_seconds: int = 60, prediction_stride_seconds: int = 30) -> dict:
        if duration_seconds < 1 or cadence_seconds <= 0:
            raise ValueError("duration_seconds and cadence_seconds must be positive")
        sessions = {s.address: (self.store.start_session(s), s.pair_address) for s in seeds}
        targets = [(address, pair) for address, (_, pair) in sessions.items()]
        started = time.monotonic()
        next_tick = started
        ticks_written = 0
        misses = 0
        shadow_predictions = 0
        shadow_settled = 0
        second = 0
        while time.monotonic() - started < duration_seconds:
            now = time.monotonic()
            if now < next_tick:
                time.sleep(next_tick - now)
            fetch_started = time.monotonic()
            items, health = self.fetcher(targets)
            latency_ms = (time.monotonic() - fetch_started) * 1000.0
            by_address = {item.address: item for item in items}
            for address, (session_id, _) in sessions.items():
                snap = by_address.get(address)
                if snap is None:
                    misses += 1
                    continue
                obs = RealTemporalObservation.from_snapshot(
                    session_id, snap, second,
                    feed_source=str(health.get("source", "unknown")),
                    feed_complete=bool(health.get("complete", False)),
                    feed_latency_ms=latency_ms,
                )
                ticks_written += int(self.store.append_tick(obs))
            if self.evaluator is not None and second + 1 >= inference_window_seconds and second % max(1, prediction_stride_seconds) == 0:
                for session_id, _ in sessions.values():
                    counts = self.evaluator.infer_session(session_id, window_seconds=inference_window_seconds)
                    shadow_predictions += counts.get("temporal",0) + counts.get("snapshot",0) + counts.get("ensemble",0) + counts.get("challenger",0)
                shadow_settled += self.evaluator.settle().get("settled",0)
            second += 1
            next_tick = started + second * cadence_seconds
        for session_id, _ in sessions.values():
            self.store.finish_session(session_id, "duration_complete")
        if self.evaluator is not None:
            shadow_settled += self.evaluator.settle().get("settled",0)
        return {"sessions": len(sessions), "ticks_written": ticks_written, "misses": misses,
                "shadow_predictions_written": shadow_predictions, "shadow_outcomes_settled": shadow_settled,
                "target_cadence_seconds": cadence_seconds, "execution_authority_pct": 0}



class ContinuousRealTemporalRunner:
    """Rolling FIRST15 discovery + one-second shadow observation loop.

    This runner deliberately has no PaperEngine reference and no order/execution
    interface. New launch candidates may be admitted while existing sessions
    continue to accumulate later outcome paths.
    """

    def __init__(
        self,
        store: RealTemporalStore,
        discoverer: Callable[[int], Sequence[TokenSnapshot]],
        fetcher: Callable[[Sequence[tuple[str, str]]], tuple[list[TokenSnapshot], dict]],
        *,
        evaluator: "RealShadowEvaluator | None" = None,
    ):
        self.store = store
        self.discoverer = discoverer
        self.fetcher = fetcher
        self.evaluator = evaluator

    def run(
        self,
        *,
        maximum_candidates: int = 30,
        maximum_active_sessions: int = 120,
        runtime_seconds: int = 0,
        cadence_seconds: float = 1.0,
        discovery_interval_seconds: float = 10.0,
        maximum_entry_age_minutes: float = 15.0,
        maximum_session_seconds: int = 1800,
        inference_window_seconds: int = 60,
        prediction_stride_seconds: int = 30,
        status_interval_seconds: float = 60.0,
        status_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        if maximum_candidates < 1:
            raise ValueError("maximum_candidates must be positive")
        if maximum_active_sessions < 1:
            raise ValueError("maximum_active_sessions must be positive")
        if runtime_seconds < 0:
            raise ValueError("runtime_seconds cannot be negative")
        if cadence_seconds <= 0 or discovery_interval_seconds <= 0:
            raise ValueError("cadence and discovery interval must be positive")
        if maximum_session_seconds < inference_window_seconds + min(OUTCOME_HORIZONS_SECONDS):
            raise ValueError("maximum_session_seconds is too short for temporal inference/outcomes")

        active: dict[str, dict[str, Any]] = {}
        admitted_pairs: set[tuple[str, str]] = set()
        capacity_evictions = 0
        started = time.monotonic()
        next_tick = started
        next_discovery = started
        next_status = started + status_interval_seconds
        ticks_written = misses = discoveries = sessions_started = sessions_finished = 0
        shadow_predictions = shadow_settled = inference_skips = 0
        fetch_cycles = incomplete_fetch_cycles = dex_requests = gecko_requests = 0
        stop_reason = "runtime_complete"
        discovery_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mykonos-rolling-discovery")
        discovery_future = None
        settlement_interval_seconds = 10.0
        next_settlement = started + settlement_interval_seconds

        def expired() -> bool:
            return runtime_seconds > 0 and time.monotonic() - started >= runtime_seconds

        try:
            while not expired():
                now = time.monotonic()
                if now < next_tick:
                    time.sleep(next_tick - now)
                now = time.monotonic()

                # v0.8.10: discovery network I/O must not occupy the one-second
                # watch path. The very first discovery may run synchronously
                # because there are no tracked sessions to miss yet. All later
                # scans run in one background worker. A 2 ms result poll gives
                # completed workers deterministic handoff without meaningfully
                # consuming the cadence budget.
                if now >= next_discovery and discovery_future is None:
                    if sessions_started == 0 and not active:
                        first_future: Future = Future()
                        try:
                            first_future.set_result(self.discoverer(maximum_candidates))
                        except Exception as exc:
                            first_future.set_exception(exc)
                        discovery_future = first_future
                    else:
                        discovery_future = discovery_executor.submit(self.discoverer, maximum_candidates)
                    next_discovery = now + discovery_interval_seconds

                if discovery_future is not None:
                    ready = discovery_future.done()
                    if not ready:
                        try:
                            discovery_future.result(timeout=0.002)
                            ready = True
                        except FuturesTimeoutError:
                            ready = False
                        except Exception:
                            ready = True
                    if ready:
                        try:
                            found = list(discovery_future.result())
                        except Exception:
                            found = []
                        discovery_future = None
                        discoveries += len(found)
                        for snap in sorted(found, key=lambda x: (x.age_minutes, -x.liquidity_usd)):
                            if snap.age_minutes > maximum_entry_age_minutes:
                                continue
                            pair_key = (snap.address, snap.pair_address)
                            if pair_key in admitted_pairs or snap.address in active:
                                continue
                            if len(active) >= maximum_active_sessions:
                                oldest_address, oldest_state = min(
                                    active.items(), key=lambda kv: float(kv[1]["started_monotonic"])
                                )
                                self.store.finish_session(str(oldest_state["session_id"]), "capacity_eviction_for_new_first15")
                                active.pop(oldest_address, None)
                                sessions_finished += 1
                                capacity_evictions += 1
                            sid = self.store.start_session(snap)
                            active[snap.address] = {
                                "session_id": sid, "pair_address": snap.pair_address,
                                "started_monotonic": now, "last_observed_second": -1,
                                "last_inference_second": -1,
                            }
                            admitted_pairs.add(pair_key)
                            sessions_started += 1

                # Mature sessions are closed only after enough time to collect
                # long-horizon outcomes; discovery remains FIRST15-only.
                for address, state in list(active.items()):
                    if now - float(state["started_monotonic"]) >= maximum_session_seconds:
                        self.store.finish_session(str(state["session_id"]), "maximum_session_complete")
                        active.pop(address, None)
                        sessions_finished += 1

                if active:
                    targets = [(address, str(state["pair_address"])) for address, state in active.items()]
                    fetch_started = time.monotonic()
                    try:
                        items, health = self.fetcher(targets)
                    except Exception:
                        items, health = [], {"source": "fetch_error", "complete": False}
                    fetch_cycles += 1
                    incomplete_fetch_cycles += int(not bool(health.get("complete", False)))
                    dex_requests += int(health.get("dex_requests", 0) or 0)
                    gecko_requests += int(health.get("gecko_requests", 0) or 0)
                    latency_ms = (time.monotonic() - fetch_started) * 1000.0
                    by_key = {(item.address, item.pair_address): item for item in items}
                    by_address = {item.address: item for item in items}
                    for address, state in list(active.items()):
                        snap = by_key.get((address, str(state["pair_address"]))) or by_address.get(address)
                        # v0.8.6: the temporal index is real elapsed wall time, not a loop counter.
                        # Slow provider cycles therefore create explicit gaps instead of compressing
                        # several real seconds into one fake "1s" step. No backfill/interpolation.
                        observed_second = max(0, int(time.monotonic() - float(state["started_monotonic"])))
                        if snap is None or snap.pair_address != str(state["pair_address"]):
                            misses += 1
                        elif observed_second > int(state["last_observed_second"]):
                            obs = RealTemporalObservation.from_snapshot(
                                str(state["session_id"]), snap, observed_second,
                                feed_source=str(health.get("source", "unknown")),
                                feed_complete=True,
                                feed_latency_ms=latency_ms,
                            )
                            ticks_written += int(self.store.append_tick(obs))
                            state["last_observed_second"] = observed_second

                        if (
                            self.evaluator is not None
                            and observed_second + 1 >= inference_window_seconds
                            and observed_second - int(state["last_inference_second"]) >= max(1, prediction_stride_seconds)
                        ):
                            counts = self.evaluator.infer_session(
                                str(state["session_id"]), window_seconds=inference_window_seconds
                            )
                            shadow_predictions += sum(int(counts.get(k, 0)) for k in ("temporal", "snapshot", "ensemble", "challenger"))
                            inference_skips += int(counts.get("skipped", 0))
                            state["last_inference_second"] = observed_second

                    if self.evaluator is not None and time.monotonic() >= next_settlement:
                        # Runtime settlement is deliberately sparse and maturity-aware.
                        # It cannot hold the 1 Hz capture loop hostage by rescanning
                        # thousands of immature or terminally rejected predictions.
                        settled_now = self.evaluator.settle(mature_only=True, finalize_rejections=True)
                        shadow_settled += int(settled_now.get("settled", 0))
                        next_settlement = time.monotonic() + settlement_interval_seconds

                now = time.monotonic()
                if status_callback is not None and status_interval_seconds > 0 and now >= next_status:
                    payload = self.store.status()
                    payload.update({
                        "active_sessions": len(active),
                        "runtime_seconds": round(now - started, 3),
                        "ticks_written_this_run": ticks_written,
                        "misses_this_run": misses,
                        "fetch_cycles_this_run": fetch_cycles,
                        "incomplete_fetch_cycles_this_run": incomplete_fetch_cycles,
                        "dex_requests_this_run": dex_requests,
                        "gecko_requests_this_run": gecko_requests,
                        "miss_rate_pct": round(100.0 * misses / max(1, misses + ticks_written), 3),
                    })
                    status_callback(payload)
                    next_status = now + status_interval_seconds

                next_tick += cadence_seconds
                # v0.8.9 cadence budget: if provider latency consumed the slot,
                # do not add another full cadence of idle time. Re-anchor to now.
                # Wall-clock indexing still exposes any missed seconds; there is
                # no catch-up, interpolation, or fabricated tick.
                if next_tick < time.monotonic():
                    next_tick = time.monotonic()
        except KeyboardInterrupt:
            stop_reason = "keyboard_interrupt"
        finally:
            for state in active.values():
                self.store.finish_session(str(state["session_id"]), stop_reason)
                sessions_finished += 1
            if self.evaluator is not None:
                shadow_settled += int(self.evaluator.settle(mature_only=True, finalize_rejections=True).get("settled", 0))
            discovery_executor.shutdown(wait=False, cancel_futures=True)

        elapsed = time.monotonic() - started
        return {
            "version": "0.8.11",
            "runtime_seconds": round(elapsed, 3),
            "discoveries_seen": discoveries,
            "sessions_started": sessions_started,
            "sessions_finished": sessions_finished,
            "ticks_written": ticks_written,
            "misses": misses,
            "shadow_predictions_written": shadow_predictions,
            "shadow_outcomes_settled": shadow_settled,
            "inference_skips": inference_skips,
            "capacity_evictions": capacity_evictions,
            "maximum_active_sessions": maximum_active_sessions,
            "stop_reason": stop_reason,
            "execution_authority_pct": 0,
            "runtime_decision_input": False,
        }


def _safe_log_ratio(current: float, previous: float) -> float:
    import math
    if current <= 0 or previous <= 0:
        return 0.0
    return math.log(current / previous)


def real_temporal_sequence(rows: Sequence[sqlite3.Row]) -> list[list[float]]:
    """Convert observed real ticks to the frozen v0.7 13-feature schema.

    Rolling 5m counters are converted only into non-negative observed deltas.
    Missing seconds are rejected rather than fabricated.
    """
    if not rows:
        raise ValueError("real temporal window cannot be empty")
    seconds = [int(r["monotonic_second"]) for r in rows]
    if seconds != list(range(seconds[0], seconds[0] + len(seconds))):
        raise ValueError("real temporal window contains missing seconds")
    if any(not bool(r["feed_complete"]) or bool(r["market_data_stale"]) for r in rows):
        raise ValueError("real temporal window contains incomplete or stale market data")
    base = rows[0]
    encoded: list[list[float]] = []
    rug_seen = False
    for i, row in enumerate(rows):
        prev = rows[i-1] if i else row
        liquidity = max(float(row["liquidity_usd"]), 1e-9)
        if i:
            volume_1s = max(0.0, float(row["volume_5m_usd"]) - float(prev["volume_5m_usd"]))
            buys_1s = max(0, int(row["buys_5m"]) - int(prev["buys_5m"]))
            sells_1s = max(0, int(row["sells_5m"]) - int(prev["sells_5m"]))
        else:
            volume_1s = 0.0; buys_1s = 0; sells_1s = 0
        tx = buys_1s + sells_1s
        rug_seen = rug_seen or bool(row["hard_risk_seen"])
        social_available = bool(row["social_data_available"])
        smart_available = bool(row["smart_wallet_data_available"])
        encoded.append([
            _safe_log_ratio(float(row["price_usd"]), float(prev["price_usd"])),
            _safe_log_ratio(float(row["price_usd"]), float(base["price_usd"])),
            _safe_log_ratio(float(row["liquidity_usd"]), float(prev["liquidity_usd"])),
            _safe_log_ratio(float(row["liquidity_usd"]), float(base["liquidity_usd"])),
            _safe_log_ratio(float(row["market_cap_usd"]), float(prev["market_cap_usd"])),
            volume_1s / liquidity * 10_000.0,
            float(tx),
            (buys_1s - sells_1s) / max(1, tx),
            float(row["social_mentions_1s"] or 0.0) if social_available else 0.0,
            float(row["social_sentiment"] or 0.0) if social_available else 0.0,
            float(row["smart_wallet_flow_1s_usd"] or 0.0) / liquidity * 10_000.0 if smart_available else 0.0,
            float(social_available),
            float(rug_seen),
        ])
    if any(len(tick) != len(FEATURE_NAMES) for tick in encoded):
        raise AssertionError("real temporal feature width drifted")
    return encoded


def _prediction_utility(p: dict[str, float]) -> float:
    return p["p5"]*.05 + p["p10"]*.10 + p["p25"]*.25 - p["p_rug"]*.40 - p["p_stagnation"]*.025


class RealShadowEvaluator:
    """Logs immutable shadow predictions and settles them from later real ticks."""

    SNAPSHOT_SECONDS_TO_MINUTES = {60: 1, 180: 3, 300: 5, 600: 10, 900: 15}

    def __init__(self, store: RealTemporalStore, temporal_model: TemporalShadowBundle | None = None,
                 *, temporal_model_sha256: str = "none", challenger_model: Any | None = None,
                 challenger_model_sha256: str = "none"):
        self.store = store
        self.temporal_model = temporal_model
        self.temporal_model_sha256 = temporal_model_sha256
        self.challenger_model = challenger_model
        self.challenger_model_sha256 = challenger_model_sha256

    def _insert(self, *, session_id: str, address: str, observed_at: str, observed_second: int,
                horizon_seconds: int, model_kind: str, model_lineage: str, model_sha256: str,
                feature_checksum: str, p: dict[str, float]) -> bool:
        key = f"{session_id}|{observed_second}|{horizon_seconds}|{model_kind}|{model_sha256}"
        prediction_id = "shadow-" + _sha(key)[:32]
        cur = self.store.conn.execute(
            """INSERT OR IGNORE INTO real_shadow_predictions(
            prediction_id,session_id,address,observed_at,observed_second,horizon_seconds,model_kind,
            model_lineage,model_sha256,feature_checksum,p5,p10,p25,p_rug,p_stagnation,expected_utility,
            inference_at,execution_authority_pct,immutable) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,1)""",
            (prediction_id,session_id,address,observed_at,observed_second,horizon_seconds,model_kind,
             model_lineage,model_sha256,feature_checksum,p["p5"],p["p10"],p["p25"],p["p_rug"],
             p["p_stagnation"],_prediction_utility(p),utc_now()),
        )
        self.store.conn.commit()
        return cur.rowcount == 1

    def _adaptive_temporal_weight(self, horizon_seconds: int, minimum_pairs: int = 100) -> dict[str, float | int | str]:
        """Choose Temporal weight using only already-settled paired evidence.

        Snapshot remains the default. Temporal receives weight only after it
        beats Snapshot on the same horizon by a real Brier margin. This is
        shadow-only and cannot affect execution.
        """
        rows = self.store.conn.execute(
            """SELECT tp.p5 tp5,tp.p10 tp10,tp.p25 tp25,tp.p_rug tpr,tp.p_stagnation tps,
                      sp.p5 sp5,sp.p10 sp10,sp.p25 sp25,sp.p_rug spr,sp.p_stagnation sps,
                      o.hit_5,o.hit_10,o.hit_25,o.rug_or_liquidity_collapse,o.stagnant
               FROM real_shadow_predictions tp
               JOIN real_shadow_outcomes o ON o.prediction_id=tp.prediction_id
               JOIN real_shadow_predictions sp
                 ON sp.session_id=tp.session_id AND sp.observed_second=tp.observed_second
                AND sp.horizon_seconds=tp.horizon_seconds AND sp.model_kind='snapshot'
               JOIN real_shadow_outcomes so ON so.prediction_id=sp.prediction_id
               WHERE tp.model_kind='temporal' AND tp.horizon_seconds=?
               ORDER BY tp.inference_at DESC LIMIT 2000""",
            (int(horizon_seconds),),
        ).fetchall()
        n = len(rows)
        if n < int(minimum_pairs):
            return {"weight": 0.0, "pairs": n, "reason": "insufficient_paired_evidence"}
        labels = ("hit_5", "hit_10", "hit_25", "rug_or_liquidity_collapse", "stagnant")
        tcols = ("tp5", "tp10", "tp25", "tpr", "tps")
        scols = ("sp5", "sp10", "sp25", "spr", "sps")
        weights = (1/6, 1/6, 1/6, 1/4, 1/4)
        tb = sb = 0.0
        for wk, tc, sc, lab in zip(weights, tcols, scols, labels):
            tb += wk * sum((float(r[tc])-int(r[lab]))**2 for r in rows) / n
            sb += wk * sum((float(r[sc])-int(r[lab]))**2 for r in rows) / n
        margin = sb - tb
        # Temporal must beat Snapshot by at least 0.002 Brier before it receives
        # any authority inside the *shadow ensemble*. Weight grows gradually and
        # is capped so Snapshot always remains represented.
        if margin < 0.002 or sb <= 1e-12:
            w = 0.0
            reason = "snapshot_preferred"
        else:
            relative = margin / sb
            w = min(0.75, max(0.15, relative * 2.5))
            reason = "temporal_proven_on_horizon"
        return {
            "weight": round(w, 4), "pairs": n, "reason": reason,
            "temporal_brier": round(tb, 6), "snapshot_brier": round(sb, 6),
            "brier_advantage": round(margin, 6),
        }

    def infer_session(self, session_id: str, *, window_seconds: int = 60,
                      horizons_seconds: Sequence[int] = OUTCOME_HORIZONS_SECONDS) -> dict[str, int]:
        rows = self.store.conn.execute(
            "SELECT * FROM real_temporal_ticks WHERE session_id=? ORDER BY monotonic_second", (session_id,)
        ).fetchall()
        if not rows:
            return {"temporal":0,"snapshot":0,"ensemble":0,"adaptive_ensemble":0,"skipped":1}
        observed = rows[-1]
        snapshot_obj = self._snapshot_from_tick(observed)
        counts = {"temporal":0,"snapshot":0,"ensemble":0,"adaptive_ensemble":0,"challenger":0,"skipped":0}

        # v0.8.8: snapshot inference is independent of temporal-window validity.
        # Real feeds can have sparse seconds; that must not suppress a model that only
        # consumes the current observed snapshot. Temporal stays fail-closed until a
        # valid causal window exists. No rows are fabricated or forward-filled.
        temporal = None
        checksum = "none"
        if self.temporal_model is not None and len(rows) >= window_seconds:
            window = rows[-window_seconds:]
            try:
                sequence = real_temporal_sequence(window)
            except ValueError:
                counts["skipped"] = 1
            else:
                checksum = _sha(json.dumps(sequence, sort_keys=True, separators=(",",":")))
                temporal = self.temporal_model.network.predict(sequence)
        elif self.temporal_model is not None:
            counts["skipped"] = 1
        # v0.8.11 challenger consumes the elapsed 60s window without filling gaps.
        # Unlike the frozen bootstrap, it has independent heads for each horizon.
        challenger_rows = [r for r in rows if int(r["monotonic_second"]) >= int(observed["monotonic_second"]) - window_seconds + 1]
        for horizon in horizons_seconds:
            h = int(horizon)
            snapshot = None
            challenger = None
            if self.challenger_model is not None and str(h) in self.challenger_model.heads:
                try:
                    challenger = self.challenger_model.predict(
                        challenger_rows, observed_second=int(observed["monotonic_second"]), horizon_seconds=h
                    )
                except ValueError:
                    counts["skipped"] = 1
                else:
                    challenger_checksum = _sha(json.dumps({
                        "rows":[str(r["raw_checksum"]) for r in challenger_rows],"horizon":h
                    }, sort_keys=True, separators=(",",":")))
                    counts["challenger"] += int(self._insert(
                        session_id=session_id,address=str(observed["address"]),observed_at=str(observed["captured_at"]),
                        observed_second=int(observed["monotonic_second"]),horizon_seconds=h,
                        model_kind="temporal_horizon_challenger",model_lineage="0.8.11-horizon-aware-gap-aware-real-shadow",
                        model_sha256=self.challenger_model_sha256,feature_checksum=challenger_checksum,p=challenger))
            minutes = self.SNAPSHOT_SECONDS_TO_MINUTES.get(h)
            if minutes is not None:
                snapshot_prediction = next(x for x in LearningFamily().predict(snapshot_obj, minutes) if x.family == "balanced")
                snapshot = {name: float(getattr(snapshot_prediction,name)) for name in OUTPUT_NAMES}
            if temporal is not None:
                counts["temporal"] += int(self._insert(
                    session_id=session_id,address=str(observed["address"]),observed_at=str(observed["captured_at"]),
                    observed_second=int(observed["monotonic_second"]),horizon_seconds=h,model_kind="temporal",
                    model_lineage="0.7.0-temporal-shadow",model_sha256=self.temporal_model_sha256,
                    feature_checksum=checksum,p=temporal))
            if snapshot is not None:
                snapshot_checksum = _sha(f"{observed['raw_checksum']}|balanced|{minutes}")
                counts["snapshot"] += int(self._insert(
                    session_id=session_id,address=str(observed["address"]),observed_at=str(observed["captured_at"]),
                    observed_second=int(observed["monotonic_second"]),horizon_seconds=h,model_kind="snapshot",
                    model_lineage="0.3.3-balanced-shadow-heuristic",model_sha256="builtin-balanced-v033",
                    feature_checksum=snapshot_checksum,p=snapshot))
            if temporal is not None and snapshot is not None:
                ensemble = {name:(float(temporal[name])+float(snapshot[name]))/2.0 for name in OUTPUT_NAMES}
                counts["ensemble"] += int(self._insert(
                    session_id=session_id,address=str(observed["address"]),observed_at=str(observed["captured_at"]),
                    observed_second=int(observed["monotonic_second"]),horizon_seconds=h,model_kind="ensemble",
                    model_lineage="0.8.1-equal-shadow-ensemble",model_sha256=_sha(self.temporal_model_sha256+"|builtin-balanced-v033"),
                    feature_checksum=_sha(checksum+"|"+str(observed["raw_checksum"])),p=ensemble))
                policy = self._adaptive_temporal_weight(h)
                # Do not create a pseudo-ensemble before a minimum paired sample
                # exists. Once >=100 settled pairs are available, Snapshot stays
                # dominant unless Temporal has actually earned weight.
                if int(policy.get("pairs") or 0) >= 100:
                    temporal_weight = float(policy.get("weight") or 0.0)
                    adaptive = {
                        name: temporal_weight * float(temporal[name]) + (1.0-temporal_weight) * float(snapshot[name])
                        for name in OUTPUT_NAMES
                    }
                    counts["adaptive_ensemble"] += int(self._insert(
                        session_id=session_id,address=str(observed["address"]),observed_at=str(observed["captured_at"]),
                        observed_second=int(observed["monotonic_second"]),horizon_seconds=h,
                        model_kind="horizon_adaptive_ensemble",
                        model_lineage=f"0.8.14-horizon-gated-shadow-ensemble-w{temporal_weight:.4f}",
                        model_sha256="adaptive-ensemble-v0814",
                        feature_checksum=_sha(checksum+"|"+str(observed["raw_checksum"])+"|"+json.dumps(policy,sort_keys=True)),
                        p=adaptive))
        return counts

    @staticmethod
    def _snapshot_from_tick(row: sqlite3.Row) -> TokenSnapshot:
        from .models import SecuritySignals
        return TokenSnapshot(
            address=str(row["address"]),symbol=str(row["symbol"]),name=str(row["symbol"]),
            pair_address=str(row["pair_address"]),dex_id="shadow",price_usd=float(row["price_usd"]),
            liquidity_usd=float(row["liquidity_usd"]),market_cap_usd=float(row["market_cap_usd"]),
            volume_5m_usd=float(row["volume_5m_usd"]),volume_1h_usd=float(row["volume_5m_usd"]),
            buys_5m=int(row["buys_5m"]),sells_5m=int(row["sells_5m"]),
            price_change_5m_pct=float(row["price_change_5m_pct"]),pair_created_at_ms=0,
            captured_at=str(row["captured_at"]),discovery_source=str(row["discovery_source"]),
            market_data_stale=bool(row["market_data_stale"]),
            security=SecuritySignals(checked=bool(row["security_checked"]))
        )

    def settle(self, *, mature_only: bool = False, finalize_rejections: bool = False) -> dict[str, int]:
        """Settle from observed real ticks without fabricating missing seconds.

        v0.8.10 keeps the v0.8.8 quality policy but adds a runtime-safe path:
        mature_only=True evaluates only predictions whose target second has
        actually elapsed (or whose session has ended). finalize_rejections=True
        records terminal low-coverage/gap/endpoint failures once so they are not
        rescanned every collector cycle. Manual/default calls preserve the older
        diagnostic behavior for immature predictions.
        """
        params: tuple[Any, ...] = ()
        maturity_clause = ""
        if mature_only:
            maturity_clause = """
            AND (m.max_second >= p.observed_second + p.horizon_seconds
                 OR s.ended_at IS NOT NULL)
            """
        pending = self.store.conn.execute(
            f"""SELECT p.*,m.max_second,s.ended_at FROM real_shadow_predictions p
            LEFT JOIN real_shadow_outcomes o ON o.prediction_id=p.prediction_id
            LEFT JOIN real_shadow_settlement_rejections r ON r.prediction_id=p.prediction_id
            LEFT JOIN (SELECT session_id,MAX(monotonic_second) max_second
                       FROM real_temporal_ticks GROUP BY session_id) m ON m.session_id=p.session_id
            LEFT JOIN real_temporal_sessions s ON s.session_id=p.session_id
            WHERE o.prediction_id IS NULL AND r.prediction_id IS NULL
            {maturity_clause}
            ORDER BY p.observed_second""", params
        ).fetchall()
        settled = incomplete = coverage_reject = gap_reject = endpoint_reject = rejected_final = 0
        min_coverage = 0.95
        max_allowed_gap = 3
        max_endpoint_lag = 1
        policy = "v0.8.10:coverage>=0.95,max_gap<=3s,endpoint_lag<=1s,no_fill,maturity-aware"

        def reject(p: sqlite3.Row, reason: str, target: int, actual_end: int, coverage: float, max_gap: int, endpoint_lag: int) -> None:
            nonlocal rejected_final
            if not finalize_rejections:
                return
            self.store.conn.execute(
                """INSERT OR IGNORE INTO real_shadow_settlement_rejections(
                prediction_id,evaluated_at,reason,target_second,actual_end_second,coverage_ratio,
                max_gap_seconds,endpoint_lag_seconds,settlement_policy,immutable)
                VALUES(?,?,?,?,?,?,?,?,?,1)""",
                (p["prediction_id"],utc_now(),reason,target,actual_end,coverage,max_gap,endpoint_lag,policy)
            )
            rejected_final += 1

        for p in pending:
            observed = int(p["observed_second"])
            horizon = int(p["horizon_seconds"])
            target = observed + horizon
            max_session_second = int(p["max_second"]) if p["max_second"] is not None else -1
            session_ended = p["ended_at"] is not None
            # In default/manual diagnostic mode preserve visibility into immature
            # predictions. In runtime mature_only mode, an ended session that never
            # reached target is terminal and can be rejected exactly once.
            if mature_only and max_session_second < target and not session_ended:
                incomplete += 1
                continue
            if mature_only and max_session_second < target - max_endpoint_lag and session_ended:
                incomplete += 1; endpoint_reject += 1
                reject(p, "session_ended_before_target", target, max_session_second, 0.0, 0, target-max_session_second)
                continue
            rows = self.store.conn.execute(
                """SELECT * FROM real_temporal_ticks WHERE session_id=? AND monotonic_second BETWEEN ? AND ?
                ORDER BY monotonic_second""", (p["session_id"], observed, target)
            ).fetchall()
            if not rows or int(rows[0]["monotonic_second"]) != observed:
                incomplete += 1; endpoint_reject += 1
                actual = int(rows[-1]["monotonic_second"]) if rows else -1
                reject(p, "missing_prediction_start", target, actual, 0.0, 0, target-actual if actual >= 0 else target-observed)
                continue
            seconds = [int(r["monotonic_second"]) for r in rows]
            future_observed = sum(1 for sec in seconds if observed < sec <= target)
            coverage = future_observed / max(1, horizon)
            gaps = [b-a for a,b in zip(seconds,seconds[1:])]
            max_gap = max(gaps, default=1)
            actual_end_second = seconds[-1]
            endpoint_lag = target - actual_end_second
            if coverage < min_coverage:
                incomplete += 1; coverage_reject += 1
                reject(p, "coverage_below_0.95", target, actual_end_second, coverage, max_gap, endpoint_lag)
                continue
            if max_gap > max_allowed_gap:
                incomplete += 1; gap_reject += 1
                reject(p, "gap_above_3s", target, actual_end_second, coverage, max_gap, endpoint_lag)
                continue
            if endpoint_lag < 0 or endpoint_lag > max_endpoint_lag:
                incomplete += 1; endpoint_reject += 1
                reject(p, "endpoint_lag_above_1s", target, actual_end_second, coverage, max_gap, endpoint_lag)
                continue
            start = float(rows[0]["price_usd"]); end = float(rows[-1]["price_usd"])
            prices = [float(r["price_usd"]) for r in rows]
            liqs = [float(r["liquidity_usd"]) for r in rows]
            ret = (end/start-1)*100 if start>0 else 0.0
            mfe = (max(prices)/start-1)*100 if start>0 else 0.0
            mae = (min(prices)/start-1)*100 if start>0 else 0.0
            collapse = any(bool(r["hard_risk_seen"]) for r in rows) or (liqs and liqs[-1] <= liqs[0]*0.35)
            stagnant = abs(ret) < 2.0 and mfe < 5.0
            self.store.conn.execute(
                """INSERT INTO real_shadow_outcomes(
                prediction_id,settled_at,start_price_usd,end_price_usd,return_pct,mfe_pct,mae_pct,
                hit_5,hit_10,hit_25,rug_or_liquidity_collapse,stagnant,target_second,settlement_complete,
                actual_end_second,coverage_ratio,max_gap_seconds,endpoint_lag_seconds,settlement_policy)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?)""",
                (p["prediction_id"],utc_now(),start,end,ret,mfe,mae,int(mfe>=5),int(mfe>=10),int(mfe>=25),
                 int(collapse),int(stagnant),target,actual_end_second,coverage,max_gap,endpoint_lag,policy))
            settled += 1
        self.store.conn.commit()
        return {"settled":settled,"incomplete":incomplete,"coverage_reject":coverage_reject,
                "gap_reject":gap_reject,"endpoint_reject":endpoint_reject,
                "finalized_rejections":rejected_final,"execution_authority_pct":0}

    def comparison_report(self) -> dict[str, Any]:
        rows = self.store.conn.execute(
            """SELECT p.*,o.hit_5,o.hit_10,o.hit_25,o.rug_or_liquidity_collapse,o.stagnant
            FROM real_shadow_predictions p JOIN real_shadow_outcomes o ON o.prediction_id=p.prediction_id"""
        ).fetchall()
        by_kind: dict[str,list[sqlite3.Row]] = {}
        for row in rows: by_kind.setdefault(str(row["model_kind"]),[]).append(row)
        weights={"p5":1/6,"p10":1/6,"p25":1/6,"p_rug":1/4,"p_stagnation":1/4}
        labels={"p5":"hit_5","p10":"hit_10","p25":"hit_25","p_rug":"rug_or_liquidity_collapse","p_stagnation":"stagnant"}
        result={}
        for kind, vals in sorted(by_kind.items()):
            brier={name:sum((float(v[name])-int(v[label]))**2 for v in vals)/len(vals) for name,label in labels.items()}
            result[kind]={"rows":len(vals),"balanced_brier":sum(brier[n]*weights[n] for n in weights),"brier_by_output":brier}
        paired = self.store.conn.execute(
            """SELECT COUNT(DISTINCT session_id||'|'||observed_second||'|'||horizon_seconds) n FROM real_shadow_predictions
            WHERE prediction_id IN (SELECT prediction_id FROM real_shadow_outcomes)"""
        ).fetchone()["n"]
        adaptive_weights = {
            str(h): self._adaptive_temporal_weight(h) for h in OUTCOME_HORIZONS_SECONDS
            if h in self.SNAPSHOT_SECONDS_TO_MINUTES
        }
        return {"version":"0.8.14","paired_evaluation_keys":int(paired or 0),"models":result,
                "adaptive_temporal_weights": adaptive_weights,
                "market_proof":False,"execution_authority_pct":0,
                "note":"Real shadow evidence only; horizon-adaptive ensemble is gated by prior paired Brier evidence and has no execution/champion authority."}
