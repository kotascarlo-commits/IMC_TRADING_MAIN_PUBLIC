"""Tests for the paper-trading Config invariants.

These lock in the single most important safety property in the whole
project: live trading cannot be turned on by constructing a Config, no
matter what else changes around it.
"""
from __future__ import annotations

import pytest

from mykonos_meme_scout.config import Config


def test_default_config_is_valid():
    cfg = Config()
    assert cfg.live_trading_enabled is False
    assert 1 <= cfg.minimum_score <= 100


def test_live_trading_enabled_is_rejected():
    with pytest.raises(ValueError, match="Live trading is deliberately unavailable"):
        Config(live_trading_enabled=True)


@pytest.mark.parametrize("bad_score", [0, -5, 101, 1000])
def test_minimum_score_out_of_range_is_rejected(bad_score):
    with pytest.raises(ValueError, match="minimum_score"):
        Config(minimum_score=bad_score)


def test_request_timeout_must_be_below_hard_deadline():
    with pytest.raises(ValueError, match="must be below"):
        Config(
            scan_discovery_request_timeout_seconds=5.0,
            scan_discovery_hard_deadline_seconds=2.0,
        )


def test_probe_window_cannot_exceed_entry_window():
    with pytest.raises(ValueError, match="probe_max_age_minutes"):
        Config(maximum_entry_age_minutes=10, probe_max_age_minutes=20)


def test_unknown_config_key_is_rejected(tmp_path):
    bad_path = tmp_path / "bad_config.json"
    bad_path.write_text('{"not_a_real_field": 1}', encoding="utf-8")
    with pytest.raises(ValueError, match="Unknown config keys"):
        Config.load(str(bad_path))
