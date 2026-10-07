from __future__ import annotations

from datetime import datetime

from .config import Config
from .models import Assessment, MicroMetrics, TokenSnapshot


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def derive_micro_metrics(snapshot: TokenSnapshot, previous: TokenSnapshot | None) -> MicroMetrics:
    if previous is None or previous.price_usd <= 0:
        return MicroMetrics()
    now = datetime.fromisoformat(snapshot.captured_at.replace("Z", "+00:00"))
    before = datetime.fromisoformat(previous.captured_at.replace("Z", "+00:00"))
    elapsed = (now - before).total_seconds()
    if elapsed <= 0:
        return MicroMetrics()
    minutes = max(elapsed / 60.0, 1 / 60)
    price_velocity = (snapshot.price_usd / previous.price_usd - 1) * 100 / minutes
    liq_velocity = None
    if previous.liquidity_usd > 0:
        liq_velocity = (snapshot.liquidity_usd / previous.liquidity_usd - 1) * 100 / minutes
    prior_activity = previous.transaction_rate_per_minute
    activity_accel = None
    if prior_activity > 0:
        activity_accel = snapshot.transaction_rate_per_minute / prior_activity - 1
    return MicroMetrics(
        has_previous=True,
        elapsed_seconds=elapsed,
        price_velocity_pct_per_minute=price_velocity,
        liquidity_velocity_pct_per_minute=liq_velocity,
        activity_acceleration=activity_accel,
        buy_ratio_change=snapshot.buy_ratio - previous.buy_ratio,
    )


def assess(snapshot: TokenSnapshot, cfg: Config, micro: MicroMetrics | None = None) -> Assessment:
    micro = micro or MicroMetrics()
    failures: list[str] = []
    warnings: list[str] = []
    sec = snapshot.security

    if snapshot.chain_id != "solana":
        failures.append("wrong_chain")
    age_seconds = snapshot.age_minutes * 60
    if age_seconds < cfg.minimum_entry_age_seconds:
        failures.append("launch_too_new_for_confirmation")
    if snapshot.age_minutes > cfg.maximum_entry_age_minutes:
        failures.append("entry_window_closed")
    if snapshot.liquidity_usd < cfg.minimum_liquidity_usd:
        failures.append("low_liquidity")
    if snapshot.market_cap_usd < cfg.minimum_market_cap_usd:
        failures.append("low_market_cap")
    if snapshot.market_cap_usd > 0 and snapshot.liquidity_ratio < cfg.minimum_liquidity_to_market_cap:
        failures.append("thin_liquidity_ratio")
    if snapshot.jupiter_price_impact_pct is not None and snapshot.jupiter_price_impact_pct > cfg.maximum_jupiter_price_impact_pct:
        failures.append("high_price_impact")

    # Solana security remains fail-closed for authority/control risks. GoPlus's
    # Solana schema does not guarantee an EVM-style is_honeypot field, so missing
    # honeypot data is an explicit warning in PAPER mode rather than a fake veto.
    if not sec.checked:
        failures.append("security_unverified")
    else:
        if sec.mint_authority_disabled is None:
            failures.append("mint_authority_unknown")
        elif not sec.mint_authority_disabled:
            failures.append("mint_authority_active")
        if sec.freeze_authority_disabled is None:
            failures.append("freeze_authority_unknown")
        elif not sec.freeze_authority_disabled:
            failures.append("freeze_authority_active")
        if sec.honeypot is True:
            failures.append("honeypot_signal")
        elif sec.honeypot is None:
            warnings.append("sellability_not_explicitly_simulated")
        if sec.non_transferable is True:
            failures.append("non_transferable_token")
        if sec.transfer_hook_present is True:
            failures.append("transfer_hook_present")
        if sec.malicious_creator is True:
            failures.append("malicious_creator")
        if sec.top10_holder_pct is None:
            warnings.append("holder_concentration_unknown")
        elif sec.top10_holder_pct >= 45:
            failures.append("top10_concentration_high")
        if sec.creator_holder_pct is None:
            warnings.append("creator_holding_unknown")
        elif sec.creator_holder_pct > 5:
            failures.append("creator_holding_high")

    security_score = 0
    if sec.checked:
        security_score += 7 if sec.mint_authority_disabled is True else 0
        security_score += 7 if sec.freeze_authority_disabled is True else 0
        security_score += 5 if sec.non_transferable is False else 0
        security_score += 3 if sec.transfer_hook_present is False else 0
        security_score += 3 if sec.malicious_creator is False else 0
        security_score += 2 if sec.honeypot is False else 0
        security_score += 3 if sec.top10_holder_pct is not None and sec.top10_holder_pct < 30 else 0

    liquidity_depth = _clamp(snapshot.liquidity_usd / max(cfg.minimum_liquidity_usd, 1), 0, 2)
    liquidity_ratio = _clamp(snapshot.liquidity_ratio / 0.10, 0, 1) if snapshot.market_cap_usd > 0 else 0
    liquidity_score = round(7 * min(liquidity_depth, 1.5) + 8 * liquidity_ratio)

    volume_strength = _clamp(snapshot.volume_rate_per_minute / max(cfg.minimum_volume_rate_usd_per_minute, 1), 0, 2)
    tx_strength = _clamp(snapshot.transaction_rate_per_minute / max(cfg.minimum_transactions_per_minute, .1), 0, 2)
    buy_strength = _clamp((snapshot.buy_ratio - .45) / .30, 0, 1)
    flow_score = round(10 * min(volume_strength, 1.2) + 9 * min(tx_strength, 1.2) + 9 * buy_strength)

    structure_score = 0
    if cfg.minimum_price_change_5m_pct <= snapshot.price_change_5m_pct <= cfg.maximum_price_change_5m_pct:
        structure_score += 5
    if snapshot.age_minutes <= cfg.preferred_entry_age_minutes:
        structure_score += 4
    elif snapshot.age_minutes <= cfg.maximum_entry_age_minutes:
        structure_score += 2
    if micro.has_previous:
        v = micro.price_velocity_pct_per_minute or 0.0
        if -2 <= v <= 45:
            structure_score += 3
        a = micro.activity_acceleration
        if a is not None and a >= -0.10:
            structure_score += 2
        if micro.buy_ratio_change is not None and micro.buy_ratio_change >= -0.05:
            structure_score += 1

    social_score = 2 if snapshot.profile_present else 0
    if snapshot.boost_amount > 500:
        warnings.append("heavy_paid_boosting")

    components = {
        "security": min(security_score, 30),
        "liquidity": min(liquidity_score, 20),
        "launch_flow": min(flow_score, 30),
        "micro_structure": min(structure_score, 15),
        "profile": social_score,
    }
    score = min(100, sum(components.values()))

    # Completeness, not win probability. Unlike v0.2 this varies with actual
    # security/micro fields instead of having a hard-coded base of eight knowns.
    checks = [
        snapshot.price_usd > 0,
        snapshot.liquidity_usd > 0,
        snapshot.market_cap_usd > 0,
        snapshot.volume_5m_usd >= 0,
        snapshot.transactions_5m >= 0,
        snapshot.pair_created_at_ms > 0,
        sec.checked,
        sec.mint_authority_disabled is not None,
        sec.freeze_authority_disabled is not None,
        sec.non_transferable is not None,
        sec.transfer_hook_present is not None,
        sec.top10_holder_pct is not None,
        snapshot.jupiter_price_impact_pct is not None,
        micro.has_previous,
    ]
    confidence = round(100 * sum(bool(v) for v in checks) / len(checks))
    eligible = not failures and score >= cfg.minimum_score
    label = "first15 qualified" if eligible else ("blocked" if failures else "insufficient score")
    return Assessment(eligible, score, confidence, label, failures, warnings, components)


def entry_momentum_ok(snapshot: TokenSnapshot, cfg: Config, micro: MicroMetrics | None = None) -> tuple[bool, list[str]]:
    micro = micro or MicroMetrics()
    reasons: list[str] = []
    if snapshot.volume_rate_per_minute < cfg.minimum_volume_rate_usd_per_minute:
        reasons.append("launch_volume_rate_too_low")
    if snapshot.transaction_rate_per_minute < cfg.minimum_transactions_per_minute:
        reasons.append("launch_transaction_rate_too_low")
    if snapshot.buy_ratio < cfg.minimum_buy_ratio:
        reasons.append("buyers_not_dominant")
    if not cfg.minimum_price_change_5m_pct <= snapshot.price_change_5m_pct <= cfg.maximum_price_change_5m_pct:
        reasons.append("launch_momentum_outside_entry_band")
    if micro.has_previous and micro.price_velocity_pct_per_minute is not None:
        if micro.price_velocity_pct_per_minute < -5:
            reasons.append("micro_price_velocity_negative")
        if micro.price_velocity_pct_per_minute > 80:
            reasons.append("micro_price_velocity_chase_risk")
    if micro.has_previous and micro.activity_acceleration is not None:
        if micro.activity_acceleration < -0.55 and snapshot.buy_ratio < 0.62:
            reasons.append("micro_activity_collapsing")
    return not reasons, reasons


def early_probe_ok(snapshot: TokenSnapshot, cfg: Config, micro: MicroMetrics | None = None,
                   assessment: Assessment | None = None) -> tuple[bool, list[str]]:
    """Return whether an exceptional launch may enter the PAPER-only probe lane.

    The probe lane never overrides a *known* critical security risk. It may only
    tolerate security data that is still unavailable/partial, because third-party
    enrichment commonly lags the first minutes of a new Solana pool. The purpose is
    to collect realistic first-minutes outcomes at tiny paper size, not to create a
    live-trading permission path.
    """
    if not cfg.paper_probe_enabled:
        return False, ["probe_disabled"]

    micro = micro or MicroMetrics()
    assessment = assessment or assess(snapshot, cfg, micro)
    sec = snapshot.security
    reasons: list[str] = []
    age_seconds = snapshot.age_minutes * 60

    if snapshot.chain_id != "solana":
        reasons.append("probe_wrong_chain")
    if age_seconds < cfg.minimum_entry_age_seconds:
        reasons.append("probe_launch_too_new")
    if snapshot.age_minutes > cfg.probe_max_age_minutes:
        reasons.append("probe_age_window_closed")
    if snapshot.liquidity_usd < cfg.probe_minimum_liquidity_usd:
        reasons.append("probe_liquidity_too_low")
    if snapshot.market_cap_usd < cfg.probe_minimum_market_cap_usd:
        reasons.append("probe_market_cap_too_low")
    if snapshot.market_cap_usd > 0 and snapshot.liquidity_ratio < cfg.minimum_liquidity_to_market_cap:
        reasons.append("probe_thin_liquidity_ratio")
    if snapshot.volume_rate_per_minute < cfg.probe_minimum_volume_rate_usd_per_minute:
        reasons.append("probe_flow_too_low")
    if snapshot.transaction_rate_per_minute < cfg.probe_minimum_transactions_per_minute:
        reasons.append("probe_transactions_too_low")
    if snapshot.buy_ratio < cfg.probe_minimum_buy_ratio:
        reasons.append("probe_buy_ratio_too_low")
    if assessment.score < cfg.probe_minimum_score:
        reasons.append("probe_score_too_low")
    if snapshot.jupiter_price_impact_pct is not None and snapshot.jupiter_price_impact_pct > cfg.maximum_jupiter_price_impact_pct:
        reasons.append("probe_high_price_impact")

    # At least two observations are mandatory. We are explicitly fast, not one-tick.
    if not micro.has_previous:
        reasons.append("probe_waiting_second_observation")
    if micro.has_previous and micro.price_velocity_pct_per_minute is not None:
        if micro.price_velocity_pct_per_minute < -5:
            reasons.append("probe_micro_price_negative")
        if micro.price_velocity_pct_per_minute > 80:
            reasons.append("probe_micro_chase_risk")
    if micro.has_previous and micro.activity_acceleration is not None:
        if micro.activity_acceleration < -0.55 and snapshot.buy_ratio < 0.68:
            reasons.append("probe_activity_collapsing")

    # Known control/rug risks are never bypassed, even in PAPER probe mode.
    if sec.checked:
        if sec.mint_authority_disabled is False:
            reasons.append("probe_mint_authority_active")
        if sec.freeze_authority_disabled is False:
            reasons.append("probe_freeze_authority_active")
        if sec.honeypot is True:
            reasons.append("probe_honeypot_signal")
        if sec.non_transferable is True:
            reasons.append("probe_non_transferable")
        if sec.transfer_hook_present is True:
            reasons.append("probe_transfer_hook")
        if sec.malicious_creator is True:
            reasons.append("probe_malicious_creator")
        if sec.top10_holder_pct is not None and sec.top10_holder_pct >= 45:
            reasons.append("probe_top10_concentration_high")
        if sec.creator_holder_pct is not None and sec.creator_holder_pct > 5:
            reasons.append("probe_creator_holding_high")

    return not reasons, reasons
