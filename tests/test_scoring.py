"""Tests for the entry gates in scoring.assess().

Focus is on the hard-failure gates that are supposed to block an entry
outright, independent of score - these are the safety-relevant paths,
not the point-scoring details.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from mykonos_meme_scout.config import Config
from mykonos_meme_scout.models import SecuritySignals, TokenSnapshot
from mykonos_meme_scout.scoring import assess

CAPTURED_AT = datetime(2026, 10, 1, 12, 5, 0, tzinfo=timezone.utc)


def _base_snapshot(age_minutes: float = 5.0, **overrides) -> TokenSnapshot:
    """A token snapshot aged `age_minutes` with otherwise-plausible numbers.

    pair_created_at_ms is derived from CAPTURED_AT so the two always agree -
    a snapshot "captured" before it was "created" would silently clamp to
    age 0 (see TokenSnapshot.age_minutes), masking the gate under test.
    Individual tests override just the field(s) relevant to the gate
    they're checking, so a failing assertion points at one cause.
    """
    created_at = CAPTURED_AT - timedelta(minutes=age_minutes)
    defaults = dict(
        address="TokenAddress111",
        symbol="TEST",
        name="Test Token",
        pair_address="PairAddress111",
        dex_id="raydium",
        price_usd=0.001,
        liquidity_usd=25_000.0,
        market_cap_usd=30_000.0,
        volume_5m_usd=10_000.0,
        volume_1h_usd=40_000.0,
        buys_5m=40,
        sells_5m=20,
        price_change_5m_pct=10.0,
        pair_created_at_ms=int(created_at.timestamp() * 1000),
        captured_at=CAPTURED_AT.isoformat(),
        chain_id="solana",
        security=SecuritySignals(
            checked=True,
            mint_authority_disabled=True,
            freeze_authority_disabled=True,
            honeypot=False,
        ),
    )
    defaults.update(overrides)
    return TokenSnapshot(**defaults)


def test_wrong_chain_is_a_hard_failure():
    cfg = Config()
    snapshot = _base_snapshot(chain_id="ethereum")
    result = assess(snapshot, cfg)
    assert result.eligible is False
    assert "wrong_chain" in result.hard_failures


def test_low_liquidity_is_a_hard_failure():
    cfg = Config()
    snapshot = _base_snapshot(liquidity_usd=1_000.0)
    result = assess(snapshot, cfg)
    assert result.eligible is False
    assert "low_liquidity" in result.hard_failures


def test_entry_window_closed_after_max_age():
    cfg = Config()
    snapshot = _base_snapshot(age_minutes=60.0)  # far beyond the 15-minute window
    result = assess(snapshot, cfg)
    assert result.eligible is False
    assert "entry_window_closed" in result.hard_failures


def test_decision_is_watch_when_score_is_below_minimum():
    cfg = Config()
    snapshot = _base_snapshot(liquidity_usd=1_000.0)  # fails a hard gate -> never PASS
    result = assess(snapshot, cfg)
    assert result.decision in {"WATCH", "REJECT"}
    assert result.decision != "PASS"
