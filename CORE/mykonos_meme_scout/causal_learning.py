from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable

VERSION = "0.8.22"

# This module is deliberately research/learning-only. It must never mutate the
# active Champion/Paper signal, risk, sizing or execution rules.
EXECUTION_AUTHORITY_PCT = 0


def _clamp(v: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, float(v)))


def _finite(v: Any) -> float | None:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except Exception:
        return None


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def snapshot_quality(payload: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Score whether a market snapshot is trustworthy enough for learning.

    Hard failures are only objectively invalid states (non-finite/negative data,
    invalid timestamps, zero price, future timestamps). Soft warnings reduce the
    weight/score but do not pretend that unusual meme-coin behavior is impossible.
    """
    current = now or datetime.now(timezone.utc)
    hard: list[str] = []
    soft: list[str] = []

    address = str(payload.get("address") or "").strip()
    if not address:
        hard.append("missing_address")

    captured = _dt(payload.get("captured_at"))
    if captured is None:
        hard.append("invalid_captured_at")
        age_s = None
    else:
        age_s = (current - captured).total_seconds()
        if age_s < -15:
            hard.append("future_snapshot")
        elif age_s > 300:
            soft.append("stale_snapshot_gt_300s")
        elif age_s > 120:
            soft.append("stale_snapshot_gt_120s")

    price = _finite(payload.get("price_usd"))
    liquidity = _finite(payload.get("liquidity_usd"))
    mcap = _finite(payload.get("market_cap_usd"))
    volume = _finite(payload.get("volume_5m_usd"))
    change = _finite(payload.get("price_change_5m_pct"))

    if price is None or price <= 0:
        hard.append("invalid_price")
    if liquidity is None:
        soft.append("missing_liquidity")
    elif liquidity < 0:
        hard.append("negative_liquidity")
    elif liquidity == 0:
        soft.append("zero_liquidity")
    if mcap is None:
        soft.append("missing_market_cap")
    elif mcap < 0:
        hard.append("negative_market_cap")
    if volume is None:
        soft.append("missing_volume")
    elif volume < 0:
        hard.append("negative_volume")
    if change is None:
        soft.append("missing_price_change")
    elif abs(change) > 5000:
        # We do not reject ordinary extreme meme-coin moves; this only catches
        # obvious parser/unit corruption.
        soft.append("extreme_price_change_suspect")

    buys = payload.get("buys_5m")
    sells = payload.get("sells_5m")
    try:
        if buys is not None and int(buys) < 0:
            hard.append("negative_buys")
        if sells is not None and int(sells) < 0:
            hard.append("negative_sells")
    except Exception:
        soft.append("invalid_transaction_counts")

    created_ms = payload.get("pair_created_at_ms")
    try:
        if created_ms is not None:
            created = datetime.fromtimestamp(int(created_ms) / 1000.0, timezone.utc)
            if captured and created > captured:
                hard.append("pair_created_after_snapshot")
            if created > current.replace(microsecond=0):
                hard.append("future_pair_creation")
    except Exception:
        soft.append("invalid_pair_created_at")

    score = 1.0 - 0.34 * len(set(hard)) - 0.08 * len(set(soft))
    score = _clamp(score, 0.0, 1.0)
    usable = not hard and score >= 0.55
    return {
        "score": round(score, 4),
        "usable_for_learning": bool(usable),
        "hard_failures": sorted(set(hard)),
        "soft_warnings": sorted(set(soft)),
        "snapshot_age_s": None if age_s is None else round(age_s, 2),
        "execution_authority_pct": 0,
    }


def row_quality(row: Any) -> dict[str, Any]:
    """Quality for an ML row when the originating snapshot was joined in.

    Historical rows without a payload are preserved as UNKNOWN rather than
    silently deleted. New runtime rows normally have an exact snapshot match.
    """
    raw = None
    try:
        raw = row["source_payload_json"]
    except Exception:
        raw = None
    if not raw:
        return {
            "score": 0.70,
            "usable_for_learning": True,
            "hard_failures": [],
            "soft_warnings": ["source_snapshot_unavailable_historical"],
            "execution_authority_pct": 0,
        }
    try:
        payload = json.loads(raw)
    except Exception:
        return {
            "score": 0.0,
            "usable_for_learning": False,
            "hard_failures": ["source_snapshot_json_invalid"],
            "soft_warnings": [],
            "execution_authority_pct": 0,
        }
    # For settled historical training rows, staleness relative to *now* is not a
    # defect. Evaluate structural validity using captured_at itself as reference.
    captured = _dt(payload.get("captured_at"))
    q = snapshot_quality(payload, now=captured or datetime.now(timezone.utc))
    # v0.8.22 preserves the v0.8.21 split between "valid market data" and "actionable decision data".
    # A zero-liquidity snapshot is useful as an outcome/rug observation, but it is
    # not a state from which an executable entry decision can be learned. Keeping
    # such rows in the opportunity network made post-collapse recognition look far
    # better than pre-collapse economic selection. Raw snapshots are still retained
    # in the database and remain available to risk/outcome analytics.
    liq = _finite(payload.get("liquidity_usd"))
    if liq is not None and liq <= 0:
        q = dict(q)
        q["usable_for_learning"] = False
        q["score"] = min(float(q.get("score") or 0.0), 0.35)
        q["hard_failures"] = sorted(set((q.get("hard_failures") or []) + ["non_actionable_zero_liquidity"]))

    # Outcome integrity guard: keep real rugs, reject impossible/stale positive labels.
    def reject(reason: str):
        q["usable_for_learning"] = False
        q["score"] = min(float(q.get("score") or 0.0), 0.20)
        q["hard_failures"] = sorted(set((q.get("hard_failures") or []) + [reason]))
    try:
        ret = float(row["return_pct"]); mx = float(row["max_return_pct"]); mn = float(row["min_return_pct"])
        if not all(math.isfinite(v) for v in (ret,mx,mn)) or ret < -100.5 or mx < mn:
            reject("outcome_math_invalid")
        if abs(ret) > 100000 or mx > 100000:
            reject("outcome_extreme_artifact")
        p0 = _dt(row["predicted_at"]); pm = _dt(row["measured_at"]); h = int(row["horizon_minutes"] or 0)
        if p0 and pm and h > 0 and (pm-p0).total_seconds() > h*60 + max(120, h*30):
            reject("outcome_measurement_too_late")
        term_raw = row["terminal_payload_json"]
        if term_raw:
            term = json.loads(term_raw); tliq = _finite(term.get("liquidity_usd")); epair = str(payload.get("pair_address") or ""); tpair = str(term.get("pair_address") or "")
            if epair and tpair and epair != tpair:
                reject("outcome_pair_changed")
            if tliq is not None and tliq <= 0 and ret > 100:
                reject("terminal_zero_liquidity_positive_artifact")
            if tliq is not None and liq is not None and ret > 1000 and tliq < max(100.0, liq*0.02):
                reject("terminal_liquidity_collapse_positive_artifact")
            if liq is not None and liq < 100 and ret > 500:
                reject("micro_liquidity_positive_artifact")
    except Exception:
        pass
    return q


def filter_learning_rows(rows: Iterable[Any]) -> tuple[list[Any], dict[str, Any]]:
    kept: list[Any] = []
    dropped = 0
    unknown = 0
    scores: list[float] = []
    reasons: dict[str, int] = {}
    for row in rows:
        q = row_quality(row)
        scores.append(float(q.get("score") or 0.0))
        for reason in q.get("hard_failures") or []:
            reasons[reason] = reasons.get(reason, 0) + 1
        if "source_snapshot_unavailable_historical" in (q.get("soft_warnings") or []):
            unknown += 1
        if q.get("usable_for_learning"):
            kept.append(row)
        else:
            dropped += 1
    return kept, {
        "rows_seen": len(scores),
        "rows_kept": len(kept),
        "rows_dropped": dropped,
        "historical_unknown": unknown,
        "mean_quality_score": None if not scores else round(sum(scores) / len(scores), 4),
        "hard_failure_counts": reasons,
        "execution_authority_pct": 0,
    }


def data_quality_report(conn: sqlite3.Connection, limit: int = 1500) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT payload_json FROM snapshots ORDER BY id DESC LIMIT ?", (max(1, int(limit)),)
    ).fetchall()
    counts: dict[str, int] = {}
    hard_rows = 0
    usable = 0
    actionable = 0
    scores: list[float] = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except Exception:
            q = {"score": 0.0, "usable_for_learning": False, "hard_failures": ["snapshot_json_invalid"], "soft_warnings": []}
        else:
            captured = _dt(payload.get("captured_at"))
            q = snapshot_quality(payload, now=captured or datetime.now(timezone.utc))
        scores.append(float(q.get("score") or 0.0))
        if q.get("usable_for_learning"):
            usable += 1
            try:
                liq = _finite(payload.get("liquidity_usd"))
            except Exception:
                liq = None
            if liq is not None and liq > 0:
                actionable += 1
        if q.get("hard_failures"):
            hard_rows += 1
        for reason in (q.get("hard_failures") or []) + (q.get("soft_warnings") or []):
            counts[str(reason)] = counts.get(str(reason), 0) + 1
    n = len(rows)
    return {
        "version": VERSION,
        "rows_checked": n,
        "usable_rows": usable,
        "usable_pct": None if not n else round(100.0 * usable / n, 2),
        "actionable_opportunity_rows": actionable,
        "actionable_opportunity_pct": None if not n else round(100.0 * actionable / n, 2),
        "hard_failure_rows": hard_rows,
        "mean_quality_score": None if not scores else round(sum(scores) / len(scores), 4),
        "top_issues": [
            {"issue": k, "count": v}
            for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
        ],
        "policy": "raw outcomes are retained; zero-liquidity snapshots remain outcome evidence but are excluded from the opportunity-learning network",
        "execution_authority_pct": 0,
    }


def _entry_features(payload: dict[str, Any]) -> dict[str, float]:
    captured = _dt(payload.get("captured_at"))
    created_ms = payload.get("pair_created_at_ms")
    age_s = 0.0
    try:
        if captured is not None and created_ms is not None:
            created = datetime.fromtimestamp(int(created_ms) / 1000.0, timezone.utc)
            age_s = max(0.0, (captured - created).total_seconds())
    except Exception:
        pass
    buys = max(0, int(payload.get("buys_5m") or 0))
    sells = max(0, int(payload.get("sells_5m") or 0))
    tx = buys + sells
    active_min = max(0.33, min(5.0, age_s / 60.0 if age_s else 5.0))
    return {
        "liquidity": max(0.0, _finite(payload.get("liquidity_usd")) or 0.0),
        "market_cap": max(0.0, _finite(payload.get("market_cap_usd")) or 0.0),
        "volume_rate": max(0.0, (_finite(payload.get("volume_5m_usd")) or 0.0) / active_min),
        "tx_rate": tx / active_min,
        "buy_ratio": buys / max(1, tx),
        "price_change": _finite(payload.get("price_change_5m_pct")) or 0.0,
        "age_s": age_s,
    }


def classify_regime(features: dict[str, float]) -> str:
    pc = float(features.get("price_change") or 0.0)
    br = float(features.get("buy_ratio") or 0.0)
    liq = float(features.get("liquidity") or 0.0)
    tx = float(features.get("tx_rate") or 0.0)
    if pc >= 25 and br >= 0.60:
        return "rocket_momentum"
    if pc <= -15 and br >= 0.55:
        return "pullback_recovery"
    if br >= 0.68 and tx >= 8:
        return "buy_pressure"
    if liq < 10000:
        return "thin_liquidity"
    if abs(pc) <= 5 and tx < 6:
        return "quiet_stagnant"
    if tx >= 12:
        return "high_activity"
    return "balanced_flow"


def _pf(values: list[float]) -> float | None:
    gp = sum(v for v in values if v > 0)
    gl = -sum(v for v in values if v < 0)
    if gl > 1e-12:
        return gp / gl
    return 999.0 if gp > 0 else None


def _mean(values: list[float]) -> float | None:
    return None if not values else sum(values) / len(values)


def _corr(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 20 or len(xs) != len(ys):
        return None
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 1e-12 or vy <= 1e-12:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(vx * vy)


def explorer_trade_forensics(conn: sqlite3.Connection, limit: int = 3000) -> dict[str, Any]:
    rows = conn.execute(
        """SELECT policy_name,net_return_pct,close_reason,entry_snapshot_json,policy_json
        FROM explorer_positions WHERE status='CLOSED' AND net_return_pct IS NOT NULL
        ORDER BY id DESC LIMIT ?""", (max(1, int(limit)),)
    ).fetchall()
    if not rows:
        return {"closed": 0, "signals": [], "causal_claim": False, "execution_authority_pct": 0}

    fields: dict[str, list[tuple[float, float]]] = {}
    by_regime: dict[str, list[float]] = {}
    by_exit: dict[str, list[float]] = {}
    wins: dict[str, list[float]] = {}
    losses: dict[str, list[float]] = {}
    for row in rows:
        ret = float(row["net_return_pct"] or 0.0)
        try:
            payload = json.loads(row["entry_snapshot_json"] or "{}")
        except Exception:
            payload = {}
        try:
            policy = json.loads(row["policy_json"] or "{}")
        except Exception:
            policy = {}
        feats = _entry_features(payload)
        regime = classify_regime(feats)
        by_regime.setdefault(regime, []).append(ret)
        by_exit.setdefault(str(row["close_reason"] or "unknown"), []).append(ret)
        merged = {
            "entry_liquidity": feats["liquidity"],
            "entry_volume_rate": feats["volume_rate"],
            "entry_tx_rate": feats["tx_rate"],
            "entry_buy_ratio": feats["buy_ratio"],
            "entry_price_change": feats["price_change"],
            "entry_age_s": feats["age_s"],
            "stop_pct": _finite(policy.get("stop_pct")) or 0.0,
            "max_hold_s": _finite(policy.get("max_hold_s")) or 0.0,
            "profit_giveback_base": _finite(policy.get("profit_giveback_base")) or 2.5,
            "profit_giveback_scale": _finite(policy.get("profit_giveback_scale")) or 1.25,
            "flow_decay_ratio": _finite(policy.get("flow_decay_ratio")) or 0.55,
            "momentum_exit_signals": _finite(policy.get("momentum_exit_signals")) or 3.0,
            "soft_hold_fraction": _finite(policy.get("soft_hold_fraction")) or 0.55,
        }
        bucket = wins if ret > 0 else losses
        for k, v in merged.items():
            fields.setdefault(k, []).append((float(v), ret))
            bucket.setdefault(k, []).append(float(v))

    signals: list[dict[str, Any]] = []
    for key, vals in fields.items():
        c = _corr([x for x, _ in vals], [y for _, y in vals])
        w = _mean(wins.get(key, []))
        l = _mean(losses.get(key, []))
        if c is None and (w is None or l is None):
            continue
        scale = max(1e-9, abs(w or 0.0) + abs(l or 0.0))
        contrast = None if w is None or l is None else (w - l) / scale
        signals.append({
            "factor": key,
            "rows": len(vals),
            "return_correlation": None if c is None else round(c, 4),
            "winner_mean": None if w is None else round(w, 4),
            "loser_mean": None if l is None else round(l, 4),
            "normalized_winner_loser_contrast": None if contrast is None else round(contrast, 4),
            "direction": "higher_with_winners" if (contrast or 0) > .08 else ("lower_with_winners" if (contrast or 0) < -.08 else "weak"),
        })
    signals.sort(key=lambda x: abs(float(x.get("return_correlation") or 0.0)) + abs(float(x.get("normalized_winner_loser_contrast") or 0.0)), reverse=True)

    regimes = [
        {"regime": k, "closed": len(v), "mean_return_pct": round(sum(v) / len(v), 4), "profit_factor": None if _pf(v) is None else round(_pf(v), 3)}
        for k, v in by_regime.items()
    ]
    regimes.sort(key=lambda x: (x["closed"], x["mean_return_pct"]), reverse=True)
    exits = [
        {"reason": k, "closed": len(v), "mean_return_pct": round(sum(v) / len(v), 4)}
        for k, v in by_exit.items()
    ]
    exits.sort(key=lambda x: x["closed"], reverse=True)
    return {
        "closed": len(rows),
        "signals": signals[:8],
        "regimes": regimes[:8],
        "exit_reasons": exits[:8],
        "causal_claim": False,
        "method": "observational matched-direction forensics; hypotheses only until forward A/B validation",
        "execution_authority_pct": 0,
    }


def execution_reality_report(conn: sqlite3.Connection, limit: int = 3000) -> dict[str, Any]:
    """Conservative fill-friction proxy for isolated Explorer Paper evidence.

    It does not overwrite recorded PnL. It simply asks how the same closes would
    look after additional liquidity-sensitive slippage and latency friction.
    """
    rows = conn.execute(
        """SELECT net_return_pct,notional_usd,entry_snapshot_json FROM explorer_positions
        WHERE status='CLOSED' AND net_return_pct IS NOT NULL ORDER BY id DESC LIMIT ?""",
        (max(1, int(limit)),),
    ).fetchall()
    adjusted: list[float] = []
    observed: list[float] = []
    slippages: list[float] = []
    for row in rows:
        ret = float(row["net_return_pct"] or 0.0)
        try:
            payload = json.loads(row["entry_snapshot_json"] or "{}")
        except Exception:
            payload = {}
        f = _entry_features(payload)
        liq = max(1000.0, f["liquidity"])
        notional = max(1.0, float(row["notional_usd"] or 5.0))
        # Square-root market impact proxy plus adverse-latency proxy. Bounded so
        # the stress model is conservative without pretending exact exchange fills.
        impact_side = 0.12 + 0.38 * math.sqrt(notional / liq) * 100.0
        low_liq = max(0.0, (12000.0 - liq) / 12000.0) * 0.35
        latency = min(0.45, abs(f["price_change"]) / 100.0 * 0.12)
        extra_roundtrip = 2.0 * _clamp(impact_side + low_liq + latency, 0.12, 2.25)
        observed.append(ret)
        adjusted.append(ret - extra_roundtrip)
        slippages.append(extra_roundtrip)
    return {
        "closed": len(rows),
        "observed_mean_return_pct": None if not observed else round(sum(observed) / len(observed), 4),
        "reality_adjusted_mean_return_pct": None if not adjusted else round(sum(adjusted) / len(adjusted), 4),
        "observed_profit_factor": None if _pf(observed) is None else round(_pf(observed), 3),
        "reality_adjusted_profit_factor": None if _pf(adjusted) is None else round(_pf(adjusted), 3),
        "mean_extra_roundtrip_friction_pct": None if not slippages else round(sum(slippages) / len(slippages), 4),
        "model": "conservative liquidity-sensitive square-root impact + latency proxy; research only",
        "mutates_recorded_pnl": False,
        "execution_authority_pct": 0,
    }


def regime_memory(conn: sqlite3.Connection, limit: int = 5000) -> dict[str, Any]:
    rows = conn.execute(
        """SELECT policy_name,net_return_pct,entry_snapshot_json FROM explorer_positions
        WHERE status='CLOSED' AND net_return_pct IS NOT NULL ORDER BY id DESC LIMIT ?""",
        (max(1, int(limit)),),
    ).fetchall()
    memory: dict[tuple[str, str], list[float]] = {}
    for row in rows:
        try:
            payload = json.loads(row["entry_snapshot_json"] or "{}")
        except Exception:
            payload = {}
        regime = classify_regime(_entry_features(payload))
        key = (regime, str(row["policy_name"] or "unknown"))
        memory.setdefault(key, []).append(float(row["net_return_pct"] or 0.0))
    cells = []
    for (regime, policy), vals in memory.items():
        cells.append({
            "regime": regime,
            "policy": policy,
            "closed": len(vals),
            "mean_return_pct": round(sum(vals) / len(vals), 4),
            "profit_factor": None if _pf(vals) is None else round(_pf(vals), 3),
            "forward_eligible": len(vals) >= 12,
        })
    cells.sort(key=lambda x: (x["forward_eligible"], x["closed"], x["mean_return_pct"]), reverse=True)
    return {
        "cells": cells[:20],
        "eligible_cells": sum(1 for x in cells if x["forward_eligible"]),
        "method": "policy x market-regime memory; selection signal remains shadow-only until enough forward closes",
        "execution_authority_pct": 0,
    }


def regime_policy_evidence(conn: sqlite3.Connection, limit: int = 5000) -> dict[str, Any]:
    """Return full Explorer evidence for regime- and policy-aware forward sampling.

    This is still research-only. It does not grant execution authority. The purpose
    is to stop spending most of the Explorer budget on combinations that already
    have a sufficiently large, consistently negative paper sample while retaining
    one novelty/audit slot for continued falsification.
    """
    rows = conn.execute(
        """SELECT policy_name,net_return_pct,entry_snapshot_json FROM explorer_positions
        WHERE status='CLOSED' AND net_return_pct IS NOT NULL ORDER BY id DESC LIMIT ?""",
        (max(1, int(limit)),),
    ).fetchall()
    by_regime: dict[str, list[float]] = {}
    by_cell: dict[tuple[str, str], list[float]] = {}
    for row in rows:
        try:
            payload = json.loads(row["entry_snapshot_json"] or "{}")
        except Exception:
            payload = {}
        regime = classify_regime(_entry_features(payload))
        ret = float(row["net_return_pct"] or 0.0)
        policy = str(row["policy_name"] or "unknown")
        by_regime.setdefault(regime, []).append(ret)
        by_cell.setdefault((regime, policy), []).append(ret)

    def pack(vals: list[float]) -> dict[str, Any]:
        pf = _pf(vals)
        return {
            "closed": len(vals),
            "mean_return_pct": 0.0 if not vals else sum(vals) / len(vals),
            "profit_factor": 0.0 if pf is None else float(pf),
        }

    return {
        "regimes": {k: pack(v) for k, v in by_regime.items()},
        "cells": {f"{r}|{p}": pack(v) for (r, p), v in by_cell.items()},
        "execution_authority_pct": 0,
    }


def _policy_numeric(policy: Any) -> list[float]:
    keys = (
        "min_age_s", "max_age_s", "min_liquidity", "min_market_cap", "min_volume_rate",
        "min_tx_rate", "min_buy_ratio", "min_price_change", "max_price_change",
        "stop_pct", "max_hold_s", "profit_giveback_base", "profit_giveback_scale",
        "flow_decay_ratio", "momentum_exit_signals", "soft_hold_fraction",
    )
    spans = (360, 900, 50000, 75000, 12000, 40, .40, 60, 120, 18, 600, 4.5, 1.8, .72, 4, .75)
    out = []
    for key, span in zip(keys, spans):
        try:
            v = float(getattr(policy, key))
        except Exception:
            try:
                v = float(policy[key])
            except Exception:
                v = 0.0
        out.append(v / float(span))
    return out


def policy_distance(a: Any, b: Any) -> float:
    av, bv = _policy_numeric(a), _policy_numeric(b)
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(av, bv)) / len(av))


def choose_diverse_policies(matches: list[Any], *, existing: list[Any], usage: dict[str, int],
                            performance: dict[str, dict[str, Any]], address: str, count: int,
                            regime: str | None = None, regime_evidence: dict[str, Any] | None = None,
                            pinned_names: set[str] | None = None) -> tuple[list[Any], dict[str, Any]]:
    """Edge-first three-lane Explorer allocation.

    v0.8.20 spent most of the allocation on novelty/underuse. v0.8.22 keeps the
    allocation structural instead of weight-tuned: one frozen forward challenger
    lane, one regime/economic evidence lane, and one novelty/audit lane. Mature
    negative regime-policy cells are quarantined from evidence lanes but remain
    eligible for the audit lane so the system can detect a genuine regime change.
    """
    import hashlib

    remaining = list(matches)
    selected: list[Any] = []
    reference = list(existing)
    pinned_names = set(pinned_names or set())
    regime_evidence = regime_evidence or {"regimes": {}, "cells": {}}
    cells = regime_evidence.get("cells") or {}
    regimes = regime_evidence.get("regimes") or {}
    current_regime = str(regime or "unknown")

    def name_of(p: Any) -> str:
        return str(getattr(p, "name", ""))

    def global_stats(p: Any) -> dict[str, Any]:
        return performance.get(name_of(p)) or {}

    def cell_stats(p: Any) -> dict[str, Any]:
        return cells.get(f"{current_regime}|{name_of(p)}") or {}

    def mature_negative(p: Any) -> bool:
        c = cell_stats(p)
        if int(c.get("closed") or 0) >= 8 and float(c.get("mean_return_pct") or 0.0) < 0 and float(c.get("profit_factor") or 0.0) < 0.80:
            return True
        g = global_stats(p)
        return int(g.get("closed") or 0) >= 12 and float(g.get("mean_return_pct") or 0.0) < 0 and float(g.get("profit_factor") or 0.0) < 0.80

    regime_summary = regimes.get(current_regime) or {}
    regime_quarantined = (
        int(regime_summary.get("closed") or 0) >= 30
        and float(regime_summary.get("mean_return_pct") or 0.0) < 0
        and float(regime_summary.get("profit_factor") or 0.0) < 0.90
    )

    def take_best(candidates: list[Any], key) -> Any | None:
        if not candidates:
            return None
        winner = max(candidates, key=key)
        selected.append(winner)
        reference.append(winner)
        remaining.remove(winner)
        return winner

    # Lane 1: exact frozen challenger. Its parameters no longer mutate; only new
    # forward closes can prove or disprove it. Negative mature cells are skipped.
    pinned = [p for p in remaining if name_of(p) in pinned_names and not mature_negative(p)]
    take_best(pinned, lambda p: (float(global_stats(p).get("profit_factor") or 0.0), int(global_stats(p).get("closed") or 0)))

    # Lane 2: regime/economic evidence. If the whole regime has already produced
    # a large negative sample, exploitation is intentionally disabled and this
    # slot falls through to novelty instead of repeatedly paying for the same idea.
    if len(selected) < max(0, int(count)) and not regime_quarantined:
        evidence = [p for p in remaining if not mature_negative(p)]
        def evidence_key(p: Any):
            c = cell_stats(p); g = global_stats(p)
            c_n = int(c.get("closed") or 0); g_n = int(g.get("closed") or 0)
            c_pf = min(4.0, float(c.get("profit_factor") or 0.0))
            g_pf = min(4.0, float(g.get("profit_factor") or 0.0))
            c_ret = max(-50.0, min(50.0, float(c.get("mean_return_pct") or 0.0)))
            g_ret = max(-50.0, min(50.0, float(g.get("mean_return_pct") or 0.0)))
            # Evidence amount is used only as a confidence tie-break; positive
            # economics remain the primary ordering.
            return (c_n >= 4, c_pf, c_ret, g_n >= 5, g_pf, g_ret, min(c_n + g_n, 40))
        take_best(evidence, evidence_key)

    # Lane 3 (and any remaining slots): novelty/audit. Proven-negative hypotheses
    # are allowed here on purpose so the system can still notice when the market
    # changes, but they no longer consume most of the research budget.
    while remaining and len(selected) < max(0, int(count)):
        def novelty_key(p: Any):
            name = name_of(p)
            used = int(usage.get(name, 0))
            novelty = min((policy_distance(p, q) for q in reference), default=.35)
            underused = 1.0 / (1.0 + used)
            jitter = int(hashlib.sha256(f"{address}|{name}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
            return (novelty, underused, jitter)
        take_best(list(remaining), novelty_key)

    return selected, {
        "selected": len(selected),
        "regime": current_regime,
        "regime_quarantined": regime_quarantined,
        "pinned_candidates_available": len(pinned),
        "budget": "profit-first lanes: frozen-forward challenger + regime/economic evidence + novelty/audit",
        "execution_authority_pct": 0,
    }

