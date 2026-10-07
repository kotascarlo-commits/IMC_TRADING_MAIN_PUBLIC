from __future__ import annotations

import math
from typing import Any

# The active meme-scout paper engine models 1% execution cost per side.
# Profit-alignment uses the same conservative round-trip assumption, but remains
# strictly a learning/validation signal: it never grants live authority or changes live size.
ROUND_TRIP_COST_PCT = 2.0
MIN_ROWS_PER_HORIZON = 20
MIN_UNIQUE_LAUNCHES = 20


def _clamp(v: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, float(v)))


def predicted_economic_score(pred: list[float]) -> float:
    """Convert model probabilities to a ranking score in return-like percentage units."""
    p5, p10, p25, p_rug, p_stag = [float(x) for x in pred]
    return 5.0 * p5 + 10.0 * p10 + 25.0 * p25 - 40.0 * p_rug - 2.5 * p_stag


def realized_counterfactual_reward(row: Any) -> float:
    """Net, bounded outcome used only to judge whether learning points toward money.

    This is not a simulated executable trade. It is an economic target derived from
    the already-settled horizon return and risk labels, after the same conservative
    2% round-trip cost assumption as the paper engine.
    """
    try:
        ret = float(row["return_pct"] or 0.0) - ROUND_TRIP_COST_PCT
    except Exception:
        ret = -ROUND_TRIP_COST_PCT
    try:
        if int(row["rug"] or 0):
            ret = min(ret, -25.0)
    except Exception:
        pass
    try:
        if int(row["stagnant"] or 0):
            ret -= 0.5
    except Exception:
        pass
    return _clamp(ret, -60.0, 100.0)


def _corr(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 5 or len(xs) != len(ys):
        return None
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 1e-12 or vy <= 1e-12:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(vx * vy)


def _pf(rewards: list[float]) -> float | None:
    gp = sum(x for x in rewards if x > 0)
    gl = -sum(x for x in rewards if x < 0)
    if gl > 1e-12:
        return gp / gl
    return 999.0 if gp > 0 else None


def _regime(row: Any) -> str:
    try:
        if int(row["rug"] or 0):
            return "rug"
        if int(row["hit_25"] or 0):
            return "rocket25"
        if int(row["hit_10"] or 0):
            return "rocket10"
        if int(row["hit_5"] or 0):
            return "rocket5"
        if int(row["stagnant"] or 0):
            return "stagnant"
        r = float(row["return_pct"] or 0.0)
        if r <= -15:
            return "dump"
    except Exception:
        pass
    return "normal"


def _one_model(network: Any, rows: list[Any], feature_fn: Any) -> dict[str, Any]:
    if len(rows) < MIN_ROWS_PER_HORIZON:
        return {"rows": len(rows), "eligible": False}
    scores: list[float] = []
    rewards: list[float] = []
    regimes: list[str] = []
    for row in rows:
        pred = network.predict(feature_fn(row))
        scores.append(predicted_economic_score(pred))
        rewards.append(realized_counterfactual_reward(row))
        regimes.append(_regime(row))
    order = sorted(range(len(rows)), key=lambda i: scores[i], reverse=True)
    top_n = max(2, int(math.ceil(len(order) * 0.25)))
    top = order[:top_n]
    top_rewards = [rewards[i] for i in top]
    top_regimes: dict[str, list[float]] = {}
    for i in top:
        top_regimes.setdefault(regimes[i], []).append(rewards[i])
    corr = _corr(scores, rewards)
    mean_top = sum(top_rewards) / len(top_rewards)
    pf = _pf(top_rewards)
    rug_rate = 100.0 * sum(1 for i in top if regimes[i] == "rug") / len(top)
    positive_rate = 100.0 * sum(1 for x in top_rewards if x > 0) / len(top_rewards)
    return {
        "rows": len(rows),
        "eligible": True,
        "economic_correlation": None if corr is None else round(corr, 5),
        "top_quartile_rows": len(top),
        "top_quartile_mean_net_return_pct": round(mean_top, 4),
        "top_quartile_profit_factor": None if pf is None else round(pf, 4),
        "top_quartile_positive_rate_pct": round(positive_rate, 2),
        "top_quartile_rug_rate_pct": round(rug_rate, 2),
        "top_quartile_regimes": {
            k: {"rows": len(v), "mean_net_return_pct": round(sum(v) / len(v), 4)}
            for k, v in sorted(top_regimes.items())
        },
    }


def profit_alignment_summary(candidate: Any, reference: Any,
                             rows_by_horizon: dict[int, list[Any]], feature_fn: Any) -> dict[str, Any]:
    """Economic validation of candidate vs reference on the same untouched rows.

    Every horizon is aggregated with equal weight so repeated horizons do not turn
    one launch into five independent economic votes.
    """
    horizons: dict[str, Any] = {}
    c_means: list[float] = []
    r_means: list[float] = []
    c_corrs: list[float] = []
    r_corrs: list[float] = []
    c_pfs: list[float] = []
    r_pfs: list[float] = []
    c_rugs: list[float] = []
    r_rugs: list[float] = []
    c_positive: list[float] = []
    r_positive: list[float] = []
    eligible_h = 0
    total_rows = 0
    unique_launches: set[str] = set()
    for h, rows in sorted(rows_by_horizon.items()):
        rows = list(rows)
        total_rows += len(rows)
        for row in rows:
            try:
                unique_launches.add(str(row["address"]))
            except Exception:
                pass
        cn = candidate.horizons.get(int(h))
        rn = reference.horizons.get(int(h)) if reference is not None else None
        if cn is None or rn is None:
            continue
        c = _one_model(cn, rows, feature_fn)
        r = _one_model(rn, rows, feature_fn)
        horizons[str(h)] = {"candidate": c, "reference": r}
        if not (c.get("eligible") and r.get("eligible")):
            continue
        eligible_h += 1
        c_means.append(float(c["top_quartile_mean_net_return_pct"]))
        r_means.append(float(r["top_quartile_mean_net_return_pct"]))
        if c.get("economic_correlation") is not None:
            c_corrs.append(float(c["economic_correlation"]))
        if r.get("economic_correlation") is not None:
            r_corrs.append(float(r["economic_correlation"]))
        if c.get("top_quartile_profit_factor") is not None:
            c_pfs.append(min(20.0, float(c["top_quartile_profit_factor"])))
        if r.get("top_quartile_profit_factor") is not None:
            r_pfs.append(min(20.0, float(r["top_quartile_profit_factor"])))
        c_rugs.append(float(c["top_quartile_rug_rate_pct"]))
        r_rugs.append(float(r["top_quartile_rug_rate_pct"]))
        c_positive.append(float(c["top_quartile_positive_rate_pct"]))
        r_positive.append(float(r["top_quartile_positive_rate_pct"]))

    def mean(v: list[float]) -> float | None:
        return None if not v else sum(v) / len(v)

    cm, rm = mean(c_means), mean(r_means)
    cc, rc = mean(c_corrs), mean(r_corrs)
    cp, rp = mean(c_pfs), mean(r_pfs)
    cr, rr = mean(c_rugs), mean(r_rugs)
    cpos, rpos = mean(c_positive), mean(r_positive)
    # Economic score used for learning diagnostics and forward ranking.
    def alignment_score(top_ret: float | None, corr: float | None, pf: float | None, rug: float | None) -> float | None:
        if top_ret is None:
            return None
        raw = (
            _clamp(top_ret / 12.0, -2.0, 2.0)
            + 0.60 * _clamp(corr or 0.0, -1.0, 1.0)
            + 0.20 * _clamp(((pf or 0.0) - 1.0) / 2.0, -1.0, 2.0)
            - 0.30 * _clamp((rug or 0.0) / 25.0, 0.0, 2.0)
        )
        return round(_clamp(raw, -3.0, 3.0), 5)

    cs = alignment_score(cm, cc, cp, cr)
    rs = alignment_score(rm, rc, rp, rr)
    return {
        "method": "shadow_only economic ranking on settled horizon returns after 2pct round-trip cost; no execution mutation",
        "eligible": eligible_h >= 3 and len(unique_launches) >= MIN_UNIQUE_LAUNCHES,
        "eligible_horizons": eligible_h,
        "unique_launches": len(unique_launches),
        "minimum_unique_launches": MIN_UNIQUE_LAUNCHES,
        "rows_seen_across_horizons": total_rows,
        "candidate_top_quartile_mean_net_return_pct": None if cm is None else round(cm, 4),
        "reference_top_quartile_mean_net_return_pct": None if rm is None else round(rm, 4),
        "top_return_delta_pct": None if cm is None or rm is None else round(cm - rm, 4),
        "candidate_economic_correlation": None if cc is None else round(cc, 5),
        "reference_economic_correlation": None if rc is None else round(rc, 5),
        "correlation_delta": None if cc is None or rc is None else round(cc - rc, 5),
        "candidate_top_quartile_profit_factor": None if cp is None else round(cp, 4),
        "reference_top_quartile_profit_factor": None if rp is None else round(rp, 4),
        "candidate_top_quartile_rug_rate_pct": None if cr is None else round(cr, 3),
        "reference_top_quartile_rug_rate_pct": None if rr is None else round(rr, 3),
        "candidate_top_quartile_positive_rate_pct": None if cpos is None else round(cpos, 3),
        "reference_top_quartile_positive_rate_pct": None if rpos is None else round(rpos, 3),
        "rug_rate_delta_pct": None if cr is None or rr is None else round(cr - rr, 3),
        "candidate_alignment_score": cs,
        "reference_alignment_score": rs,
        "alignment_score_delta": None if cs is None or rs is None else round(cs - rs, 5),
        "horizons": horizons,
        "execution_authority_pct": 0,
    }


def economic_rank_key(summary: dict[str, Any]) -> tuple[float, ...]:
    """Primary ranking key for the learning population; lower is better.

    v0.8.22 deliberately puts realized after-cost forward economics ahead of
    classification metrics. This is lexicographic rather than a tiny weighted
    bonus, so a beautifully calibrated but money-losing model cannot win the
    population selection step. It remains shadow-only and grants no execution
    authority.
    """
    if not summary.get("eligible"):
        return (1.0, 1.0, 999.0, 999.0, 999.0, 999.0, 999.0)
    top_ret = float(summary.get("candidate_top_quartile_mean_net_return_pct") or 0.0)
    top_pf = float(summary.get("candidate_top_quartile_profit_factor") or 0.0)
    top_pos = float(summary.get("candidate_top_quartile_positive_rate_pct") or 0.0)
    top_rug = float(summary.get("candidate_top_quartile_rug_rate_pct") or 100.0)
    corr = float(summary.get("candidate_economic_correlation") or 0.0)
    # First prefer models with positive mean AND PF>1. Then maximize return/PF,
    # minimize rug exposure, maximize positive rate and finally correlation.
    return (
        0.0 if top_ret > 0.0 else 1.0,
        0.0 if top_pf > 1.0 else 1.0,
        -round(_clamp(top_ret, -100.0, 100.0), 6),
        -round(_clamp(top_pf, 0.0, 20.0), 6),
        round(_clamp(top_rug, 0.0, 100.0), 6),
        -round(_clamp(top_pos, 0.0, 100.0), 6),
        -round(_clamp(corr, -1.0, 1.0), 6),
    )


def economic_meta_adjustment(summary: dict[str, Any]) -> float:
    """Compatibility shim. v0.8.22 no longer uses a tiny economic adjustment."""
    if not summary.get("eligible"):
        return 0.0
    key = economic_rank_key(summary)
    if key[0] >= 1.0:
        return 0.02
    return -0.02 if key[1] == 0.0 else 0.0


def promotion_rules(summary: dict[str, Any]) -> list[tuple[bool, str]]:
    """Absolute + relative economic guardrails for Champion promotion.

    v0.8.19/20 only required a candidate not to be materially worse than the
    existing reference. That allowed a new Champion to be promoted when both
    candidate and reference had objectively awful economics. v0.8.22 keeps the absolute economic gate and
    makes that economic state the primary learning objective, then applies the relative
    comparison. This is still shadow-only and never changes execution authority.
    """
    if not summary.get("eligible"):
        return [(False, "profit_alignment_insufficient_independent_forward_evidence")]
    dret = float(summary.get("top_return_delta_pct") or 0.0)
    dcorr = float(summary.get("correlation_delta") or 0.0)
    drug = float(summary.get("rug_rate_delta_pct") or 0.0)
    top_ret = float(summary.get("candidate_top_quartile_mean_net_return_pct") or 0.0)
    top_pf = float(summary.get("candidate_top_quartile_profit_factor") or 0.0)
    top_rug = float(summary.get("candidate_top_quartile_rug_rate_pct") or 100.0)
    top_pos = float(summary.get("candidate_top_quartile_positive_rate_pct") or 0.0)
    cs = summary.get("candidate_alignment_score")
    rs = summary.get("reference_alignment_score")
    dscore = 0.0 if cs is None or rs is None else float(cs) - float(rs)
    return [
        (top_ret > 0.0, f"absolute_top_return={top_ret:.4f}>0"),
        (top_pf >= 2.0, f"absolute_top_pf={top_pf:.4f}>=2.0"),
        (top_pos >= 50.0, f"absolute_top_positive_rate={top_pos:.2f}>=50pct"),
        (top_rug <= 35.0, f"absolute_top_rug_rate={top_rug:.2f}<=35pct"),
        (dret >= 0.0, f"economic_top_return_delta={dret:.4f}>=0.0pct"),
        (dcorr >= -0.08, f"economic_correlation_delta={dcorr:.4f}>=-0.08"),
        (drug <= 5.0, f"top_quartile_rug_rate_delta={drug:.3f}<=5pct"),
        (dscore >= -0.10, f"profit_alignment_score_delta={dscore:.4f}>=-0.10"),
    ]

