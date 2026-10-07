from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import Config
from .engine import PaperEngine
from .learning import LearningLab
from .models import SecuritySignals, TokenSnapshot
from .store import Store


def _safe_security() -> SecuritySignals:
    return SecuritySignals(
        checked=True, mint_authority_disabled=True, freeze_authority_disabled=True,
        honeypot=None, top10_holder_pct=14.0, creator_holder_pct=0.8,
        metadata_mutable=False, non_transferable=False, transfer_hook_present=False,
        malicious_creator=False,
    )


def run_demo(database: Path, cfg: Config) -> list[dict]:
    store = Store(database, cfg.initial_cash_usd)
    engine = PaperEngine(store, cfg)
    base_time = datetime.now(timezone.utc).replace(microsecond=0)
    created_ms = int((base_time - timedelta(minutes=2)).timestamp() * 1000)
    base = TokenSnapshot(
        address="Demo111111111111111111111111111111111111111", symbol="MYKO",
        name="Mykonos First15 Demo", pair_address="PairDemo11111111111111111111111111111111111",
        dex_id="raydium", price_usd=.001, liquidity_usd=80_000, market_cap_usd=220_000,
        volume_5m_usd=18_000, volume_1h_usd=18_000, buys_5m=42, sells_5m=15,
        price_change_5m_pct=8, pair_created_at_ms=created_ms, captured_at=base_time.isoformat(),
        profile_present=True, discovery_source="demo", security=_safe_security(),
    )
    sequence = [
        base,
        replace(base, captured_at=(base_time + timedelta(seconds=20)).isoformat(), price_usd=.00103,
                volume_5m_usd=21_000, buys_5m=49, sells_5m=17, price_change_5m_pct=10),
        replace(base, captured_at=(base_time + timedelta(minutes=2)).isoformat(), price_usd=.00118,
                volume_5m_usd=35_000, buys_5m=82, sells_5m=27, price_change_5m_pct=18),
        replace(base, captured_at=(base_time + timedelta(minutes=4)).isoformat(), price_usd=.00132,
                volume_5m_usd=48_000, buys_5m=112, sells_5m=39, price_change_5m_pct=25),
        replace(base, captured_at=(base_time + timedelta(minutes=6)).isoformat(), price_usd=.00120,
                volume_5m_usd=42_000, buys_5m=87, sells_5m=66, price_change_5m_pct=-6),
    ]
    outputs = [engine.process(snapshot) for snapshot in sequence]
    outputs.append({"summary": store.dashboard_data(), "equity": round(engine.equity(), 2)})
    store.close()
    return outputs


def run_learning_demo(database: Path, cfg: Config) -> dict:
    store = Store(database, cfg.initial_cash_usd)
    lab = LearningLab(store, prediction_interval_seconds=0)
    base_time = datetime.now(timezone.utc).replace(microsecond=0)
    safe = _safe_security()
    risky = SecuritySignals(True, False, True, None, 58, 9, True, False, True, True)
    cases = [
        ("ROCKET", safe, 55, 16, 12, 90_000, 250_000, 1.18, 1.05),
        ("HYPER", safe, 80, 18, 22, 130_000, 320_000, 1.35, 1.10),
        ("RUGGY", risky, 90, 12, 28, 35_000, 180_000, .55, .15),
        ("FLAT", safe, 24, 20, 1, 60_000, 240_000, 1.01, .98),
    ]
    for index, (symbol, security, buys, sells, change, liquidity, cap, price_mult, liq_mult) in enumerate(cases):
        start = base_time + timedelta(seconds=index)
        created_ms = int((start - timedelta(minutes=2)).timestamp() * 1000)
        snapshot = TokenSnapshot(
            address=f"Learn{index:02d}1111111111111111111111111111111111111", symbol=symbol,
            name=f"{symbol} First15 Case", pair_address=f"Pair{index:02d}11111111111111111111111111111111111111",
            dex_id="raydium", price_usd=.001, liquidity_usd=liquidity, market_cap_usd=cap,
            volume_5m_usd=liquidity * .20, volume_1h_usd=liquidity * .20,
            buys_5m=buys, sells_5m=sells, price_change_5m_pct=change,
            pair_created_at_ms=created_ms, captured_at=start.isoformat(), profile_present=True,
            discovery_source="learning_demo", security=security,
        )
        store.record_snapshot(snapshot)
        lab.observe(snapshot)
        # One terminal point after 16m settles all 1/3/5/10/15m predictions. The
        # deterministic demo proves plumbing only; it is not a market backtest.
        future = replace(snapshot, captured_at=(start + timedelta(minutes=16)).isoformat(),
                         price_usd=snapshot.price_usd * price_mult,
                         liquidity_usd=snapshot.liquidity_usd * liq_mult)
        store.record_snapshot(future)
    settled = lab.settle()
    result = {
        "mode": "SYNTHETIC_FIRST15_INFRASTRUCTURE_TEST_ONLY",
        "database": str(database), "cases": len(cases),
        "shadow_predictions_settled": settled,
        "learning": store.learning_summary(), "stress_test": lab.leaderboard(paths=1000),
    }
    store.close()
    return result
