"""Hard, fail-closed safety outside every neural model. PAPER/SHADOW only."""
from __future__ import annotations

from dataclasses import dataclass
from .validation import finite

EXECUTION_AUTHORITY_PCT = 0


@dataclass(frozen=True)
class SafetyState:
    security_ok: bool | None = None
    catastrophic_risk: bool | None = None
    provider_healthy: bool | None = None
    liquidity_usd: float | None = None
    exposure_pct: float | None = None
    open_positions: int | None = None
    daily_loss_pct: float | None = None
    kill_switch: bool | None = None
    requested_notional_pct: float = .25
    max_exposure_pct: float = 1.5
    max_positions: int = 3
    min_liquidity_usd: float = 15000.0
    max_daily_loss_pct: float = 1.0


def hard_shadow_decision(action: str, state: SafetyState) -> dict:
    reasons = []
    for name, expected in (("security_ok", True), ("catastrophic_risk", False), ("provider_healthy", True), ("kill_switch", False)):
        if getattr(state, name) is not expected:
            reasons.append(name)
    numeric = ("liquidity_usd", "exposure_pct", "open_positions", "daily_loss_pct", "requested_notional_pct", "max_exposure_pct", "max_positions", "min_liquidity_usd", "max_daily_loss_pct")
    if any(not finite(getattr(state, k)) for k in numeric):
        reasons.append("missing_or_nonfinite_safety_context")
    else:
        if state.liquidity_usd < state.min_liquidity_usd or state.min_liquidity_usd <= 0:
            reasons.append("liquidity_safety")
        if state.exposure_pct < 0 or state.requested_notional_pct <= 0 or state.max_exposure_pct <= 0 or state.exposure_pct + state.requested_notional_pct > state.max_exposure_pct:
            reasons.append("max_exposure")
        if state.open_positions < 0 or int(state.open_positions) != state.open_positions or state.max_positions <= 0 or int(state.max_positions) != state.max_positions or state.open_positions >= state.max_positions:
            reasons.append("max_positions")
        if state.daily_loss_pct < 0 or state.max_daily_loss_pct <= 0 or state.daily_loss_pct >= state.max_daily_loss_pct:
            reasons.append("daily_loss_guard")
    if action not in {"TRADE", "ABSTAIN", "VETO"}:
        reasons.append("invalid_model_action")
    final = "VETO" if reasons or action == "VETO" else action
    return {"action": final, "shadow_trade_allowed": final == "TRADE", "reasons": reasons,
            "execution_authority_pct": 0, "live_trade_allowed": False}


def assert_zero_authority(payload: object) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in {"authority_pct", "execution_authority_pct", "live_authority_pct"} and (not finite(value) or value != 0):
                raise ValueError("nonzero_or_invalid_execution_authority")
            if key in {"live_trading_enabled", "live_trade_allowed", "wallet_access"} and value is not False:
                raise ValueError("live_execution_forbidden")
            assert_zero_authority(value)
    elif isinstance(payload, list):
        for value in payload:
            assert_zero_authority(value)
