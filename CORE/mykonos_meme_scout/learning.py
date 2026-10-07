from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime
from statistics import mean
from typing import Any

from .models import MicroMetrics, TokenSnapshot
from .store import Store


# Model identity is deliberately decoupled from app releases. v0.3.4/v0.3.5/v0.3.6 do not
# change prediction logic, so it must continue the v0.3.3 model lineage instead
# of creating a third set of identical shadow models/predictions.
MODEL_VERSION = "0.3.3-first15-rug-shield-shadow-heuristic"
DEFAULT_HORIZONS = (1, 3, 5, 10, 15)


@dataclass(slots=True)
class Evidence:
    security: float
    liquidity: float
    flow: float
    structure: float
    micro: float
    rug_pressure: float
    uncertainty: float


@dataclass(slots=True)
class ShadowPrediction:
    family: str
    horizon_minutes: int
    p5: float
    p10: float
    p25: float
    p_rug: float
    p_stagnation: float
    uncertainty: float
    expected_utility: float
    reasons: list[str]


FAMILIES: dict[str, dict[str, float]] = {
    "security_first": {"security": .43, "liquidity": .22, "flow": .16, "structure": .09, "micro": .10},
    "flow_rocket": {"security": .17, "liquidity": .13, "flow": .34, "structure": .16, "micro": .20},
    "balanced": {"security": .28, "liquidity": .18, "flow": .23, "structure": .14, "micro": .17},
    "ai_learning_family": {"security": .25, "liquidity": .16, "flow": .27, "structure": .13, "micro": .19},
}


def _clamp(value: float, low: float = 0.01, high: float = 0.99) -> float:
    return min(high, max(low, value))


def _sigmoid(value: float) -> float:
    return 1 / (1 + math.exp(-value))


def evidence_from_snapshot(s: TokenSnapshot, micro: MicroMetrics | None = None) -> Evidence:
    micro = micro or MicroMetrics()
    sec = s.security
    safety_bits = [
        sec.mint_authority_disabled,
        sec.freeze_authority_disabled,
        None if sec.non_transferable is None else not sec.non_transferable,
        None if sec.transfer_hook_present is None else not sec.transfer_hook_present,
        None if sec.malicious_creator is None else not sec.malicious_creator,
    ]
    known = [float(v) for v in safety_bits if v is not None]
    security = mean(known) if known else 0.20
    if sec.top10_holder_pct is not None:
        security *= _clamp(1 - sec.top10_holder_pct / 70, 0, 1)
    if sec.creator_holder_pct is not None:
        security *= _clamp(1 - sec.creator_holder_pct / 15, 0, 1)

    liquidity_depth = _clamp(math.log10(max(s.liquidity_usd, 1) / 3_000) / 2, 0, 1)
    liquidity_ratio = _clamp(s.liquidity_ratio / .12, 0, 1) if s.market_cap_usd > 0 else .2
    liquidity = .60 * liquidity_depth + .40 * liquidity_ratio

    buy_strength = _clamp((s.buy_ratio - .40) / .35, 0, 1)
    volume_strength = _clamp(s.volume_rate_per_minute / 6_000, 0, 1)
    tx_strength = _clamp(s.transaction_rate_per_minute / 22, 0, 1)
    flow = .42 * buy_strength + .33 * volume_strength + .25 * tx_strength

    momentum = _clamp((s.price_change_5m_pct + 8) / 65, 0, 1)
    age_quality = _clamp(1 - max(0, s.age_minutes - 5) / 12, 0, 1)
    structure = .62 * momentum + .38 * age_quality

    if micro.has_previous:
        pv = micro.price_velocity_pct_per_minute or 0.0
        av = micro.activity_acceleration if micro.activity_acceleration is not None else 0.0
        br = micro.buy_ratio_change if micro.buy_ratio_change is not None else 0.0
        price_micro = _clamp((pv + 5) / 30, 0, 1)
        activity_micro = _clamp((av + .5) / 1.5, 0, 1)
        buy_micro = _clamp((br + .10) / .30, 0, 1)
        micro_score = .45 * price_micro + .35 * activity_micro + .20 * buy_micro
    else:
        micro_score = .35

    unknown_count = sum(value is None for value in (
        sec.mint_authority_disabled, sec.freeze_authority_disabled,
        sec.non_transferable, sec.transfer_hook_present, sec.top10_holder_pct,
    ))
    uncertainty = _clamp(.10 + unknown_count * .10 + (0 if sec.checked else .25)
                         + (0 if micro.has_previous else .12), .05, .92)
    rug_pressure = 1 - security
    if s.liquidity_ratio < .02:
        rug_pressure += .18
    if s.boost_amount > 500:
        rug_pressure += .06
    if sec.non_transferable is True or sec.transfer_hook_present is True:
        rug_pressure += .35
    return Evidence(security, liquidity, flow, structure, micro_score,
                    _clamp(rug_pressure), uncertainty)


class LearningFamily:
    """Shadow-only launch model family. It cannot place orders or promote itself."""

    def predict(self, snapshot: TokenSnapshot, horizon_minutes: int,
                micro: MicroMetrics | None = None) -> list[ShadowPrediction]:
        e = evidence_from_snapshot(snapshot, micro)
        predictions: list[ShadowPrediction] = []
        horizon_factor = {1: .78, 3: .90, 5: 1.00, 10: 1.08, 15: 1.13}.get(horizon_minutes, 1.0)
        for family, base_weights in FAMILIES.items():
            weights = dict(base_weights)
            reasons: list[str] = []
            if family == "ai_learning_family":
                if snapshot.age_minutes <= 3:
                    weights["security"] += .05
                    weights["structure"] -= .02
                    weights["flow"] -= .03
                    reasons.append("first3m_security_emphasis")
                if e.flow > .72 and e.micro > .65 and e.security > .68:
                    weights["flow"] += .04
                    weights["micro"] += .03
                    weights["liquidity"] -= .07
                    reasons.append("safe_micro_flow_acceleration")
            raw = sum(weights[name] * getattr(e, name) for name in weights)
            p_rug = _clamp(.72 * e.rug_pressure + .28 * e.uncertainty)
            opportunity = _sigmoid((raw - .55) * 5.6) * horizon_factor
            p5 = _clamp(opportunity * (1 - .62 * p_rug))
            p10 = _clamp(p5 * (.52 + .28 * e.flow + .10 * e.micro))
            p25 = _clamp(p10 * (.24 + .25 * e.structure + .16 * e.micro))
            p_stagnation = _clamp(1 - (.42 * e.flow + .33 * e.micro + .25 * e.structure))
            expected_utility = (
                p5 * .05 + p10 * .10 + p25 * .25
                - p_rug * .40 - p_stagnation * .025 - e.uncertainty * .05
            )
            reasons.extend([
                f"h={horizon_minutes}m", f"security={e.security:.2f}",
                f"liquidity={e.liquidity:.2f}", f"flow={e.flow:.2f}",
                f"structure={e.structure:.2f}", f"micro={e.micro:.2f}",
            ])
            predictions.append(ShadowPrediction(
                family, horizon_minutes, p5, p10, p25, p_rug, p_stagnation,
                e.uncertainty, expected_utility, reasons,
            ))
        return predictions


class LearningLab:
    def __init__(self, store: Store, horizons: tuple[int, ...] = DEFAULT_HORIZONS,
                 prediction_interval_seconds: int = 60, maximum_age_minutes: int = 15):
        self.store = store
        self.horizons = tuple(sorted(set(horizons)))
        self.prediction_interval_seconds = prediction_interval_seconds
        self.maximum_age_minutes = maximum_age_minutes
        if not getattr(self.store, "_learning_models_registered", False):
            for family in FAMILIES:
                self.store.register_model(family, MODEL_VERSION, {**FAMILIES[family], "horizons": self.horizons})
            self.store._learning_models_registered = True

    def observe(self, snapshot: TokenSnapshot, micro: MicroMetrics | None = None) -> list[ShadowPrediction]:
        if snapshot.age_minutes > self.maximum_age_minutes:
            return []
        last = self.store.latest_learning_prediction_at(snapshot.address, MODEL_VERSION)
        if last:
            last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
            now = datetime.fromisoformat(snapshot.captured_at.replace("Z", "+00:00"))
            if (now - last_dt).total_seconds() < self.prediction_interval_seconds:
                return []
        all_predictions: list[ShadowPrediction] = []
        family = LearningFamily()
        adaptive_bundle = champion_bundle = None
        try:
            from .machine_intelligence import (
                ML_ADAPTIVE_FAMILY, ML_CHAMPION_FAMILY,
                load_machine_models_for_store,
            )
            adaptive_bundle, champion_bundle = load_machine_models_for_store(self.store)
            for bundle, family_name in (
                (adaptive_bundle, ML_ADAPTIVE_FAMILY),
                (champion_bundle, ML_CHAMPION_FAMILY),
            ):
                if bundle is not None:
                    key = f"_ml_registered_{family_name}_{bundle.model_version}"
                    if not getattr(self.store, key, False):
                        self.store.register_model(family_name, bundle.model_version, {
                            "authority_pct": 0, "trained_at": bundle.trained_at,
                            "horizons": sorted(bundle.horizons),
                            "generation": bundle.generation, "continual_learning": True,
                        })
                        setattr(self.store, key, True)
        except Exception:
            adaptive_bundle = champion_bundle = None

        for horizon in self.horizons:
            predictions = family.predict(snapshot, horizon, micro)
            for prediction in predictions:
                self.store.record_learning_prediction(
                    snapshot, prediction, horizon, MODEL_VERSION, commit=False
                )
            all_predictions.extend(predictions)
            if adaptive_bundle is not None:
                adaptive_prediction = adaptive_bundle.predict(
                    snapshot, horizon, micro, family_override=ML_ADAPTIVE_FAMILY
                )
                if adaptive_prediction is not None:
                    self.store.record_learning_prediction(
                        snapshot, adaptive_prediction, horizon, adaptive_bundle.model_version, commit=False
                    )
                    all_predictions.append(adaptive_prediction)
            if champion_bundle is not None:
                champion_prediction = champion_bundle.predict(
                    snapshot, horizon, micro, family_override=ML_CHAMPION_FAMILY
                )
                if champion_prediction is not None:
                    self.store.record_learning_prediction(
                        snapshot, champion_prediction, horizon, champion_bundle.model_version, commit=False
                    )
                    all_predictions.append(champion_prediction)
        if all_predictions:
            self.store.conn.commit()
        return all_predictions

    def settle(self) -> int:
        return self.store.label_and_reward_learning_predictions()

    def leaderboard(self, paths: int = 1000, seed: int = 20260902) -> list[dict[str, Any]]:
        rows = self.store.learning_reward_rows()
        grouped: dict[str, list[float]] = {}
        for row in rows:
            grouped.setdefault(str(row["model_family"]), []).append(float(row["total_reward"]))
        rng = random.Random(seed)
        board: list[dict[str, Any]] = []
        for family in FAMILIES:
            rewards = grouped.get(family, [])
            if not rewards:
                board.append({"family": family, "resolved": 0, "mean_reward": None,
                              "stress_p05": None, "positive_path_rate": None})
                continue
            path_scores = []
            for _ in range(paths):
                sample = [rng.choice(rewards) for _ in range(len(rewards))]
                execution_penalty = rng.uniform(0.0, .10)
                missing_data_penalty = rng.uniform(0.0, .07)
                path_scores.append(mean(sample) - execution_penalty - missing_data_penalty)
            ordered = sorted(path_scores)
            board.append({
                "family": family,
                "resolved": len(rewards),
                "mean_reward": round(mean(rewards), 4),
                "stress_p05": round(ordered[max(0, int(paths * .05) - 1)], 4),
                "positive_path_rate": round(sum(v > 0 for v in path_scores) / paths, 4),
            })
        return sorted(board, key=lambda x: (x["stress_p05"] is None,
                                            -(x["stress_p05"] if x["stress_p05"] is not None else -999)))


def cookie_components(prediction: dict[str, Any], outcome: dict[str, Any]) -> dict[str, float]:
    y10, yrug = float(outcome["hit_10"]), float(outcome["rug"])
    p10, prug = float(prediction["p10"]), float(prediction["p_rug"])
    calibration = .5 * (1 - (p10 - y10) ** 2) + .5 * (1 - (prug - yrug) ** 2)
    wants_trade = p10 >= .45 and prug <= .35
    if wants_trade and y10:
        opportunity = 1.0
    elif wants_trade and not y10:
        opportunity = -1.0
    elif not wants_trade and y10:
        opportunity = -.60
    else:
        opportunity = .15
    rug_safety = 1.0 if (yrug and prug >= .50) else (-1.0 if yrug and wants_trade else .15)
    realized = max(-1.0, min(1.0, float(outcome["return_pct"]) / 15)) if wants_trade else 0.0
    drawdown = max(0.0, min(1.5, -float(outcome["min_return_pct"]) / 12)) if wants_trade else 0.0
    return {
        "calibration": calibration,
        "opportunity": opportunity,
        "rug_safety": rug_safety,
        "realized": realized,
        "drawdown_penalty": drawdown,
        "total": .34 * (2 * calibration - 1) + .27 * opportunity + .24 * rug_safety
                 + .15 * realized - .22 * drawdown,
    }
