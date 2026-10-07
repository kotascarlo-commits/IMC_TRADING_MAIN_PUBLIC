"""Coin-independent economic diagnostics. No metric grants execution authority.

Returns and costs use percentage points. A zero-loss sample has undefined PF,
never a numeric sentinel. The first decision per coin is fixed before returns
are examined; abstentions remain in the population and paired comparisons.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import random
from statistics import mean
from typing import Iterable
from .costs import net_return as after_cost_return


@dataclass(frozen=True)
class TradeEvidence:
    coin: str
    decision_ts: float
    outcome_ts: float | None
    gross_return_pct: float | None
    round_trip_cost_pct: float
    explorer_id: int | None = None
    specialist: str = "market"
    regime: str = "unknown"
    predicted_win_probability: float | None = None
    model_hash: str = ""
    recorded_at: float | None = None
    source: str = "REAL"
    action: str = "TRADE"


def finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def profit_factor(values: Iterable[float]) -> float | None:
    values = list(values)
    gains = sum(v for v in values if v > 0)
    losses = -sum(v for v in values if v < 0)
    return gains / losses if losses > 0 else None


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * fraction
    lower = math.floor(pos)
    upper = math.ceil(pos)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (pos - lower)


def unique_evidence(records: Iterable[TradeEvidence]) -> tuple[list[TradeEvidence], int]:
    # Stable arrival order breaks equal timestamp ties, never a return ranking.
    ordered = sorted(enumerate(records), key=lambda r: (r[1].decision_ts, r[0]))
    selected: dict[str, TradeEvidence] = {}
    duplicates = 0
    for _, row in ordered:
        if row.coin in selected:
            duplicates += 1
        else:
            selected[row.coin] = row
    return list(selected.values()), duplicates


def evidence_errors(row: TradeEvidence) -> list[str]:
    errors = []
    if not row.coin:
        errors.append("missing_coin")
    if row.action not in {"TRADE", "ABSTAIN", "VETO"}:
        errors.append("invalid_action")
    if not finite(row.decision_ts):
        errors.append("invalid_decision_time")
    if not finite(row.outcome_ts) or not finite(row.decision_ts) or row.outcome_ts <= row.decision_ts:
        errors.append("missing_or_invalid_outcome_time")
    if not finite(row.gross_return_pct) or row.gross_return_pct < -100:
        errors.append("missing_or_invalid_return")
    if not finite(row.round_trip_cost_pct) or row.round_trip_cost_pct <= 0:
        errors.append("missing_or_nonpositive_cost")
    if row.predicted_win_probability is not None and (
        not finite(row.predicted_win_probability) or not 0 <= row.predicted_win_probability <= 1
    ):
        errors.append("invalid_probability")
    if row.recorded_at is not None and (
        not finite(row.recorded_at) or not finite(row.outcome_ts) or row.recorded_at >= row.outcome_ts
    ):
        errors.append("decision_not_recorded_before_outcome")
    return errors


def net_return(row: TradeEvidence, stress: float = 1.0) -> float:
    return after_cost_return(float(row.gross_return_pct), stress * row.round_trip_cost_pct) if row.action == "TRADE" else 0.0


def _basic(rows: list[TradeEvidence]) -> dict:
    trades = [row for row in rows if row.action == "TRADE"]
    net = [net_return(row) for row in trades]
    stress = [net_return(row, 2.0) for row in trades]
    equity = high = 1.0
    max_dd = 0.0
    # Fixed one-percent starting-equity notional per independent coin. This is
    # an auditable diagnostic, not an inferred executable portfolio backtest.
    for value in net:
        equity += value / 100.0 * 0.01
        high = max(high, equity)
        max_dd = max(max_dd, (high - equity) / high * 100)
    losses = sum(v < 0 for v in net)
    worst = sorted(net)[:max(1, math.ceil(len(net) * .05))]
    return {
        "unique_coins": len(rows), "traded_coins": len(trades), "abstained_or_vetoed_coins": len(rows) - len(trades),
        "profit_factor": profit_factor(net), "oos_pf_after_cost": profit_factor(net),
        "cost_stress_pf": profit_factor(stress), "wins": sum(v > 0 for v in net), "losses": losses,
        "win_rate_pct": mean(v > 0 for v in net) * 100 if net else None,
        "expectancy_pct": mean(net) if net else None,
        "population_expectancy_pct": mean(net_return(row) for row in rows) if rows else None,
        "max_drawdown_pct": max_dd if net else None,
        "drawdown_method": "chronological_fixed_1pct_initial_equity_notional_per_coin",
        "tail_loss_cvar_5_pct": mean(worst) if net else None,
        "top_1_removal_pf": profit_factor(sorted(net, reverse=True)[1:]) if len(net) > 1 else None,
        "top_3_removal_pf": profit_factor(sorted(net, reverse=True)[3:]) if len(net) > 3 else None,
    }


def evaluate_trades(records: Iterable[TradeEvidence], *, bootstrap_samples: int = 1000, seed: int = 1007) -> dict:
    records = list(records)
    # Validate every input, including duplicates; a corrupt discarded row is
    # evidence of a broken cohort, not permission to hide the row.
    invalid = [(i, evidence_errors(row)) for i, row in enumerate(records) if evidence_errors(row)]
    valid = [row for row in records if not evidence_errors(row)]
    rows, duplicates = unique_evidence(valid)
    result = _basic(rows)
    result.update({
        "input_rows": len(records), "duplicate_rows_excluded": duplicates,
        "invalid_or_unresolved_rows": len(invalid), "invalid_reasons": invalid[:20],
        "complete": not invalid, "execution_authority_pct": 0,
        "selection_rule": "first_decision_per_exact_coin_before_examining_outcomes",
        "cost_method": "compounded_entry_exit_all_in_cost;stress=2x_cost",
        "model_hashes": sorted({row.model_hash for row in rows}),
    })
    trades = [row for row in rows if row.action == "TRADE"]
    rng = random.Random(seed)
    samples: list[float] = []
    undefined = 0
    for _ in range(bootstrap_samples if trades else 0):
        pf = profit_factor(net_return(rng.choice(trades)) for _ in trades)
        if pf is None:
            undefined += 1
            # Undefined is conservatively zero for a promotion lower bound.
            samples.append(0.0)
        else:
            samples.append(pf)
    result["bootstrap_pf_p10"] = percentile(samples, .10)
    result["bootstrap_samples"] = len(samples)
    result["bootstrap_undefined_samples"] = undefined
    calibrated = [row for row in rows if row.predicted_win_probability is not None]
    result["calibration_count"] = len(calibrated)
    result["brier_score"] = mean(
        (row.predicted_win_probability - (after_cost_return(float(row.gross_return_pct), row.round_trip_cost_pct) > 0)) ** 2
        for row in calibrated
    ) if calibrated else None
    ece = 0.0
    for i in range(10):
        bucket = [r for r in calibrated if min(9, int(r.predicted_win_probability * 10)) == i]
        if bucket:
            confidence = mean(r.predicted_win_probability for r in bucket)
            observed = mean(after_cost_return(float(r.gross_return_pct), r.round_trip_cost_pct) > 0 for r in bucket)
            ece += len(bucket) / len(calibrated) * abs(confidence - observed)
    result["calibration_ece"] = ece if calibrated else None
    result["regimes"] = {name: _basic([r for r in rows if r.regime == name]) for name in sorted({r.regime for r in rows})}
    result["specialists"] = {name: _basic([r for r in rows if r.specialist == name]) for name in sorted({r.specialist for r in rows})}
    # Fixed chronological thirds; these are sequential forward evaluation
    # blocks of a frozen model, not retraining folds or a substitute for them.
    result["chronological_forward_blocks"] = [
        _basic(rows[len(rows)*i//3:len(rows)*(i+1)//3]) for i in range(3)
    ]
    result["counterfactuals"] = {
        "prevented_bad_trades": sum(r.action != "TRADE" and after_cost_return(float(r.gross_return_pct), r.round_trip_cost_pct) < 0 for r in rows),
        "missed_good_trades": sum(r.action != "TRADE" and after_cost_return(float(r.gross_return_pct), r.round_trip_cost_pct) > 0 for r in rows),
        "false_positives": sum(r.action == "TRADE" and net_return(r) <= 0 for r in rows),
        "false_negatives": sum(r.action != "TRADE" and after_cost_return(float(r.gross_return_pct), r.round_trip_cost_pct) > 0 for r in rows),
    }
    return result


def paired_comparison(candidate: Iterable[TradeEvidence], champion: Iterable[TradeEvidence], *, seed: int = 1008, samples: int = 1000) -> dict:
    candidate, _ = unique_evidence(candidate)
    champion, _ = unique_evidence(champion)
    c = {r.coin: r for r in candidate}
    r = {row.coin: row for row in champion}
    same = set(c) == set(r) and bool(c)
    errors = any(evidence_errors(row) for row in candidate + champion)
    same_times = same and all(c[k].decision_ts == r[k].decision_ts and c[k].outcome_ts == r[k].outcome_ts for k in c)
    same_market = same and all(c[k].gross_return_pct == r[k].gross_return_pct and c[k].round_trip_cost_pct == r[k].round_trip_cost_pct for k in c)
    if not same or not same_times or not same_market or errors:
        return {"eligible": False, "reason": "comparison_requires_identical_complete_coin_time_cohort"}
    deltas = [net_return(c[k]) - net_return(r[k]) for k in sorted(c)]
    rng = random.Random(seed)
    distribution = [mean(rng.choice(deltas) for _ in deltas) for _ in range(samples)]
    return {"eligible": True, "paired_coins": len(deltas), "expectancy_improvement_pct": mean(deltas),
            "paired_bootstrap_improvement_p10": percentile(distribution, .1),
            "abstentions_included_as_zero_return": True}


def chronological_coin_split(records: Iterable[TradeEvidence], *, train_fraction: float = .60, validation_fraction: float = .20) -> dict[str, list[TradeEvidence]]:
    """Split whole coin lifetimes, then purge labels crossing a later boundary."""
    rows = list(records)
    if not 0 < train_fraction < 1 or not 0 < validation_fraction < 1 - train_fraction:
        raise ValueError("invalid_split_fractions")
    coins: dict[str, list[TradeEvidence]] = {}
    for row in rows:
        coins.setdefault(row.coin, []).append(row)
    ordered = sorted(coins, key=lambda k: (min(r.decision_ts for r in coins[k]), k))
    a, b = int(len(ordered) * train_fraction), int(len(ordered) * (train_fraction + validation_fraction))
    val_start = min((r.decision_ts for k in ordered[a:b] for r in coins[k]), default=float("inf"))
    test_start = min((r.decision_ts for k in ordered[b:] for r in coins[k]), default=float("inf"))
    result = {"train": [], "validation": [], "test": [], "purged": []}
    for keys, name, boundary in ((ordered[:a], "train", val_start), (ordered[a:b], "validation", test_start), (ordered[b:], "test", float("inf"))):
        for k in keys:
            for row in coins[k]:
                destination = name if finite(row.outcome_ts) and row.outcome_ts < boundary else "purged"
                result[destination].append(row)
    return result


def walk_forward_splits(records: Iterable[TradeEvidence], *, folds: int = 3) -> list[dict[str, list[TradeEvidence]]]:
    """Expanding research folds with whole-coin assignment and label purging."""
    rows = list(records)
    first: dict[str, float] = {}
    for row in rows:
        first[row.coin] = min(first.get(row.coin, row.decision_ts), row.decision_ts)
    ordered = sorted(first, key=lambda k: (first[k], k))
    output = []
    for i in range(folds):
        end = max(1, len(ordered) * (i + 2) // (folds + 1))
        selected = set(ordered[:end])
        output.append(chronological_coin_split([r for r in rows if r.coin in selected]))
    return output
