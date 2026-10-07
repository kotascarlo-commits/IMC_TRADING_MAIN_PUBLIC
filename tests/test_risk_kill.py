"""Tests for PaperEngine.risk_kill_reason().

This is the circuit breaker that is supposed to stop the paper account
(and, if live trading is ever enabled, real capital) from digging
itself deeper into a drawdown. A fresh account must trade freely; a
severely damaged account must be locked out.
"""
from __future__ import annotations

from pathlib import Path

from mykonos_meme_scout.config import Config
from mykonos_meme_scout.engine import PaperEngine
from mykonos_meme_scout.store import Store


def _engine(tmp_path: Path) -> PaperEngine:
    store = Store(tmp_path / "test.db", initial_cash=1000.0)
    return PaperEngine(store, Config())


def test_fresh_account_has_no_kill_reason(tmp_path):
    engine = _engine(tmp_path)
    assert engine.risk_kill_reason() is None


def test_severe_loss_engages_a_kill_switch(tmp_path):
    engine = _engine(tmp_path)
    assert engine.risk_kill_reason() is None  # establish the baseline first

    # Simulate a 40% loss directly on the ledger - far past every configured
    # daily/weekly/drawdown threshold, so some kill reason must engage.
    engine.store.conn.execute("UPDATE account_state SET cash_usd=? WHERE id=1", (600.0,))
    engine.store.conn.commit()

    reason = engine.risk_kill_reason()
    assert reason in {"daily_loss_limit", "weekly_loss_limit", "drawdown_limit"}


def test_kill_reason_persists_once_set(tmp_path):
    engine = _engine(tmp_path)
    engine.store.conn.execute("UPDATE account_state SET cash_usd=? WHERE id=1", (600.0,))
    engine.store.conn.commit()
    first = engine.risk_kill_reason()
    assert first is not None

    # Even if equity were to recover without a new day/week starting,
    # the stored kill_reason should still be reported until explicitly reset.
    second = engine.risk_kill_reason()
    assert second == first
