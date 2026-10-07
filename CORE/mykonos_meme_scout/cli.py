from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import threading
import subprocess
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from .config import Config
from . import __version__
from .dashboard import serve
from .demo import run_demo, run_learning_demo
from .engine import PaperEngine
from .providers import (
    DexScreenerProvider,
    GeckoTerminalProvider,
    GoPlusProvider,
    JupiterProvider,
    ProviderError,
    ResilientWatchProvider,
    provider_health,
)
from .shadow_feed import ShadowBatchWatchProvider, ShadowDiscoveryCoordinator
from .store import Store
from .machine_intelligence import (
    ML_MODEL_FILENAME, ML_CHAMPION_FILENAME, ML_STATE_FILENAME,
    adaptive_learning_tick, machine_shadow_status, train_machine_shadow,
)
from .simulation import (
    SYNTHETIC_FAMILIES, SYNTHETIC_FAMILY_ALIASES, SimulationStore, run_simulation,
)
from .training_data import DatasetPolicy, build_training_dataset
from .realtime_temporal import (
    ContinuousRealTemporalRunner, RealShadowEvaluator, RealTemporalCollector, RealTemporalStore,
)
from .run_readiness import run_preflight
from .temporal_challenger import HorizonAwareChallengerBundle, challenger_status, train_real_challenger
from .temporal_intelligence import (
    TEMPORAL_MODEL_FILENAME,
    build_temporal_dataset,
    temporal_report,
    temporal_status,
    train_temporal_shadow,
    TemporalShadowBundle,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DB_ACTION_LOCK = threading.RLock()
_WATCHER_PENDING = threading.Event()
_WATCH_FEED_HEALTHY = threading.Event()
_WATCH_FEED_HEALTHY.set()


def _runtime(args: argparse.Namespace) -> tuple[Config, Path]:
    cfg = Config.load(args.config)
    database = Path(args.database) if getattr(args, "database", None) else cfg.resolve_database(PROJECT_ROOT)
    return cfg, database


def scan_once(
    cfg: Config, database: Path, maximum: int, *, manage_open_positions: bool = True,
    watcher_priority: bool = False, timing_out: dict | None = None,
    observation_out: dict | None = None, signal_only: bool = False,
) -> list[dict]:
    dex, gecko = DexScreenerProvider(), GeckoTerminalProvider()
    security, jupiter = GoPlusProvider(), JupiterProvider()
    store = Store(database, cfg.initial_cash_usd)
    tracked = store.tracked_addresses(maximum * 2, cfg.tracking_retention_minutes)
    open_addresses = {str(row["address"]) for row in store.open_positions()}
    phase: dict[str, float] = {}
    observation = {'providers': {}, 'errors': [], 'market_state': 'UNKNOWN'}

    candidates: dict[str, object] = {}
    discover_started = time.monotonic()
    # Fresh-pool and curated discovery are independent. Provider methods have
    # their own hard deadlines so a Windows TLS stall cannot hold this executor.
    with ThreadPoolExecutor(max_workers=2) as pool:
        gecko_future = pool.submit(
            gecko.discover, maximum, cfg.maximum_entry_age_minutes + 3,
            request_timeout=cfg.scan_discovery_request_timeout_seconds,
            hard_deadline=cfg.scan_discovery_hard_deadline_seconds,
        )
        dex_future = pool.submit(
            dex.discover, maximum,
            request_timeout=cfg.scan_discovery_request_timeout_seconds,
            hard_deadline=cfg.scan_discovery_hard_deadline_seconds,
        )
        try:
            for item in gecko_future.result():
                candidates[item.address] = item
        except ProviderError as exc:
            observation['errors'].append('gecko_discovery_failed')
            print(f"WARN GeckoTerminal fresh-pool feed unavailable: {exc}", file=sys.stderr)
        try:
            for item in dex_future.result():
                if item.age_minutes <= cfg.maximum_entry_age_minutes + 3:
                    candidates.setdefault(item.address, item)
        except ProviderError as exc:
            observation['errors'].append('dex_discovery_failed')
            print(f"WARN DEX Screener discovery unavailable: {exc}", file=sys.stderr)
    phase["discover"] = time.monotonic() - discover_started
    observation['providers']['gecko'] = getattr(gecko, 'last_observation', {'state': 'UNKNOWN'})
    observation['providers']['dex'] = getattr(dex, 'last_observation', {'state': 'UNKNOWN'})

    # DEX-discovered candidates were already pair-refreshed inside discover().
    # Refresh only Gecko-only candidates plus tracked tokens missing a fresh DEX
    # snapshot. This removes a duplicate public API pass from every scan cycle.
    fresh_dex_addresses = {
        address for address, item in candidates.items()
        if getattr(item, "discovery_source", "") == "dexscreener"
    }
    refresh_addresses = list(dict.fromkeys([
        *[address for address in candidates if address not in fresh_dex_addresses],
        *[address for address in tracked if address not in fresh_dex_addresses],
    ]))
    refresh_started = time.monotonic()
    if refresh_addresses:
        try:
            for item in dex.fetch_addresses(
                refresh_addresses,
                request_timeout=cfg.scan_refresh_request_timeout_seconds,
                hard_deadline=cfg.scan_refresh_hard_deadline_seconds,
            ):
                candidates[item.address] = item
        except ProviderError as exc:
            observation['errors'].append('dex_refresh_failed')
            print(f"WARN DEX Screener refresh unavailable; keeping fallback snapshots: {exc}", file=sys.stderr)
    phase["refresh"] = time.monotonic() - refresh_started
    observation['providers']['refresh'] = getattr(dex, 'refresh_observation', {'state': 'NOT_REQUESTED'})

    snapshots = [
        item for item in candidates.values()
        if getattr(item, "price_usd", 0) > 0 and not getattr(item, "market_data_stale", False)
    ]
    if watcher_priority:
        # Hot path: evaluate tokens that can still enter before older tracking/learning rows.
        # This changes only processing order, never eligibility or scoring.
        snapshots.sort(key=lambda s: (s.age_minutes > cfg.maximum_entry_age_minutes, s.age_minutes, s.address not in tracked))
    else:
        snapshots.sort(key=lambda s: (s.address not in tracked, s.age_minutes))
    if not snapshots:
        print("INFO no usable Solana launch candidates in the current public feeds", file=sys.stderr)

    # Security calls are useful only while a token can still enter. Older tracked
    # snapshots are retained for learning outcomes, but do not spend GoPlus IO.
    security_started = time.monotonic()
    fresh_for_security = [s for s in snapshots if s.age_minutes <= cfg.maximum_entry_age_minutes]
    potential = [s.address for s in fresh_for_security
                 if s.liquidity_usd >= cfg.minimum_liquidity_usd
                 and s.market_cap_usd >= cfg.minimum_market_cap_usd
                 and s.liquidity_ratio >= cfg.minimum_liquidity_to_market_cap]
    observation['entry_window_candidates'] = len(fresh_for_security)
    observation['market_gate_candidates'] = len(potential)
    older = {s.address: s for s in snapshots if s.age_minutes > cfg.maximum_entry_age_minutes}
    if fresh_for_security:
        try:
            fresh_for_security = security.enrich(
                fresh_for_security,
                request_timeout=cfg.scan_security_request_timeout_seconds,
                hard_deadline=cfg.scan_security_hard_deadline_seconds,
                priority_addresses=potential,
            )
        except ProviderError as exc:
            observation['errors'].append('security_lookup_failed')
            print(f"WARN security provider unavailable; candidates remain blocked: {exc}", file=sys.stderr)
    enriched = {s.address: s for s in fresh_for_security}
    enriched.update(older)
    snapshots = [enriched.get(s.address, s) for s in snapshots]
    phase["security"] = time.monotonic() - security_started
    observation['security_lookup'] = getattr(security, 'last_observation', {})
    observation['security_checked'] = sum(s.security.checked for s in fresh_for_security)
    observation['holder_top10_known'] = sum(s.security.top10_holder_pct is not None for s in fresh_for_security)
    observation['holder_creator_known'] = sum(s.security.creator_holder_pct is not None for s in fresh_for_security)
    latest_ages = []
    for s in snapshots:
        try:
            stamp = datetime.fromisoformat(s.captured_at.replace('Z', '+00:00'))
            latest_ages.append((datetime.now(timezone.utc)-stamp).total_seconds())
        except (ValueError, TypeError):
            latest_ages.append(float('inf'))
    observation['fresh_market_snapshots'] = sum(_market_fresh(s) for s in snapshots)
    valid_ages = [a for a in latest_ages if 0 <= a < float('inf')]
    observation['market_age_max_seconds'] = max(valid_ages) if valid_ages else None
    observation['market_age_unknown_or_future'] = len(latest_ages)-len(valid_ages)
    observation['market_state'] = 'FRESH' if observation['fresh_market_snapshots'] else 'UNKNOWN'
    from .shadow_session import discovery_verified
    if not snapshots and not observation['errors'] and all(
        discovery_verified(observation['providers'][name]) for name in ('gecko', 'dex')
    ) and (not refresh_addresses or observation['providers']['refresh'].get('network_success') is True):
        observation['market_state'] = 'NO_CANDIDATES'

    engine = PaperEngine(store, cfg)
    output: list[dict] = []
    process_started = time.monotonic()
    for snapshot in snapshots:
        if watcher_priority and snapshot.address in open_addresses:
            continue
        if jupiter.available:
            try:
                snapshot = jupiter.enrich(snapshot)
            except ProviderError as exc:
                print(f"WARN Jupiter quote unavailable for {snapshot.symbol}: {exc}", file=sys.stderr)
        if watcher_priority:
            while _WATCHER_PENDING.is_set():
                time.sleep(0.005)
        with _DB_ACTION_LOCK:
            output.append(engine.process(
                snapshot, manage_existing=manage_open_positions, settle_learning=False,
                entry_authority=(not signal_only and (not watcher_priority or _WATCH_FEED_HEALTHY.is_set())
                                 and _market_fresh(snapshot)),
            ))
    phase["process"] = time.monotonic() - process_started

    learning_started = time.monotonic()
    if watcher_priority:
        # Live/forward hot path: settlement is handled by the dedicated
        # background learner so historical labeling can never delay signals.
        phase["learning"] = 0.0
    else:
        with _DB_ACTION_LOCK:
            store.label_mature_outcomes(batch_limit=500)
            store.label_and_reward_learning_predictions(batch_limit=500)
        phase["learning"] = time.monotonic() - learning_started
    phase["total"] = sum(phase.values())
    if timing_out is not None:
        timing_out.clear()
        timing_out.update(phase)
    observation['evaluated'] = len(output)
    observation['phases_seconds'] = phase
    if observation_out is not None:
        observation_out.clear()
        observation_out.update(observation)
    store.close()
    return output


def _market_fresh(snapshot, maximum_age=30):
    try:
        stamp = datetime.fromisoformat(snapshot.captured_at.replace('Z', '+00:00'))
        age = (datetime.now(timezone.utc)-stamp).total_seconds()
        return snapshot.market_data_stale is False and 0 <= age <= maximum_age
    except (AttributeError, ValueError, TypeError):
        return False



def watch_open_positions_once(
    cfg: Config, database: Path, *, store: Store | None = None,
    watch_provider: ResilientWatchProvider | None = None,
    shadow_database: Path | None = None,
) -> tuple[list[dict], dict]:
    """Low-latency PAPER-only refresh for open positions; never opens a position.

    The continuous watcher passes a persistent SQLite connection and provider so
    the hot path does not reopen/migrate the database every two seconds.
    """
    owns_store = store is None
    store = store or Store(database, cfg.initial_cash_usd)
    watch_provider = watch_provider or ResilientWatchProvider()
    read_started = time.monotonic()
    targets: list[tuple[str, str]] = []
    try:
        for row in store.open_positions():
            previous = store.snapshot_at_or_before(str(row["address"]), str(row["opened_at"]))
            pair_address = previous.pair_address if previous is not None else ""
            targets.append((str(row["address"]), str(pair_address or "")))
        paper_addresses = {mint for mint, _ in targets}
        from .shadow_observation import bound_targets, record_observation, valid_observation
        shadow_targets, shadow_errors = bound_targets(shadow_database)
        # Paper inventory has priority. Conflicting market identities are not
        # silently merged into one observation for the same mint.
        target_pairs = dict(targets)
        for mint, pair in shadow_targets:
            if mint in target_pairs and target_pairs[mint] != pair:
                shadow_errors.append('shadow_paper_pair_conflict')
                continue
            if mint not in target_pairs:
                targets.append((mint, pair))
                target_pairs[mint] = pair
        db_read_seconds = time.monotonic() - read_started
        if not targets:
            return [], {
                "market_seconds": 0.0, "db_seconds": db_read_seconds, "open_positions": 0,
                "feed_complete": True, "watch_source": "none", "feed_errors": [],
                "shadow_observed": 0, "shadow_errors": shadow_errors,
                "shadow_targets": len(shadow_targets),
            }

        market_started = time.monotonic()
        snapshots, feed_meta = watch_provider.fetch_watch_targets(
            targets,
            dex_timeout=cfg.position_watch_market_timeout_seconds,
            gecko_timeout=cfg.position_watch_fallback_timeout_seconds,
            hedge_delay=cfg.position_watch_fallback_hedge_delay_seconds,
            hard_deadline=cfg.position_watch_hard_deadline_seconds,
        )
        market_seconds = time.monotonic() - market_started

        output: list[dict] = []
        observed_shadow = set()
        shadow_pairs = dict(shadow_targets)
        write_started = time.monotonic()
        _WATCHER_PENDING.set()
        try:
            engine = PaperEngine(store, cfg)
            with _DB_ACTION_LOCK:
                for snapshot in snapshots:
                    if snapshot.market_data_stale or snapshot.price_usd <= 0:
                        continue
                    if snapshot.address not in target_pairs or not valid_observation(snapshot, target_pairs[snapshot.address]):
                        continue
                    if snapshot.address in paper_addresses:
                        output.append(engine.manage_position_snapshot(snapshot))
                    elif (snapshot.address in target_pairs and record_observation(
                            store, snapshot, target_pairs[snapshot.address])):
                        pass
                    if shadow_pairs.get(snapshot.address) == snapshot.pair_address:
                        observed_shadow.add(snapshot.address)
        finally:
            _WATCHER_PENDING.clear()
        db_seconds = db_read_seconds + (time.monotonic() - write_started)
        return output, {
            "market_seconds": market_seconds,
            "db_seconds": db_seconds,
            "open_positions": len(targets),
            "feed_complete": bool(feed_meta.get("complete")) and len(observed_shadow) == len(shadow_targets),
            "watch_source": str(feed_meta.get("source") or "unknown"),
            "feed_errors": list(feed_meta.get("errors") or []),
            "shadow_observed": len(observed_shadow), "shadow_errors": shadow_errors,
            "shadow_targets": len(shadow_targets),
        }
    finally:
        if owns_store:
            store.close()


def _position_watch_loop(cfg: Config, database: Path, stop_event: threading.Event) -> None:
    interval = max(1.0, float(cfg.position_watch_interval_seconds))
    next_due = time.monotonic() + interval
    last_started: float | None = None
    watch_count = 0
    store = Store(database, cfg.initial_cash_usd)
    watch_provider = ResilientWatchProvider()
    root = Path(__file__).resolve().parents[2]
    shadow_database = (root/'RUNTIME/watch-breadth-v425-shadow.db'
                       if database.resolve() == (root/'CORE/data/imc-main.db').resolve() else None)
    feed_degraded = bool(store.open_positions())
    if feed_degraded:
        _WATCH_FEED_HEALTHY.clear()
    try:
        while True:
            wait_seconds = max(0.0, next_due - time.monotonic())
            if stop_event.wait(wait_seconds):
                break
            started = time.monotonic()
            start_gap = None if last_started is None else started - last_started
            last_started = started
            watch_count += 1
            try:
                results, timing = watch_open_positions_once(
                    cfg, database, store=store, watch_provider=watch_provider,
                    shadow_database=shadow_database,
                )
                elapsed = time.monotonic() - started
                if shadow_database is not None:
                    from .projection_io import write_json_projection
                    write_json_projection(root/'RUNTIME/shadow-watch-status.json', {
                        'schema_version': 1, 'updated_at': datetime.now(timezone.utc).isoformat(),
                        'evidence_domain': 'MARKET_OBSERVATIONS_NOT_FILLS',
                        'feed_complete': timing['feed_complete'],
                        'shadow_targets': timing.get('shadow_targets', 0),
                        'shadow_observed': timing.get('shadow_observed', 0),
                        'shadow_errors': timing.get('shadow_errors', []),
                        'cycle_seconds': elapsed,
                    })
                    from .shadow_session import observe
                    try:
                        observation = observe(root)
                    except Exception as error:
                        observation = {'schema_version': 1, 'error': type(error).__name__,
                            'observed_at': datetime.now(timezone.utc).isoformat()}
                    write_json_projection(root/'RUNTIME/shadow-session-observation.json', observation)
                gap_value = start_gap if start_gap is not None else interval
                open_count = int(timing.get("open_positions", 0))
                feed_complete = bool(timing.get("feed_complete", open_count == 0))
                now_degraded = bool(open_count and not feed_complete)
                if now_degraded:
                    _WATCH_FEED_HEALTHY.clear()
                    if not feed_degraded:
                        print(
                            f"WARN WATCH_FEED_DEGRADED source={timing.get('watch_source')} "
                            f"errors={' | '.join(timing.get('feed_errors') or ['unknown'])} "
                            f"entry_action=PAUSED",
                            file=sys.stderr,
                        )
                else:
                    _WATCH_FEED_HEALTHY.set()
                    if feed_degraded:
                        print(
                            f"{datetime.now(timezone.utc).isoformat()} WATCH_FEED_RECOVERED "
                            f"source={timing.get('watch_source')} entry_action=RESUMED"
                        )
                feed_degraded = now_degraded
                closed = [item for item in results if item.get("closed")]
                for item in closed:
                    trade = item.get("last_exit") or {}
                    print(
                        f"{datetime.now(timezone.utc).isoformat()} POSITION_WATCH symbol={item.get('symbol')} "
                        f"closed=1 reason={trade.get('reason','unknown')} price={trade.get('price')} "
                        f"watch_cycle={elapsed:.2f}s watch_market={timing['market_seconds']:.2f}s "
                        f"watch_db={timing['db_seconds']:.2f}s watch_gap={gap_value:.2f}s "
                        f"watch_source={timing.get('watch_source','unknown')}"
                    )
                latency_warn = timing.get("open_positions", 0) and (
                    elapsed >= cfg.position_watch_warning_latency_seconds
                    or gap_value >= cfg.position_watch_warning_latency_seconds
                )
                if latency_warn:
                    print(
                        f"WARN POSITION_WATCH_LATENCY total={elapsed:.2f}s "
                        f"market={timing['market_seconds']:.2f}s db={timing['db_seconds']:.2f}s "
                        f"gap={gap_value:.2f}s source={timing.get('watch_source','unknown')} "
                        f"target_lt={cfg.position_watch_target_latency_seconds:.2f}s "
                        f"warn_at={cfg.position_watch_warning_latency_seconds:.2f}s",
                        file=sys.stderr,
                    )
                elif timing.get("open_positions", 0) and watch_count % 10 == 0:
                    print(
                        f"{datetime.now(timezone.utc).isoformat()} POSITION_WATCH_HEALTH "
                        f"open={timing['open_positions']} total={elapsed:.2f}s "
                        f"market={timing['market_seconds']:.2f}s db={timing['db_seconds']:.2f}s "
                        f"gap={gap_value:.2f}s source={timing.get('watch_source','unknown')} "
                        f"target_lt={cfg.position_watch_target_latency_seconds:.2f}s"
                    )
            except Exception as exc:  # watcher must never kill the main scanner
                _WATCH_FEED_HEALTHY.clear()
                feed_degraded = True
                print(
                    f"WARN position watcher error: {exc}; entry_action=PAUSED",
                    file=sys.stderr,
                )
            # Fixed cadence: network/DB work consumes the interval instead of being added to it.
            next_due += interval
            if next_due <= time.monotonic():
                next_due = time.monotonic() + 0.05
    finally:
        store.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="mykonos-scout", description="Solana first-15-minute meme-coin paper scout")
    parser.add_argument("--config", help="JSON config path")
    parser.add_argument("--database", help="SQLite database path")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="show safety and provider configuration")
    demo_parser = sub.add_parser("demo", help="run deterministic offline first15 paper demo")
    demo_parser.add_argument("--fresh", action="store_true", help="use a new timestamped demo database")
    sub.add_parser("learning-demo", help="run deterministic 1/3/5/10/15m shadow learning demo")
    sub.add_parser("learning-status", help="show shadow-model, horizon label and cookie counts")
    ml_train = sub.add_parser("ml-train", help="bootstrap the local continual neural shadow system")
    ml_train.add_argument("--max-rows", type=int, default=5000, help="recent resolved rows per horizon")
    ml_train.add_argument("--minimum", type=int, default=500, help="minimum rows required per horizon")
    ml_train.add_argument("--epochs", type=int, default=12)
    sub.add_parser("ml-auto-once", help="run one automatic continual-learning check/update")
    sub.add_parser("ml-status", help="show adaptive/champion neural status, drift and validation")
    simulation_parser = sub.add_parser(
        "simulate", help="build isolated one-second synthetic/historical research scenarios"
    )
    simulation_parser.add_argument("--simulation-database", help="dedicated simulation SQLite path")
    simulation_parser.add_argument("--historical-database", help="read-only real snapshot DB to resample")
    simulation_parser.add_argument("--historical-paths", type=int, default=0)
    simulation_parser.add_argument("--synthetic-per-family", type=int, default=1)
    simulation_parser.add_argument("--families", nargs="+", choices=(
        *SYNTHETIC_FAMILIES, *SYNTHETIC_FAMILY_ALIASES,
    ),
                                   default=list(SYNTHETIC_FAMILIES))
    simulation_parser.add_argument("--duration-seconds", type=int, default=900)
    simulation_parser.add_argument("--block-seconds", type=int, default=30)
    simulation_parser.add_argument("--example-stride-seconds", type=int, default=30)
    simulation_parser.add_argument("--seed", type=int, default=20260904)
    for command in ("sim-status", "simulation-status"):
        simulation_status = sub.add_parser(
            command, help="inspect one dedicated simulation database"
        )
        simulation_status.add_argument("--simulation-database", required=True)
    for command in ("sim-report", "simulation-report"):
        simulation_report = sub.add_parser(
            command, help="report profit/risk and scenario-family simulation diagnostics"
        )
        simulation_report.add_argument("--simulation-database", required=True)
    simulation_export = sub.add_parser(
        "simulation-export", help="export SIMULATION_ONLY labelled rows as JSONL"
    )
    simulation_export.add_argument("--simulation-database", required=True)
    simulation_export.add_argument("--output", required=True)
    training_dataset = sub.add_parser(
        "training-dataset",
        help="build purged Neural Population train/validation/test files across data domains",
    )
    training_dataset.add_argument("--output-directory", required=True)
    training_dataset.add_argument("--simulation-database", action="append", default=[])
    training_dataset.add_argument("--historical-database", action="append", default=[])
    training_dataset.add_argument("--real-database", action="append", default=[])
    training_dataset.add_argument("--train-fraction", type=float, default=.70)
    training_dataset.add_argument("--validation-fraction", type=float, default=.15)
    training_dataset.add_argument("--purge-seconds", type=int, default=900)
    training_dataset.add_argument("--max-rows-per-source", type=int, default=50_000)
    training_dataset.add_argument("--overwrite", action="store_true")
    temporal_dataset = sub.add_parser(
        "temporal-dataset",
        help="build causal one-second sequence windows from isolated simulation databases",
    )
    temporal_dataset.add_argument("--output-directory", required=True)
    temporal_dataset.add_argument("--simulation-database", action="append", required=True)
    temporal_dataset.add_argument("--window-seconds", type=int, default=60)
    temporal_dataset.add_argument("--stride-seconds", type=int, default=30)
    temporal_dataset.add_argument(
        "--horizons-seconds", type=int, nargs="+", default=[60, 180, 300, 600, 900]
    )
    temporal_dataset.add_argument("--max-rows-per-source", type=int, default=50_000)
    temporal_dataset.add_argument("--overwrite", action="store_true")
    temporal_train = sub.add_parser(
        "temporal-train", help="train the isolated v0.7 causal-convolution shadow model"
    )
    temporal_train.add_argument("--dataset-directory", required=True)
    temporal_train.add_argument("--model")
    temporal_train.add_argument("--hidden-units", type=int, default=12)
    temporal_train.add_argument("--kernel-seconds", type=int, default=5)
    temporal_train.add_argument("--epochs", type=int, default=8)
    temporal_train.add_argument("--learning-rate", type=float, default=.02)
    temporal_train.add_argument("--minimum-sequences", type=int, default=40)
    temporal_train.add_argument("--seed", type=int, default=20260904)
    temporal_train.add_argument("--overwrite", action="store_true")
    temporal_status_parser = sub.add_parser(
        "temporal-status", help="inspect a v0.7 temporal shadow model and its safety flags"
    )
    temporal_status_parser.add_argument("--model", required=True)
    temporal_report_parser = sub.add_parser(
        "temporal-report", help="report sequence coverage, leakage gates and shadow metrics"
    )
    temporal_report_parser.add_argument("--dataset-directory", required=True)
    temporal_report_parser.add_argument("--model")
    challenger_train = sub.add_parser(
        "temporal-challenger-train", help="train v0.8.11 horizon-aware gap-aware real shadow challenger"
    )
    challenger_train.add_argument("--real-database", required=True)
    challenger_train.add_argument("--model", required=True)
    challenger_train.add_argument("--window-seconds", type=int, default=60)
    challenger_train.add_argument("--minimum-window-coverage", type=float, default=.85)
    challenger_train.add_argument("--minimum-examples-per-horizon", type=int, default=20)
    challenger_train.add_argument("--epochs", type=int, default=30)
    challenger_train.add_argument("--learning-rate", type=float, default=.01)
    challenger_train.add_argument("--l2", type=float, default=.001)
    challenger_train.add_argument("--overwrite", action="store_true")
    challenger_status_parser = sub.add_parser(
        "temporal-challenger-status", help="inspect v0.8.11 horizon-aware real shadow challenger"
    )
    challenger_status_parser.add_argument("--model", required=True)
    real_collect = sub.add_parser(
        "temporal-real-collect",
        help="collect real FIRST15 market ticks into an isolated v0.8 shadow database",
    )
    real_collect.add_argument("--output-database", required=True)
    real_collect.add_argument("--maximum", type=int, default=10)
    real_collect.add_argument("--duration-seconds", type=int, default=60)
    real_collect.add_argument("--cadence-seconds", type=float, default=1.0)
    real_collect.add_argument("--temporal-model")
    real_collect.add_argument("--inference-window-seconds", type=int, default=60)
    real_collect.add_argument("--prediction-stride-seconds", type=int, default=30)
    real_status = sub.add_parser(
        "temporal-real-status", help="inspect a v0.8 real temporal shadow database"
    )
    real_status.add_argument("--output-database", required=True)
    real_settle = sub.add_parser("temporal-real-settle", help="settle immutable v0.8.1 shadow predictions from later real ticks")
    real_settle.add_argument("--output-database", required=True)
    real_report = sub.add_parser("temporal-real-report", help="compare temporal, snapshot and ensemble shadow predictions on settled real outcomes")
    real_report.add_argument("--output-database", required=True)
    real_run = sub.add_parser(
        "temporal-real-run",
        help="run rolling FIRST15 discovery + one-second temporal paper-shadow collection",
    )
    real_run.add_argument("--output-database", default=str(PROJECT_ROOT / "data" / "real" / "mykonos-temporal-shadow.db"))
    real_run.add_argument("--temporal-model", default=str(PROJECT_ROOT / "models" / "temporal-shadow-bootstrap-v0.8.2.json"))
    real_run.add_argument("--challenger-model", help="optional v0.8.11 horizon-aware gap-aware shadow challenger")
    real_run.add_argument("--maximum", type=int, default=30, help="maximum discovery candidates per cycle")
    real_run.add_argument("--maximum-active-sessions", type=int, default=120)
    real_run.add_argument("--runtime-seconds", type=int, default=0, help="0 means run until Ctrl+C")
    real_run.add_argument("--cadence-seconds", type=float, default=1.0)
    real_run.add_argument("--discovery-interval-seconds", type=float, default=10.0)
    real_run.add_argument("--maximum-session-seconds", type=int, default=1800)
    real_run.add_argument("--inference-window-seconds", type=int, default=60)
    real_run.add_argument("--prediction-stride-seconds", type=int, default=30)
    real_run.add_argument("--status-interval-seconds", type=float, default=60.0)
    preflight = sub.add_parser("temporal-real-preflight", help="verify model, database, authority freeze and optional live discovery before a run")
    preflight.add_argument("--output-database", default=str(PROJECT_ROOT / "data" / "real" / "mykonos-temporal-shadow.db"))
    preflight.add_argument("--temporal-model", default=str(PROJECT_ROOT / "models" / "temporal-shadow-bootstrap-v0.8.2.json"))
    preflight.add_argument("--network", action="store_true", help="also require at least one public discovery provider to answer")
    scan_parser = sub.add_parser("scan", help="scan current fresh public candidates once")
    scan_parser.add_argument("--maximum", type=int, default=30)
    analyze_parser = sub.add_parser("analyze", help="analyze one Solana token address")
    analyze_parser.add_argument("address")
    run_parser = sub.add_parser("run", help="continuously scan fresh public candidates")
    run_parser.add_argument("--maximum", type=int, default=30)
    run_parser.add_argument("--interval", type=float)
    dash_parser = sub.add_parser("dashboard", help="serve local read-only dashboard")
    dash_parser.add_argument("--port", type=int, default=8080)
    report_parser = sub.add_parser("report", help="print compact current database summary")
    report_parser.add_argument("--full", action="store_true", help="print legacy full database dump")
    args = parser.parse_args(argv)
    cfg, database = _runtime(args)

    if args.command == "doctor":
        print(json.dumps({
            "version": __version__,
            "mode": "PAPER_ONLY_FIRST15_STANDARD_EXECUTION_CONTINUAL_AND_TEMPORAL_NEURAL_SHADOW",
            "entry_window": f"{cfg.minimum_entry_age_seconds}s..{cfg.maximum_entry_age_minutes}m",
            "learning_horizons_minutes": [1, 3, 5, 10, 15],
            "position_watch_interval_seconds": cfg.position_watch_interval_seconds,
            "position_watch_market_timeout_seconds": cfg.position_watch_market_timeout_seconds,
            "position_watch_fallback_timeout_seconds": cfg.position_watch_fallback_timeout_seconds,
            "position_watch_fallback_hedge_delay_seconds": cfg.position_watch_fallback_hedge_delay_seconds,
            "position_watch_hard_deadline_seconds": cfg.position_watch_hard_deadline_seconds,
            "position_watch_target_latency_seconds": cfg.position_watch_target_latency_seconds,
            "position_watch_warning_latency_seconds": cfg.position_watch_warning_latency_seconds,
            "scanner_deadlines": {
                "discovery_request_timeout_seconds": cfg.scan_discovery_request_timeout_seconds,
                "discovery_hard_deadline_seconds": cfg.scan_discovery_hard_deadline_seconds,
                "refresh_request_timeout_seconds": cfg.scan_refresh_request_timeout_seconds,
                "refresh_hard_deadline_seconds": cfg.scan_refresh_hard_deadline_seconds,
                "security_request_timeout_seconds": cfg.scan_security_request_timeout_seconds,
                "security_hard_deadline_seconds": cfg.scan_security_hard_deadline_seconds,
            },
            "paper_probe_lane": {
                "enabled": cfg.paper_probe_enabled,
                "max_age_minutes": cfg.probe_max_age_minutes,
                "max_notional_pct": cfg.probe_maximum_notional_pct,
                "unverified_or_partial_security_max_notional_pct": cfg.probe_unverified_maximum_notional_pct,
                "security_can_size_up": False,
                "execution_enabled": cfg.paper_probe_execution_enabled,
            },
            "database": str(database),
            "machine_intelligence": {
                "mode": "continual_adaptive_plus_champion",
                "authority_pct": 0,
                "auto_learning": True,
                "bootstrap_command": "ml-train",
                "automatic_update_command": "ml-auto-once",
                "adaptive_model_file": ML_MODEL_FILENAME,
                "champion_model_file": ML_CHAMPION_FILENAME,
                "state_file": ML_STATE_FILENAME,
                "adaptive_update_trigger": "120 new resolved rows/horizon; 60 during high drift",
                "execution_model": "none; both networks are shadow-only",
                "auto_check_seconds": cfg.neural_auto_check_seconds,
                "max_rows_per_horizon": cfg.neural_max_rows_per_horizon,
                "bootstrap_minimum_rows_per_horizon": cfg.neural_bootstrap_minimum_rows_per_horizon,
                "normal_update_new_rows_per_horizon": cfg.neural_new_rows_per_horizon,
                "high_drift_update_new_rows_per_horizon": cfg.neural_high_drift_new_rows_per_horizon,
                "hidden_units": cfg.neural_hidden_units,
                "update_epochs": cfg.neural_update_epochs,
            },
            "simulation_engine": {
                "installed": True,
                "data_domain": "SIMULATION",
                "synthetic_families": list(SYNTHETIC_FAMILIES),
                "historical_source_mode": "read_only",
                "sequence_cadence": "one_second",
                "separate_database_required": True,
                "eligible_for_neural_research": True,
                "eligible_for_real_training": False,
                "eligible_for_real_champion": False,
                "execution_authority_pct": 0,
            },
            "temporal_intelligence": {
                "installed": True,
                "mode": "offline_temporal_shadow_only",
                "input_cadence": "one_second",
                "architecture": "causal_1d_convolution_with_temporal_mean_pooling",
                "default_model_file": TEMPORAL_MODEL_FILENAME,
                "runtime_integration_enabled": False,
                "eligible_for_real_champion": False,
                "execution_authority_pct": 0,
            },
            "providers": provider_health(),
        }, indent=2))
    elif args.command == "simulate":
        simulation_database = Path(args.simulation_database) if args.simulation_database else (
            PROJECT_ROOT / "data" / "simulations" /
            f"mykonos-simulation-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.db"
        )
        result = run_simulation(
            simulation_database,
            seed=args.seed,
            duration_seconds=args.duration_seconds,
            synthetic_per_family=args.synthetic_per_family,
            families=args.families,
            historical_database=(Path(args.historical_database) if args.historical_database else None),
            historical_paths=args.historical_paths,
            block_seconds=args.block_seconds,
            example_stride_seconds=args.example_stride_seconds,
        )
        print(json.dumps(result, indent=2, default=str))
    elif args.command in {"sim-status", "simulation-status"}:
        simulation_path = Path(args.simulation_database)
        if not simulation_path.is_file():
            raise SystemExit(f"Simulation database not found: {simulation_path}")
        simulation_store = SimulationStore(simulation_path, read_only=True)
        try:
            print(json.dumps(simulation_store.summary(), indent=2, default=str))
        finally:
            simulation_store.close()
    elif args.command in {"sim-report", "simulation-report"}:
        simulation_path = Path(args.simulation_database)
        if not simulation_path.is_file():
            raise SystemExit(f"Simulation database not found: {simulation_path}")
        simulation_store = SimulationStore(simulation_path, read_only=True)
        try:
            print(json.dumps(simulation_store.report(), indent=2, default=str))
        finally:
            simulation_store.close()
    elif args.command == "simulation-export":
        simulation_path = Path(args.simulation_database)
        if not simulation_path.is_file():
            raise SystemExit(f"Simulation database not found: {simulation_path}")
        simulation_store = SimulationStore(simulation_path, read_only=True)
        try:
            result = simulation_store.export_training_jsonl(Path(args.output))
            print(json.dumps(result, indent=2, default=str))
        finally:
            simulation_store.close()
    elif args.command == "training-dataset":
        result = build_training_dataset(
            Path(args.output_directory),
            simulation_databases=tuple(Path(path) for path in args.simulation_database),
            historical_databases=tuple(Path(path) for path in args.historical_database),
            real_databases=tuple(Path(path) for path in args.real_database),
            policy=DatasetPolicy(
                train_fraction=args.train_fraction,
                validation_fraction=args.validation_fraction,
                purge_seconds=args.purge_seconds,
                max_rows_per_source=args.max_rows_per_source,
            ),
            overwrite=args.overwrite,
        )
        print(json.dumps(result, indent=2, default=str))
    elif args.command == "temporal-dataset":
        result = build_temporal_dataset(
            Path(args.output_directory),
            simulation_databases=tuple(Path(path) for path in args.simulation_database),
            window_seconds=args.window_seconds,
            stride_seconds=args.stride_seconds,
            horizons_seconds=tuple(args.horizons_seconds),
            max_rows_per_source=args.max_rows_per_source,
            overwrite=args.overwrite,
        )
        print(json.dumps(result, indent=2, default=str))
    elif args.command == "temporal-train":
        dataset_directory = Path(args.dataset_directory)
        model_path = Path(args.model) if args.model else dataset_directory / TEMPORAL_MODEL_FILENAME
        bundle = train_temporal_shadow(
            dataset_directory,
            model_path,
            hidden_units=args.hidden_units,
            kernel_seconds=args.kernel_seconds,
            epochs=args.epochs,
            learning_rate=args.learning_rate,
            minimum_sequences=args.minimum_sequences,
            seed=args.seed,
            overwrite=args.overwrite,
        )
        print(json.dumps({
            "trained": True,
            "model_path": str(model_path.resolve()),
            "trained_at": bundle.trained_at,
            "metrics": bundle.metrics,
            "shadow_only": True,
            "runtime_integration_enabled": False,
            "eligible_for_real_champion": False,
            "execution_authority_pct": 0,
        }, indent=2, default=str))
    elif args.command == "temporal-status":
        print(json.dumps(temporal_status(Path(args.model)), indent=2, default=str))
    elif args.command == "temporal-report":
        print(json.dumps(temporal_report(
            Path(args.dataset_directory), Path(args.model) if args.model else None
        ), indent=2, default=str))
    elif args.command == "temporal-challenger-train":
        bundle = train_real_challenger(
            Path(args.real_database), Path(args.model), window_seconds=args.window_seconds,
            minimum_window_coverage=args.minimum_window_coverage,
            minimum_examples_per_horizon=args.minimum_examples_per_horizon, epochs=args.epochs,
            learning_rate=args.learning_rate, l2=args.l2, overwrite=args.overwrite,
        )
        print(json.dumps({"version":"0.8.11","trained":True,"model":str(Path(args.model).resolve()),
                          "metrics":bundle.metrics,"execution_authority_pct":0}, indent=2, default=str))
    elif args.command == "temporal-challenger-status":
        print(json.dumps(challenger_status(Path(args.model)), indent=2, default=str))
    elif args.command == "temporal-real-preflight":
        def _network_probe():
            found = 0
            errors = []
            for provider in (GeckoTerminalProvider(), DexScreenerProvider()):
                try:
                    if isinstance(provider, GeckoTerminalProvider):
                        rows = provider.discover(2, cfg.maximum_entry_age_minutes, request_timeout=cfg.scan_discovery_request_timeout_seconds, hard_deadline=cfg.scan_discovery_hard_deadline_seconds)
                    else:
                        rows = provider.discover(2, request_timeout=cfg.scan_discovery_request_timeout_seconds, hard_deadline=cfg.scan_discovery_hard_deadline_seconds)
                    found += len(rows)
                except Exception as exc:
                    errors.append(f"{type(provider).__name__}: {type(exc).__name__}: {exc}")
            return {"ok": found > 0 or len(errors) < 2, "candidates_seen": found, "errors": errors}
        result = run_preflight(
            PROJECT_ROOT, database=Path(args.output_database), model=Path(args.temporal_model),
            network_probe=_network_probe if args.network else None,
        )
        print(json.dumps(result, indent=2, default=str))
        if not result["ready"]:
            raise SystemExit(2)
    elif args.command == "temporal-real-status":
        real_store = RealTemporalStore(Path(args.output_database))
        try:
            print(json.dumps(real_store.status(), indent=2, default=str))
        finally:
            real_store.close()
    elif args.command == "temporal-real-settle":
        real_store = RealTemporalStore(Path(args.output_database))
        try:
            print(json.dumps(RealShadowEvaluator(real_store).settle(), indent=2, default=str))
        finally:
            real_store.close()
    elif args.command == "temporal-real-report":
        real_store = RealTemporalStore(Path(args.output_database))
        try:
            print(json.dumps(RealShadowEvaluator(real_store).comparison_report(), indent=2, default=str))
        finally:
            real_store.close()
    elif args.command == "temporal-real-run":
        model_path = Path(args.temporal_model).resolve()
        if not model_path.is_file():
            raise SystemExit(f"Temporal bootstrap model not found: {model_path}")
        bundle = TemporalShadowBundle.load(model_path)
        model_sha256 = hashlib.sha256(model_path.read_bytes()).hexdigest()
        gecko, dex = GeckoTerminalProvider(), DexScreenerProvider()
        discovery = ShadowDiscoveryCoordinator(gecko, dex)

        def _discover(maximum):
            items, health = discovery.discover(
                maximum, cfg.maximum_entry_age_minutes,
                request_timeout=cfg.scan_discovery_request_timeout_seconds,
                hard_deadline=cfg.scan_discovery_hard_deadline_seconds,
            )
            for error in health.get("errors", []):
                print(f"WARN rolling discovery degraded: {error}", file=sys.stderr)
            if health.get("dex_backoff_seconds", 0) > 0:
                print(
                    f"INFO DexScreener discovery backoff={health['dex_backoff_seconds']}s; "
                    "Gecko/cache remains active",
                    file=sys.stderr,
                )
            return items

        watch = ShadowBatchWatchProvider(gecko=gecko)
        # v0.8.9: a one-second collector cannot permit a >1s watch deadline.
        # Keep the entire market read inside 90% of the requested cadence and
        # leave a small scheduler/DB margin. Missing data remains explicit.
        cadence_budget = max(0.20, float(args.cadence_seconds) * 0.90)
        def _fetch(targets):
            return watch.fetch_watch_targets(
                targets,
                dex_timeout=min(cfg.position_watch_market_timeout_seconds, cadence_budget * 0.78),
                gecko_timeout=min(cfg.position_watch_fallback_timeout_seconds, cadence_budget * 0.55),
                hedge_delay=min(cfg.position_watch_fallback_hedge_delay_seconds, cadence_budget * 0.25),
                hard_deadline=cadence_budget,
            )

        real_store = RealTemporalStore(Path(args.output_database))
        challenger_bundle = None
        challenger_sha256 = "none"
        if args.challenger_model:
            challenger_path = Path(args.challenger_model).resolve()
            if not challenger_path.is_file():
                raise SystemExit(f"Challenger model not found: {challenger_path}")
            challenger_bundle = HorizonAwareChallengerBundle.load(challenger_path)
            challenger_sha256 = hashlib.sha256(challenger_path.read_bytes()).hexdigest()
        evaluator = RealShadowEvaluator(real_store, bundle, temporal_model_sha256=model_sha256,
                                        challenger_model=challenger_bundle, challenger_model_sha256=challenger_sha256)
        def _status(payload):
            print("REAL_SHADOW_STATUS " + json.dumps(payload, sort_keys=True), flush=True)
        try:
            result = ContinuousRealTemporalRunner(real_store, _discover, _fetch, evaluator=evaluator).run(
                maximum_candidates=args.maximum,
                maximum_active_sessions=args.maximum_active_sessions,
                runtime_seconds=args.runtime_seconds,
                cadence_seconds=args.cadence_seconds,
                discovery_interval_seconds=args.discovery_interval_seconds,
                maximum_entry_age_minutes=cfg.maximum_entry_age_minutes,
                maximum_session_seconds=args.maximum_session_seconds,
                inference_window_seconds=args.inference_window_seconds,
                prediction_stride_seconds=args.prediction_stride_seconds,
                status_interval_seconds=args.status_interval_seconds,
                status_callback=_status,
            )
            result["database"] = str(Path(args.output_database).resolve())
            result["temporal_model"] = str(model_path)
            result["temporal_model_sha256"] = model_sha256
            result["challenger_model_loaded"] = bool(challenger_bundle)
            if challenger_bundle is not None:
                result["challenger_model_sha256"] = challenger_sha256
            result["final_status"] = real_store.status()
            result["final_report"] = evaluator.comparison_report()
            print(json.dumps(result, indent=2, default=str))
        finally:
            real_store.close()
    elif args.command == "temporal-real-collect":
        # Discovery is read-only and does not route through PaperEngine. The
        # collector has no execution interface and writes to a dedicated DB.
        gecko, dex = GeckoTerminalProvider(), DexScreenerProvider()
        candidates = {}
        try:
            for item in gecko.discover(
                args.maximum, cfg.maximum_entry_age_minutes,
                request_timeout=cfg.scan_discovery_request_timeout_seconds,
                hard_deadline=cfg.scan_discovery_hard_deadline_seconds,
            ):
                candidates[item.address] = item
        except ProviderError as exc:
            print(f"WARN GeckoTerminal collector discovery unavailable: {exc}", file=sys.stderr)
        try:
            for item in dex.discover(
                args.maximum,
                request_timeout=cfg.scan_discovery_request_timeout_seconds,
                hard_deadline=cfg.scan_discovery_hard_deadline_seconds,
            ):
                if item.age_minutes <= cfg.maximum_entry_age_minutes:
                    candidates[item.address] = item
        except ProviderError as exc:
            print(f"WARN DexScreener collector discovery unavailable: {exc}", file=sys.stderr)
        seeds = sorted(candidates.values(), key=lambda x: (x.age_minutes, -x.liquidity_usd))[:args.maximum]
        if not seeds:
            raise SystemExit("No fresh FIRST15 candidates available for real temporal collection")
        watch = ResilientWatchProvider()
        def _fetch(targets):
            return watch.fetch_watch_targets(
                targets,
                dex_timeout=cfg.position_watch_market_timeout_seconds,
                gecko_timeout=cfg.position_watch_fallback_timeout_seconds,
                hedge_delay=cfg.position_watch_fallback_hedge_delay_seconds,
                hard_deadline=max(
                    cfg.position_watch_market_timeout_seconds,
                    cfg.position_watch_fallback_hedge_delay_seconds + cfg.position_watch_fallback_timeout_seconds,
                ),
            )
        real_store = RealTemporalStore(Path(args.output_database))
        try:
            evaluator = None
            if args.temporal_model:
                model_path = Path(args.temporal_model).resolve()
                bundle = TemporalShadowBundle.load(model_path)
                model_sha256 = hashlib.sha256(model_path.read_bytes()).hexdigest()
                evaluator = RealShadowEvaluator(real_store, bundle, temporal_model_sha256=model_sha256)
            result = RealTemporalCollector(real_store, _fetch, evaluator=evaluator).collect(
                seeds, duration_seconds=args.duration_seconds, cadence_seconds=args.cadence_seconds,
                inference_window_seconds=args.inference_window_seconds,
                prediction_stride_seconds=args.prediction_stride_seconds,
            )
            result["database"] = str(Path(args.output_database).resolve())
            result["temporal_model_loaded"] = bool(args.temporal_model)
            result["status"] = real_store.status()
            print(json.dumps(result, indent=2, default=str))
        finally:
            real_store.close()
    elif args.command == "demo":
        if args.fresh or not args.database:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            database = PROJECT_ROOT / "data" / f"demo-first15-{stamp}.db"
        results = run_demo(database, cfg)
        print(json.dumps({"database": str(database), "events": results}, indent=2, default=str))
    elif args.command == "learning-demo":
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        database = PROJECT_ROOT / "data" / f"learning-demo-first15-{stamp}.db"
        print(json.dumps(run_learning_demo(database, cfg), indent=2, default=str))
    elif args.command == "learning-status":
        store = Store(database, cfg.initial_cash_usd)
        print(json.dumps(store.learning_summary(), indent=2, default=str))
        store.close()
    elif args.command == "ml-train":
        store = Store(database, cfg.initial_cash_usd)
        model_path = database.with_name(ML_MODEL_FILENAME)
        bundle = train_machine_shadow(
            store, model_path, max_rows_per_horizon=args.max_rows,
            minimum_rows_per_horizon=args.minimum, epochs=args.epochs,
        )
        print(json.dumps({
            "trained": True,
            "model_path": str(model_path),
            "authority_pct": 0,
            "trained_at": bundle.trained_at,
            "status": machine_shadow_status(store),
        }, indent=2, default=str))
        store.close()
    elif args.command == "ml-auto-once":
        store = Store(database, cfg.initial_cash_usd)
        try:
            result = adaptive_learning_tick(
                store, max_rows_per_horizon=cfg.neural_max_rows_per_horizon,
                bootstrap_minimum_rows=cfg.neural_bootstrap_minimum_rows_per_horizon,
                normal_new_rows_per_horizon=cfg.neural_new_rows_per_horizon,
                high_drift_new_rows_per_horizon=cfg.neural_high_drift_new_rows_per_horizon,
                hidden_units=cfg.neural_hidden_units, update_epochs=cfg.neural_update_epochs,
                update_learning_rate=cfg.neural_update_learning_rate,
            )
            print(json.dumps(result, indent=2, default=str))
        finally:
            store.close()
    elif args.command == "ml-status":
        store = Store(database, cfg.initial_cash_usd)
        print(json.dumps(machine_shadow_status(store), indent=2, default=str))
        store.close()
    elif args.command == "scan":
        print(json.dumps(scan_once(cfg, database, args.maximum), indent=2))
    elif args.command == "analyze":
        dex, security, jupiter = DexScreenerProvider(), GoPlusProvider(), JupiterProvider()
        snapshots = dex.fetch_addresses([args.address])
        if not snapshots:
            raise SystemExit("No Solana pair found for this address")
        try:
            snapshots = security.enrich(snapshots)
        except ProviderError as exc:
            print(f"WARN security unavailable; entry stays blocked: {exc}", file=sys.stderr)
        if jupiter.available:
            try:
                snapshots[0] = jupiter.enrich(snapshots[0])
            except ProviderError as exc:
                print(f"WARN Jupiter quote unavailable: {exc}", file=sys.stderr)
        store = Store(database, cfg.initial_cash_usd)
        result = PaperEngine(store, cfg).process(snapshots[0])
        store.close()
        print(json.dumps(result, indent=2))
    elif args.command == "run":
        interval = max(0.5, float(args.interval or cfg.scan_interval_seconds))
        print(
            f"PAPER_ONLY_FIRST15_STANDARD_EXECUTION_CONTINUAL_NEURAL_SHADOW loop started; target_interval={interval}s "
            f"position_watch={cfg.position_watch_interval_seconds}s "
            f"watch_timeout={cfg.position_watch_market_timeout_seconds}s "
            f"watch_deadline={cfg.position_watch_hard_deadline_seconds}s database={database}"
        )
        stop_event = threading.Event()
        _WATCH_FEED_HEALTHY.set()
        watcher = threading.Thread(
            target=_position_watch_loop, args=(cfg, database, stop_event),
            name="mykonos-position-watch", daemon=True,
        )
        watcher.start()
        ml_process: subprocess.Popen | None = None
        auto_learn = os.environ.get('IMC_SHADOW_DATA_ONLY') != '1'
        diagnostic_scan = not auto_learn and database.resolve() == (PROJECT_ROOT/'data/imc-main.db').resolve()
        scan_cycle_id = 0
        if diagnostic_scan:
            from .shadow_session import native_process_birth, record_scan
            scan_birth = native_process_birth()
        ml_next_check = time.monotonic()
        ml_log_path = database.with_name("ml-autolearn.log")
        try:
            while True:
                cycle_started = time.monotonic()
                try:
                    scan_timing: dict[str, float] = {}
                    scan_observation: dict = {}
                    results = scan_once(
                        cfg, database, args.maximum, manage_open_positions=False, watcher_priority=True,
                        timing_out=scan_timing,
                        observation_out=scan_observation,
                        signal_only=os.environ.get('IMC_PAPER_FORWARD_SOURCE') == '1',
                    )
                    if diagnostic_scan:
                        scan_cycle_id += 1
                        record_scan(PROJECT_ROOT.parent, scan_cycle_id,
                            time.monotonic()-cycle_started, len(results), scan_birth,
                            observation=scan_observation)
                    passing = sum(1 for item in results if item["decision"] == "PASS")
                    probes = sum(1 for item in results if item["decision"] == "PROBE")
                    probe_shadows = sum(1 for item in results if item["decision"] == "PROBE_SHADOW")
                    opened = sum(1 for item in results if item["opened"])
                    reentry_blocked = sum(1 for item in results if item.get("reentry_blocked"))
                    fresh = sum(1 for item in results if item["age_minutes"] <= cfg.maximum_entry_age_minutes)
                    blockers = Counter(reason for item in results for reason in item.get("hard_failures", []))
                    top_blockers = ",".join(f"{k}:{v}" for k, v in blockers.most_common(3)) or "none"
                    cycle_elapsed = time.monotonic() - cycle_started
                    phases = ",".join(
                        f"{name}:{scan_timing.get(name, 0.0):.2f}"
                        for name in ("discover", "refresh", "security", "process", "learning")
                    )
                    print(
                        f"{datetime.now(timezone.utc).isoformat()} scanned={len(results)} fresh15={fresh} "
                        f"pass={passing} probe={probes} probe_shadow={probe_shadows} opened={opened} reentry_blocked={reentry_blocked} "
                        f"cycle={cycle_elapsed:.2f}s phases={phases} watch={cfg.position_watch_interval_seconds}s blockers={top_blockers}"
                    )
                    # Continual neural updates run in a separate interpreter so CPU-bound
                    # backpropagation cannot stall the scanner/watcher. The child only
                    # reads labels and atomically swaps SHADOW model files.
                    if ml_process is not None and ml_process.poll() is not None:
                        ml_process = None
                    if auto_learn and time.monotonic() >= ml_next_check and ml_process is None:
                        cmd = [sys.executable, "-m", "mykonos_meme_scout"]
                        if args.config:
                            cmd += ["--config", args.config]
                        cmd += ["--database", str(database), "ml-auto-once"]
                        with ml_log_path.open("a", encoding="utf-8") as ml_log:
                            ml_process = subprocess.Popen(
                                cmd, stdout=ml_log, stderr=subprocess.STDOUT,
                                cwd=str(PROJECT_ROOT),
                            )
                        ml_next_check = time.monotonic() + float(cfg.neural_auto_check_seconds)
                except ProviderError as exc:
                    print(f"provider error: {exc}", file=sys.stderr)
                elapsed = time.monotonic() - cycle_started
                time.sleep(max(0.0, interval - elapsed))
        except KeyboardInterrupt:
            print("stopped")
        finally:
            if ml_process is not None and ml_process.poll() is None:
                ml_process.terminate()
                try:
                    ml_process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    ml_process.kill()
            stop_event.set()
            watcher.join(timeout=3.0)
    elif args.command == "dashboard":
        serve(database, args.port)
    elif args.command == "report":
        store = Store(database, cfg.initial_cash_usd)
        engine = PaperEngine(store, cfg)
        if args.full:
            data = store.dashboard_data()
            payload = {"database": str(database), "paper_equity": round(engine.equity(), 2), **data}
        else:
            payload = {"database": str(database), **store.compact_report(
                engine.equity(), cfg.estimated_cost_per_side_pct
            )}
            payload["execution_policy"] = {
                "standard_execution_enabled": True,
                "early_probe_execution_enabled": cfg.paper_probe_execution_enabled,
                "early_probe_shadow_learning_enabled": cfg.paper_probe_enabled,
            }
        print(json.dumps(payload, indent=2, default=str))
        store.close()
