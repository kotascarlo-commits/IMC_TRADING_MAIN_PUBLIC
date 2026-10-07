from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .learning import LearningFamily, ShadowPrediction, evidence_from_snapshot
from .models import MicroMetrics, TokenSnapshot
from .profit_alignment import economic_rank_key, profit_alignment_summary, promotion_rules as profit_promotion_rules
from .causal_learning import filter_learning_rows

ML_LINEAGE_VERSION = "0.4.3-profit-first-neural"
ML_ADAPTIVE_FAMILY = "neural_adaptive_v041"
ML_CHAMPION_FAMILY = "neural_champion_v041"
# Backward-compatible public name used by older tests/imports.
ML_MODEL_FAMILY = ML_ADAPTIVE_FAMILY
ML_MODEL_VERSION = ML_LINEAGE_VERSION
ML_MODEL_FILENAME = "neural-adaptive-v0.4.3.json"
ML_CHAMPION_FILENAME = "neural-champion-v0.4.3.json"
ML_STATE_FILENAME = "neural-adaptive-state-v0.4.3.json"
ML_CANDIDATE_FILENAME = "neural-candidate-v0.4.3.json"
OUTPUT_NAMES = ("p5", "p10", "p25", "p_rug", "p_stagnation")
FEATURE_NAMES = (
    "security", "liquidity", "flow", "structure", "micro",
    "uncertainty", "heur_p5", "heur_p10", "heur_p25", "heur_p_rug",
    "heur_p_stagnation", "heur_expected_utility", "log10_entry_liquidity",
)
HORIZONS = (1, 3, 5, 10, 15)


def _require_real_training_domain(store: Any) -> None:
    """Fail closed if a simulation store is passed to the real champion trainer."""
    if getattr(store, "data_domain", "REAL_PAPER") != "REAL_PAPER":
        raise ValueError(
            "Simulation data is research-only and cannot train or promote the real neural champion"
        )


def _clamp(value: float, low: float = 1e-5, high: float = 1 - 1e-5) -> float:
    return min(high, max(low, float(value)))


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-min(value, 60.0))
        return 1.0 / (1.0 + z)
    z = math.exp(max(value, -60.0))
    return z / (1.0 + z)


def _extract_reason_number(reasons_json: str, key: str, default: float = 0.0) -> float:
    try:
        reasons = json.loads(reasons_json or "[]")
    except Exception:
        reasons = []
    prefix = f"{key}="
    for item in reasons:
        text = str(item)
        if text.startswith(prefix):
            try:
                return float(text[len(prefix):])
            except ValueError:
                return default
    return default


def training_features(row: Any) -> list[float]:
    return [
        _extract_reason_number(row["reasons_json"], "security", .2),
        _extract_reason_number(row["reasons_json"], "liquidity", .2),
        _extract_reason_number(row["reasons_json"], "flow", .2),
        _extract_reason_number(row["reasons_json"], "structure", .2),
        _extract_reason_number(row["reasons_json"], "micro", .35),
        float(row["uncertainty"]),
        float(row["p5"]),
        float(row["p10"]),
        float(row["p25"]),
        float(row["p_rug"]),
        float(row["p_stagnation"]),
        float(row["expected_utility"]),
        math.log10(max(float(row["entry_liquidity"]), 1.0)),
    ]


def runtime_features(snapshot: TokenSnapshot, horizon_minutes: int,
                     micro: MicroMetrics | None = None) -> list[float]:
    evidence = evidence_from_snapshot(snapshot, micro)
    baseline = next(
        p for p in LearningFamily().predict(snapshot, horizon_minutes, micro)
        if p.family == "balanced"
    )
    return [
        evidence.security, evidence.liquidity, evidence.flow, evidence.structure, evidence.micro,
        evidence.uncertainty, baseline.p5, baseline.p10, baseline.p25, baseline.p_rug,
        baseline.p_stagnation, baseline.expected_utility,
        math.log10(max(snapshot.liquidity_usd, 1.0)),
    ]


def _labels(row: Any) -> list[int]:
    return [
        int(row["hit_5"]), int(row["hit_10"]), int(row["hit_25"]),
        int(row["rug"]), int(row["stagnant"]),
    ]


def _target_confidence(row: Any, output_index: int) -> float:
    """Confidence in legacy labels without mutating immutable outcomes.

    Before v0.8.14 the `rug` bit also fired on a >=40% price crash. When that
    happens we cannot know from the immutable outcome row alone whether the
    cause was a structural rug or only a severe dump. Such ambiguous positives
    remain visible but get only 15% gradient weight for p_rug. Structural-certain
    positives (rug=1 with final return above -40%) keep full weight.
    """
    if output_index != 3 or int(row["rug"]) != 1:
        return 1.0
    try:
        final_return = float(row["return_pct"])
    except Exception:
        return 1.0
    return 0.15 if final_return <= -40.0 else 1.0


def _heuristic(row: Any) -> list[float]:
    return [
        float(row["p5"]), float(row["p10"]), float(row["p25"]),
        float(row["p_rug"]), float(row["p_stagnation"]),
    ]


@dataclass(slots=True)
class HorizonNetwork:
    horizon_minutes: int
    means: list[float]
    stds: list[float]
    w1: list[list[float]]
    b1: list[float]
    w2: list[list[float]]
    b2: list[float]
    metrics: dict[str, Any]
    train_rows: int
    validation_rows: int
    training_prevalence: list[float]

    def _standardize(self, features: list[float]) -> list[float]:
        return [
            (float(value) - self.means[i]) / self.stds[i]
            for i, value in enumerate(features)
        ]

    def predict(self, features: list[float]) -> list[float]:
        x = self._standardize(features)
        hidden = [
            math.tanh(self.b1[j] + sum(self.w1[j][i] * x[i] for i in range(len(x))))
            for j in range(len(self.w1))
        ]
        return [
            _sigmoid(self.b2[k] + sum(self.w2[k][j] * hidden[j] for j in range(len(hidden))))
            for k in range(len(self.w2))
        ]

    def as_dict(self) -> dict[str, Any]:
        return {
            "horizon_minutes": self.horizon_minutes,
            "means": self.means,
            "stds": self.stds,
            "w1": self.w1,
            "b1": self.b1,
            "w2": self.w2,
            "b2": self.b2,
            "metrics": self.metrics,
            "train_rows": self.train_rows,
            "validation_rows": self.validation_rows,
            "training_prevalence": self.training_prevalence,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "HorizonNetwork":
        prevalence = raw.get("training_prevalence")
        if prevalence is None:
            metrics = raw.get("metrics") or {}
            prevalence = [float((metrics.get(name) or {}).get("prevalence") or 0.0) for name in OUTPUT_NAMES]
        return cls(
            horizon_minutes=int(raw["horizon_minutes"]),
            means=[float(v) for v in raw["means"]],
            stds=[float(v) for v in raw["stds"]],
            w1=[[float(v) for v in row] for row in raw["w1"]],
            b1=[float(v) for v in raw["b1"]],
            w2=[[float(v) for v in row] for row in raw["w2"]],
            b2=[float(v) for v in raw["b2"]],
            metrics=dict(raw.get("metrics") or {}),
            train_rows=int(raw.get("train_rows") or 0),
            validation_rows=int(raw.get("validation_rows") or 0),
            training_prevalence=[float(v) for v in prevalence],
        )


@dataclass(slots=True)
class MachineShadowBundle:
    trained_at: str
    source_family: str
    authority_pct: float
    feature_names: tuple[str, ...]
    horizons: dict[int, HorizonNetwork]
    training_config: dict[str, Any]
    model_family: str = ML_ADAPTIVE_FAMILY
    model_version: str = ""
    generation: int = 1
    parent_generation: int | None = None
    training_anchor_prediction_id: int = 0

    def __post_init__(self) -> None:
        if not self.model_version:
            self.model_version = generation_version(self.generation, self.model_family)
        # v0.4.3 is deliberately shadow-only. A model file cannot opt itself in.
        self.authority_pct = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_family": self.model_family,
            "model_version": self.model_version,
            "lineage_version": ML_LINEAGE_VERSION,
            "generation": self.generation,
            "parent_generation": self.parent_generation,
            "training_anchor_prediction_id": self.training_anchor_prediction_id,
            "trained_at": self.trained_at,
            "source_family": self.source_family,
            "authority_pct": 0.0,
            "feature_names": list(self.feature_names),
            "training_config": self.training_config,
            "horizons": {str(k): v.as_dict() for k, v in self.horizons.items()},
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "MachineShadowBundle":
        if raw.get("lineage_version") != ML_LINEAGE_VERSION:
            raise ValueError(f"Unsupported neural model lineage: {raw.get('lineage_version')}")
        authority = float(raw.get("authority_pct") or 0.0)
        if authority != 0.0:
            raise ValueError("v0.4.3 neural models must have zero execution authority")
        return cls(
            trained_at=str(raw["trained_at"]),
            source_family=str(raw.get("source_family") or "balanced"),
            authority_pct=0.0,
            feature_names=tuple(raw.get("feature_names") or FEATURE_NAMES),
            horizons={int(k): HorizonNetwork.from_dict(v) for k, v in (raw.get("horizons") or {}).items()},
            training_config=dict(raw.get("training_config") or {}),
            model_family=str(raw.get("model_family") or ML_ADAPTIVE_FAMILY),
            model_version=str(raw.get("model_version") or ""),
            generation=int(raw.get("generation") or 1),
            parent_generation=(None if raw.get("parent_generation") is None else int(raw["parent_generation"])),
            training_anchor_prediction_id=int(raw.get("training_anchor_prediction_id") or 0),
        )

    @classmethod
    def load(cls, path: Path) -> "MachineShadowBundle":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.as_dict(), indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)

    def predict(self, snapshot: TokenSnapshot, horizon_minutes: int,
                micro: MicroMetrics | None = None, *, family_override: str | None = None) -> ShadowPrediction | None:
        network = self.horizons.get(int(horizon_minutes))
        if network is None:
            return None
        raw = network.predict(runtime_features(snapshot, horizon_minutes, micro))
        p5 = max(raw[0], raw[1], raw[2])
        p25 = min(raw[0], raw[1], raw[2])
        p10 = sum(raw[:3]) - p5 - p25
        p_rug = _clamp(raw[3])
        p_stagnation = _clamp(raw[4])
        expected_utility = (
            p5 * .05 + p10 * .10 + p25 * .25
            - p_rug * .40 - p_stagnation * .025
        )
        family = family_override or self.model_family
        return ShadowPrediction(
            family=family,
            horizon_minutes=horizon_minutes,
            p5=_clamp(p5), p10=_clamp(p10), p25=_clamp(p25),
            p_rug=p_rug, p_stagnation=p_stagnation,
            uncertainty=_clamp((p_rug + p_stagnation) / 2, .01, .99),
            expected_utility=expected_utility,
            reasons=[
                "continual_neural_shadow_only", f"h={horizon_minutes}m",
                f"generation={self.generation}", f"trained_at={self.trained_at}",
                "authority_pct=0",
            ],
        )


def generation_version(generation: int, family: str = ML_ADAPTIVE_FAMILY) -> str:
    role = "adaptive" if family == ML_ADAPTIVE_FAMILY else "champion"
    return f"0.4.3-neural-{role}-g{int(generation):06d}"


def _means_stds(rows: list[list[float]]) -> tuple[list[float], list[float]]:
    n = len(rows)
    means = [sum(row[i] for row in rows) / n for i in range(len(rows[0]))]
    stds: list[float] = []
    for i, avg in enumerate(means):
        var = sum((row[i] - avg) ** 2 for row in rows) / max(1, n - 1)
        stds.append(max(math.sqrt(var), 1e-6))
    return means, stds


def _launch_balanced_weights(groups: list[str] | None, n: int) -> list[float]:
    """Give every token/launch the same total evidence weight.

    Repeated snapshots/predictions from one launch remain useful for path learning,
    but they must not masquerade as independent launches. We normalize weights so
    the average row weight stays close to 1.0 while every launch contributes the
    same total mass.
    """
    if not groups or len(groups) != n or n <= 0:
        return [1.0] * n
    counts: dict[str, int] = {}
    for group in groups:
        counts[str(group)] = counts.get(str(group), 0) + 1
    unique = max(1, len(counts))
    return [n / (unique * counts[str(group)]) for group in groups]


def _recent_unique_launch_rows(rows: list[Any], maximum: int | None = None) -> list[Any]:
    """Return one most-recent row per token, preserving chronological order."""
    chosen: dict[str, Any] = {}
    for row in rows:
        chosen[str(row["address"])] = row
    values = sorted(chosen.values(), key=lambda row: int(row["id"]))
    if maximum is not None:
        values = values[-max(1, int(maximum)):]
    return values


def _primary_learning_regime(row: Any) -> str:
    """Compact outcome bucket used only to balance learning exposure."""
    labels = _labels(row)
    try:
        final_return = float(row["return_pct"] or 0.0)
        minimum_return = float(row["min_return_pct"] or 0.0)
    except Exception:
        final_return = minimum_return = 0.0
    if labels[3]:
        return "rug"
    if final_return <= -20.0 or minimum_return <= -25.0:
        return "severe_dump"
    if labels[2]:
        return "rocket25"
    if labels[1]:
        return "rocket10"
    if labels[0]:
        return "rocket5"
    if labels[4]:
        return "stagnation"
    return "normal"


def _regime_balanced_weights(rows: list[Any]) -> list[float]:
    """Bounded inverse-frequency weights so common regimes cannot dominate."""
    if not rows:
        return []
    regimes = [_primary_learning_regime(row) for row in rows]
    counts: dict[str, int] = {}
    for regime in regimes:
        counts[regime] = counts.get(regime, 0) + 1
    maximum = max(counts.values())
    raw = [min(1.80, max(0.65, (maximum / counts[r]) ** 0.5)) for r in regimes]
    avg = sum(raw) / len(raw)
    return [w / max(avg, 1e-12) for w in raw]


def _row_difficulty(row: Any) -> float:
    """Difficulty of a row for hard-example mining, without using model secrets."""
    ys = _labels(row)
    ps = [_clamp(v) for v in _heuristic(row)]
    mean_error = sum(abs(p - y) for p, y in zip(ps, ys)) / len(ys)
    confident_error = max(abs(p - y) * (0.5 + abs(p - 0.5)) for p, y in zip(ps, ys))
    try:
        uncertainty = float(row["uncertainty"] or 0.0)
    except Exception:
        uncertainty = 0.0
    # Moderate uncertainty is informative; extreme uncertainty is down-weighted
    # because it is more likely to be missing-data noise than a hard example.
    uncertainty_term = min(0.12, max(0.0, uncertainty) * 0.18)
    noise_guard = 0.82 if uncertainty >= 0.80 else 1.0
    return noise_guard * min(1.0, 0.65 * mean_error + 0.35 * confident_error + uncertainty_term)


def _hard_example_weights(rows: list[Any], strength: float = 1.0) -> list[float]:
    if not rows:
        return []
    strength = min(1.45, max(0.55, float(strength)))
    raw = [min(2.15, max(0.68, 0.72 + strength * 1.30 * _row_difficulty(row))) for row in rows]
    avg = sum(raw) / len(raw)
    return [w / max(avg, 1e-12) for w in raw]


def _uncertainty_priority(row: Any) -> float:
    """Information value proxy for active-learning style replay.

    Rows are useful when the baseline is internally conflicted or moderately
    uncertain. Extremely uncertain rows are guarded because those are often
    provider/missing-data noise rather than informative boundary cases.
    """
    ps = [_clamp(v) for v in _heuristic(row)]
    directional = ps[:3]
    boundary = sum(1.0 - min(1.0, abs(p - .5) * 2.0) for p in directional) / 3.0
    spread = min(1.0, (max(directional) - min(directional)) * 2.0)
    try:
        raw_u = max(0.0, min(1.0, float(row["uncertainty"] or 0.0)))
    except Exception:
        raw_u = 0.0
    moderate_u = 1.0 - min(1.0, abs(raw_u - .45) / .45)
    noise_guard = .55 if raw_u >= .88 else 1.0
    return noise_guard * min(1.0, .48 * boundary + .27 * spread + .25 * moderate_u)


def _uncertainty_weights(rows: list[Any], strength: float = 1.0) -> list[float]:
    if not rows:
        return []
    strength = min(1.50, max(0.40, float(strength)))
    raw = [min(1.95, max(.72, .76 + 1.05 * strength * _uncertainty_priority(row))) for row in rows]
    avg = sum(raw) / len(raw)
    return [w / max(avg, 1e-12) for w in raw]


def _rare_regime_weights(rows: list[Any], strength: float = 1.0) -> list[float]:
    if not rows:
        return []
    regimes = [_primary_learning_regime(row) for row in rows]
    counts: dict[str, int] = {}
    for regime in regimes:
        counts[regime] = counts.get(regime, 0) + 1
    maximum = max(counts.values())
    strength = min(1.35, max(.65, float(strength)))
    raw = [min(2.30, max(.55, (maximum / counts[r]) ** (0.50 * strength))) for r in regimes]
    avg = sum(raw) / len(raw)
    return [w / max(avg, 1e-12) for w in raw]


def _economic_weights(rows: list[Any], mode: str = "balanced_profit") -> list[float]:
    """Outcome-sensitive weights for the profit-first learning families.

    The labels stay unchanged. We only change how strongly already-settled
    outcomes teach the shadow learner. Three economic specialists deliberately
    disagree: balanced profit seeking, winner/profit-capture emphasis and
    downside/rug avoidance. This improves real diversity instead of cloning the
    same probability model with different random seeds.
    """
    if not rows:
        return []
    raw: list[float] = []
    for row in rows:
        try:
            ret = float(row["return_pct"] or 0.0) - 2.0
        except Exception:
            ret = -2.0
        ret = max(-60.0, min(100.0, ret))
        try:
            rug = bool(int(row["rug"] or 0))
        except Exception:
            rug = False
        try:
            stagnant = bool(int(row["stagnant"] or 0))
        except Exception:
            stagnant = False

        pos = max(0.0, ret)
        neg = max(0.0, -ret)
        if mode == "profit_capture":
            # Teach the network hardest from actual outsized winners while still
            # preserving enough negative evidence to avoid pure rocket chasing.
            w = 0.35 + min(3.20, pos / 12.0) + min(1.00, neg / 35.0)
            if ret >= 10.0:
                w *= 1.25
            if rug:
                w *= 1.10
        elif mode == "loss_avoider":
            # Deliberately pessimistic specialist: severe losses/rugs dominate
            # its gradient so the population has a real downside-control voice.
            w = 0.40 + min(0.90, pos / 30.0) + min(3.40, neg / 10.0)
            if rug:
                w *= 1.45
        else:
            # Balanced economic learner: both large winners and large losers are
            # more informative than near-flat outcomes.
            w = 0.45 + min(2.40, pos / 15.0) + min(2.20, neg / 16.0)
            if rug:
                w *= 1.30
        if stagnant and abs(ret) < 5.0:
            w *= 0.60
        raw.append(min(4.50, max(0.25, w)))
    avg = sum(raw) / len(raw)
    return [w / max(avg, 1e-12) for w in raw]


def _combined_training_weights(rows: list[Any], profile: str = "balanced") -> list[float]:
    groups = [str(row["address"]) for row in rows]
    launch = _launch_balanced_weights(groups, len(rows))
    economic = [1.0] * len(rows)
    if profile == "rare_regime":
        regime = _rare_regime_weights(rows, 1.30)
        hard_strength, uncertainty_strength = .90, .80
    elif profile == "uncertainty":
        regime = _regime_balanced_weights(rows)
        hard_strength, uncertainty_strength = .90, 1.45
    elif profile == "hardcase":
        regime = _regime_balanced_weights(rows)
        hard_strength, uncertainty_strength = 1.35, .95
    elif profile == "profit_seeking":
        regime = _regime_balanced_weights(rows)
        hard_strength, uncertainty_strength = .80, .62
        economic = _economic_weights(rows, "balanced_profit")
    elif profile == "profit_capture":
        regime = _regime_balanced_weights(rows)
        hard_strength, uncertainty_strength = .68, .52
        economic = _economic_weights(rows, "profit_capture")
    elif profile == "loss_avoider":
        regime = _regime_balanced_weights(rows)
        hard_strength, uncertainty_strength = 1.05, .48
        economic = _economic_weights(rows, "loss_avoider")
    elif profile == "stable":
        regime = _regime_balanced_weights(rows)
        hard_strength, uncertainty_strength = .72, .55
    else:
        regime = _regime_balanced_weights(rows)
        hard_strength, uncertainty_strength = 1.00, 1.00
    hard = _hard_example_weights(rows, hard_strength)
    uncertainty = _uncertainty_weights(rows, uncertainty_strength)
    raw = [min(3.25, max(0.22, a*b*c*d*e)) for a, b, c, d, e in zip(launch, regime, hard, uncertainty, economic)]
    avg = sum(raw) / max(1, len(raw))
    return [w / max(avg, 1e-12) for w in raw]


def _diverse_replay_rows(rows: list[Any], maximum: int) -> list[Any]:
    """Recent + hard + uncertainty + long-memory replay under one fixed budget."""
    maximum = max(2, int(maximum))
    if len(rows) <= maximum:
        return list(rows)
    recent_n = max(2, int(maximum * 0.55))
    hard_n = max(1, int(maximum * 0.20))
    uncertainty_n = max(1, int(maximum * 0.15))
    recent = list(rows[-recent_n:])
    chosen_ids = {int(row["id"]) for row in recent}
    older = [row for row in rows[:-recent_n] if int(row["id"]) not in chosen_ids]
    hard = sorted(older, key=_row_difficulty, reverse=True)[:hard_n]
    chosen_ids.update(int(row["id"]) for row in hard)
    uncertainty_pool = [row for row in older if int(row["id"]) not in chosen_ids]
    uncertain = sorted(uncertainty_pool, key=_uncertainty_priority, reverse=True)[:uncertainty_n]
    chosen_ids.update(int(row["id"]) for row in uncertain)
    memory_pool = [row for row in older if int(row["id"]) not in chosen_ids]
    need = maximum - len(recent) - len(hard) - len(uncertain)
    memory: list[Any] = []
    if need > 0 and memory_pool:
        if len(memory_pool) <= need:
            memory = memory_pool
        else:
            step = len(memory_pool) / need
            memory = [memory_pool[min(len(memory_pool)-1, int(i*step))] for i in range(need)]
    chosen = {int(row["id"]): row for row in recent + hard + uncertain + memory}
    return sorted(chosen.values(), key=lambda row: int(row["id"]))[-maximum:]


def _weighted_ece(ps: list[float], ys: list[int], weights: list[float], bins: int = 8) -> float:
    total = max(1e-12, sum(weights))
    acc = 0.0
    for b in range(max(2, int(bins))):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, p in enumerate(ps) if (lo <= p < hi) or (b == bins - 1 and p == 1.0)]
        if not idx:
            continue
        mass = sum(weights[i] for i in idx)
        conf = sum(weights[i] * ps[i] for i in idx) / max(mass, 1e-12)
        freq = sum(weights[i] * ys[i] for i in idx) / max(mass, 1e-12)
        acc += (mass / total) * abs(conf - freq)
    return acc


def _brier_metrics(predictions: list[list[float]], labels: list[list[int]],
                   heuristic_predictions: list[list[float]] | None = None,
                   reference_predictions: list[list[float]] | None = None,
                   groups: list[str] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    weights = _launch_balanced_weights(groups, len(labels))
    denom = max(1e-12, sum(weights))
    for k, name in enumerate(OUTPUT_NAMES):
        ys = [int(row[k]) for row in labels]
        ps = [_clamp(row[k]) for row in predictions]
        prevalence = sum(w*y for w, y in zip(weights, ys)) / denom
        brier = sum(w*(p-y)**2 for w, p, y in zip(weights, ps, ys)) / denom
        constant_brier = prevalence * (1 - prevalence)
        skill = None if constant_brier <= 1e-12 else 1 - brier / constant_brier
        logloss = -sum(w*(y*math.log(p)+(1-y)*math.log(1-p)) for w,p,y in zip(weights,ps,ys)) / denom
        accuracy = sum(w*((p >= .5) == bool(y)) for w,p,y in zip(weights,ps,ys)) / denom
        heuristic_brier = None
        delta_balanced = None
        if heuristic_predictions is not None:
            hps = [_clamp(row[k]) for row in heuristic_predictions]
            heuristic_brier = sum(w*(p-y)**2 for w,p,y in zip(weights,hps,ys)) / denom
            delta_balanced = brier - heuristic_brier
        ece = _weighted_ece(ps, ys, weights)
        reference_brier = None
        delta_reference = None
        reference_ece = None
        if reference_predictions is not None:
            rps = [_clamp(row[k]) for row in reference_predictions]
            reference_brier = sum(w*(p-y)**2 for w,p,y in zip(weights,rps,ys)) / denom
            delta_reference = brier - reference_brier
            reference_ece = _weighted_ece(rps, ys, weights)
        result[name] = {
            "prevalence": round(prevalence, 4),
            "brier": round(brier, 6),
            "brier_skill_vs_constant": None if skill is None else round(skill, 4),
            "balanced_heuristic_brier": None if heuristic_brier is None else round(heuristic_brier, 6),
            "brier_delta_vs_balanced": None if delta_balanced is None else round(delta_balanced, 6),
            "beats_balanced_brier": None if delta_balanced is None else bool(delta_balanced < 0),
            "reference_brier": None if reference_brier is None else round(reference_brier, 6),
            "brier_delta_vs_reference": None if delta_reference is None else round(delta_reference, 6),
            "beats_reference_brier": None if delta_reference is None else bool(delta_reference < 0),
            "ece": round(ece, 6),
            "reference_ece": None if reference_ece is None else round(reference_ece, 6),
            "calibration_delta_vs_reference": None if reference_ece is None else round(ece - reference_ece, 6),
            "log_loss": round(logloss, 6),
            "accuracy": round(accuracy, 4),
            "validation_unit": "launch_balanced",
            "unique_launches": len(set(groups or [])) if groups else len(labels),
        }
    return result


def _initial_weights(d: int, h: int, out: int, rng: random.Random) -> tuple[list[list[float]], list[float], list[list[float]], list[float]]:
    scale1 = 1 / math.sqrt(d)
    scale2 = 1 / math.sqrt(h)
    w1 = [[rng.uniform(-scale1, scale1) for _ in range(d)] for _ in range(h)]
    b1 = [0.0] * h
    w2 = [[rng.uniform(-scale2, scale2) for _ in range(h)] for _ in range(out)]
    b2 = [0.0] * out
    return w1, b1, w2, b2


def _rebase_first_layer(parent: HorizonNetwork, new_means: list[float], new_stds: list[float]) -> tuple[list[list[float]], list[float]]:
    # Preserve the parent's function after changing feature standardization:
    # z_old=(x-m_old)/s_old expressed through z_new=(x-m_new)/s_new.
    w1: list[list[float]] = []
    b1: list[float] = []
    for j, row in enumerate(parent.w1):
        new_row = [row[i] * new_stds[i] / parent.stds[i] for i in range(len(row))]
        new_bias = parent.b1[j] + sum(
            row[i] * (new_means[i] - parent.means[i]) / parent.stds[i]
            for i in range(len(row))
        )
        w1.append(new_row)
        b1.append(new_bias)
    return w1, b1


def _fit_network(training_rows_only: list[Any], validation_rows: list[Any], horizon: int, *,
                 hidden_units: int, epochs: int, learning_rate: float, seed: int,
                 parent: HorizonNetwork | None = None, learning_profile: str = "balanced") -> HorizonNetwork:
    if len(training_rows_only) < 2 or len(validation_rows) < 1:
        raise ValueError(f"Insufficient train/validation rows for {horizon}m")
    validation_addresses = {str(row["address"]) for row in validation_rows}
    training_rows_only = [row for row in training_rows_only if str(row["address"]) not in validation_addresses]
    if len(training_rows_only) < 2:
        raise ValueError(f"Token-disjoint training split too small for {horizon}m")

    x_raw = [training_features(row) for row in training_rows_only]
    y_train = [_labels(row) for row in training_rows_only]
    x_val_raw = [training_features(row) for row in validation_rows]
    y_val = [_labels(row) for row in validation_rows]
    means, stds = _means_stds(x_raw)
    x_train = [[(v - means[i]) / stds[i] for i, v in enumerate(row)] for row in x_raw]

    rng = random.Random(seed + horizon + len(training_rows_only))
    d = len(FEATURE_NAMES)
    h = int(hidden_units)
    out = len(OUTPUT_NAMES)
    if parent is not None and len(parent.w1) == h and len(parent.w2) == out:
        w1, b1 = _rebase_first_layer(parent, means, stds)
        w2 = [list(row) for row in parent.w2]
        b2 = list(parent.b2)
    else:
        w1, b1, w2, b2 = _initial_weights(d, h, out, rng)

    train_groups = [str(row["address"]) for row in training_rows_only]
    learning_weights = _combined_training_weights(training_rows_only, learning_profile)
    prevalence = [
        sum(w * row[k] for w, row in zip(learning_weights, y_train)) / max(1e-12, sum(learning_weights))
        for k in range(out)
    ]
    pos_weights = [min(5.0, max(1.0, (1-p) / max(p, .02))) for p in prevalence]
    order = list(range(len(x_train)))
    n = len(x_train)
    for epoch in range(max(1, int(epochs))):
        rng.shuffle(order)
        lr = learning_rate * (0.90 ** epoch)
        for idx in order:
            x = x_train[idx]
            y = y_train[idx]
            # Recent observations receive more weight while older rows remain in
            # replay to limit catastrophic forgetting.
            recency = 0.55 + 1.45 * ((idx + 1) / n) ** 2
            hidden = [math.tanh(b1[j] + sum(w1[j][i] * x[i] for i in range(d))) for j in range(h)]
            preds = [_sigmoid(b2[k] + sum(w2[k][j] * hidden[j] for j in range(h))) for k in range(out)]
            d_out = []
            for k in range(out):
                weight = (pos_weights[k] if y[k] else 1.0) * recency * learning_weights[idx]
                weight *= _target_confidence(training_rows_only[idx], k)
                d_out.append((preds[k] - y[k]) * weight)
            d_hidden = [
                (1 - hidden[j] ** 2) * sum(d_out[k] * w2[k][j] for k in range(out))
                for j in range(h)
            ]
            for k in range(out):
                for j in range(h):
                    w2[k][j] -= lr * (d_out[k] * hidden[j] + 1e-5 * w2[k][j])
                b2[k] -= lr * d_out[k]
            for j in range(h):
                for i in range(d):
                    w1[j][i] -= lr * (d_hidden[j] * x[i] + 1e-5 * w1[j][i])
                b1[j] -= lr * d_hidden[j]

    network = HorizonNetwork(
        horizon, means, stds, w1, b1, w2, b2, {}, len(training_rows_only), len(validation_rows), prevalence
    )
    val_predictions = [network.predict(row) for row in x_val_raw]
    heuristic_predictions = [_heuristic(row) for row in validation_rows]
    reference_predictions = None
    if parent is not None:
        reference_predictions = [parent.predict(row) for row in x_val_raw]
    network.metrics = _brier_metrics(
        val_predictions, y_val, heuristic_predictions, reference_predictions,
        groups=[str(row["address"]) for row in validation_rows],
    )
    return network


def _train_horizon(rows: list[Any], horizon: int, *, hidden_units: int, epochs: int,
                   learning_rate: float, seed: int) -> HorizonNetwork:
    # Split by launch/token, not by prediction row. This removes leakage when a
    # single launch produced many closely related snapshots.
    launch_order = list(dict.fromkeys(str(row["address"]) for row in rows))
    if len(launch_order) < 2:
        raise ValueError(f"Need at least two independent launches for {horizon}m")
    split = max(1, min(len(launch_order) - 1, int(len(launch_order) * .80)))
    validation_addresses = set(launch_order[split:])
    validation_rows = _recent_unique_launch_rows([r for r in rows if str(r["address"]) in validation_addresses])
    training_rows_only = [r for r in rows if str(r["address"]) not in validation_addresses]
    return _fit_network(
        training_rows_only, validation_rows, horizon,
        hidden_units=hidden_units, epochs=epochs, learning_rate=learning_rate, seed=seed,
    )


def training_rows(store: Any, horizon: int, max_rows: int) -> list[Any]:
    _require_real_training_domain(store)
    rows = store.conn.execute(
        """SELECT p.*,o.hit_5,o.hit_10,o.hit_25,o.rug,o.stagnant,o.max_return_pct,
        o.min_return_pct,o.return_pct,
        (SELECT s.payload_json FROM snapshots s WHERE s.address=p.address AND s.captured_at=p.predicted_at ORDER BY s.id DESC LIMIT 1) AS source_payload_json,
        (SELECT s.payload_json FROM snapshots s WHERE s.address=p.address AND s.captured_at>=o.measured_at ORDER BY s.captured_at LIMIT 1) AS terminal_payload_json
        FROM learning_predictions p JOIN learning_outcomes o ON o.prediction_id=p.id
        WHERE p.model_family='balanced' AND p.horizon_minutes=?
        ORDER BY p.predicted_at DESC,p.id DESC LIMIT ?""",
        (int(horizon), max(2, int(max_rows) * 6)),
    ).fetchall()
    clean, _quality = filter_learning_rows(list(reversed(rows)))
    return _diverse_replay_rows(clean, max_rows)


def _rows_before_prediction(store: Any, horizon: int, before_id: int, max_rows: int,
                            excluded_addresses: set[str]) -> list[Any]:
    _require_real_training_domain(store)
    rows = store.conn.execute(
        """SELECT p.*,o.hit_5,o.hit_10,o.hit_25,o.rug,o.stagnant,o.max_return_pct,
        o.min_return_pct,o.return_pct,
        (SELECT s.payload_json FROM snapshots s WHERE s.address=p.address AND s.captured_at=p.predicted_at ORDER BY s.id DESC LIMIT 1) AS source_payload_json,
        (SELECT s.payload_json FROM snapshots s WHERE s.address=p.address AND s.captured_at>=o.measured_at ORDER BY s.captured_at LIMIT 1) AS terminal_payload_json
        FROM learning_predictions p JOIN learning_outcomes o ON o.prediction_id=p.id
        WHERE p.model_family='balanced' AND p.horizon_minutes=? AND p.id<?
        ORDER BY p.id DESC LIMIT ?""",
        (int(horizon), int(before_id), max(2, int(max_rows) * 6)),
    ).fetchall()
    chosen = [row for row in reversed(rows) if str(row["address"]) not in excluded_addresses]
    clean, _quality = filter_learning_rows(chosen)
    return _diverse_replay_rows(clean, max_rows)


def _latest_balanced_prediction_id(store: Any) -> int:
    row = store.conn.execute(
        """SELECT COALESCE(MAX(p.id),0) AS n FROM learning_predictions p
        JOIN learning_outcomes o ON o.prediction_id=p.id WHERE p.model_family='balanced'"""
    ).fetchone()
    return int(row["n"] or 0)


def _new_rows_since(store: Any, horizon: int, after_id: int) -> list[Any]:
    _require_real_training_domain(store)
    rows = store.conn.execute(
        """SELECT p.*,o.hit_5,o.hit_10,o.hit_25,o.rug,o.stagnant,o.max_return_pct,
        o.min_return_pct,o.return_pct,
        (SELECT s.payload_json FROM snapshots s WHERE s.address=p.address AND s.captured_at=p.predicted_at ORDER BY s.id DESC LIMIT 1) AS source_payload_json,
        (SELECT s.payload_json FROM snapshots s WHERE s.address=p.address AND s.captured_at>=o.measured_at ORDER BY s.captured_at LIMIT 1) AS terminal_payload_json
        FROM learning_predictions p JOIN learning_outcomes o ON o.prediction_id=p.id
        WHERE p.model_family='balanced' AND p.horizon_minutes=? AND p.id>?
        ORDER BY p.id""",
        (int(horizon), int(after_id)),
    ).fetchall()
    clean, _quality = filter_learning_rows(list(rows))
    return clean


def train_machine_shadow(store: Any, model_path: Path, *, max_rows_per_horizon: int = 5000,
                         minimum_rows_per_horizon: int = 500, hidden_units: int = 12,
                         epochs: int = 12, learning_rate: float = .018,
                         seed: int = 20260903) -> MachineShadowBundle:
    """Bootstrap the continual adaptive network. Kept as the public ml-train API."""
    _require_real_training_domain(store)
    horizons: dict[int, HorizonNetwork] = {}
    for horizon in HORIZONS:
        rows = training_rows(store, horizon, max_rows_per_horizon)
        if len(rows) < minimum_rows_per_horizon:
            raise ValueError(
                f"Not enough resolved balanced rows for {horizon}m: {len(rows)} < {minimum_rows_per_horizon}"
            )
        horizons[horizon] = _train_horizon(
            rows, horizon, hidden_units=hidden_units, epochs=epochs,
            learning_rate=learning_rate, seed=seed,
        )
    anchor = _latest_balanced_prediction_id(store)
    bundle = MachineShadowBundle(
        trained_at=datetime.now(timezone.utc).isoformat(),
        source_family="balanced",
        authority_pct=0.0,
        feature_names=FEATURE_NAMES,
        horizons=horizons,
        training_config={
            "mode": "bootstrap",
            "max_rows_per_horizon": int(max_rows_per_horizon),
            "minimum_rows_per_horizon": int(minimum_rows_per_horizon),
            "hidden_units": int(hidden_units),
            "epochs": int(epochs),
            "learning_rate": float(learning_rate),
            "seed": int(seed),
            "split": "chronological_80_20_launch_disjoint",
            "evidence_unit": "launch_balanced",
            "per_launch_training_weight": True,
            "rug_label_policy": "legacy_price_crash_positive_is_ambiguous_weight_0.15",
            "recency_weighted_replay": True,
            "execution_authority_pct": 0,
        },
        model_family=ML_ADAPTIVE_FAMILY,
        model_version=generation_version(1, ML_ADAPTIVE_FAMILY),
        generation=1,
        parent_generation=None,
        training_anchor_prediction_id=anchor,
    )
    bundle.save(model_path)
    champion = clone_bundle(bundle, ML_CHAMPION_FAMILY)
    # v1 preserves every existing legacy champion. Only the new preregistered
    # governor can promote a v1 shadow artifact; legacy bootstrap is a reference.
    existing_champion = model_path.with_name(ML_CHAMPION_FILENAME)
    if existing_champion.exists():
        champion = MachineShadowBundle.load(existing_champion)
    else:
        champion.save(existing_champion)
    state = {
        "lineage_version": ML_LINEAGE_VERSION,
        "adaptive_generation": 1,
        "champion_generation": 1,
        "last_evaluated_prediction_id": anchor,
        "last_promoted_prediction_id": anchor,
        "adaptive_updates": 0,
        "champion_promotions": 0,
        "rejected_for_champion": 0,
        "last_event": "bootstrap",
        "last_event_at": bundle.trained_at,
        "last_drift": None,
        "history": [],
    }
    _save_state(model_path.with_name(ML_STATE_FILENAME), state)
    _register_bundle(store, bundle)
    _register_bundle(store, champion)
    return bundle


def clone_bundle(bundle: MachineShadowBundle, family: str) -> MachineShadowBundle:
    raw = bundle.as_dict()
    raw["model_family"] = family
    raw["model_version"] = generation_version(bundle.generation, family)
    return MachineShadowBundle.from_dict(raw)


def _register_bundle(store: Any, bundle: MachineShadowBundle) -> None:
    store.register_model(bundle.model_family, bundle.model_version, {
        "authority_pct": 0,
        "architecture": f"{len(FEATURE_NAMES)}-{len(next(iter(bundle.horizons.values())).w1)}-{len(OUTPUT_NAMES)}",
        "horizons": sorted(bundle.horizons),
        "trained_at": bundle.trained_at,
        "generation": bundle.generation,
        "parent_generation": bundle.parent_generation,
        "continual_learning": True,
    })


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("lineage_version") != ML_LINEAGE_VERSION:
            return {}
        return raw
    except Exception:
        return {}


def _save_state(path: Path, state: dict[str, Any]) -> None:
    state = dict(state)
    state["lineage_version"] = ML_LINEAGE_VERSION
    history = list(state.get("history") or [])[-40:]
    state["history"] = history
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _drift_for_rows(network: HorizonNetwork, rows: list[Any]) -> dict[str, Any]:
    if not rows:
        return {"score": 0.0, "feature_shift": 0.0, "label_shift": 0.0, "level": "unknown"}
    features = [training_features(row) for row in rows]
    feature_shifts = []
    for i in range(len(FEATURE_NAMES)):
        mean_recent = sum(row[i] for row in features) / len(features)
        feature_shifts.append(min(3.0, abs(mean_recent - network.means[i]) / network.stds[i]))
    recent_prev = [sum(_labels(row)[k] for row in rows) / len(rows) for k in range(len(OUTPUT_NAMES))]
    label_shifts = [abs(recent_prev[k] - network.training_prevalence[k]) for k in range(len(OUTPUT_NAMES))]
    feature_shift = sum(feature_shifts) / len(feature_shifts)
    label_shift = sum(label_shifts) / len(label_shifts)
    score = .72 * min(1.5, feature_shift) + .28 * min(1.0, label_shift * 3.0)
    level = "high" if score >= .75 else ("moderate" if score >= .35 else "stable")
    return {
        "score": round(score, 4),
        "feature_shift": round(feature_shift, 4),
        "label_shift": round(label_shift, 4),
        "level": level,
    }


def _regime_validation_summary(bundle: MachineShadowBundle, rows_by_horizon: dict[int, list[Any]]) -> dict[str, Any]:
    buckets: dict[str, list[float]] = {}
    counts: dict[str, int] = {}
    for h, rows in rows_by_horizon.items():
        net = bundle.horizons[h]
        for row in rows:
            regime = _primary_learning_regime(row)
            pred = net.predict(training_features(row))
            ys = _labels(row)
            brier = sum((p-y)**2 for p, y in zip(pred, ys)) / len(ys)
            buckets.setdefault(regime, []).append(brier)
            counts[regime] = counts.get(regime, 0) + 1
    by_regime = {
        regime: {"rows": counts[regime], "mean_brier": round(sum(vals)/len(vals), 6)}
        for regime, vals in sorted(buckets.items()) if vals
    }
    worst = max((float(v["mean_brier"]) for v in by_regime.values()), default=None)
    mean = (sum(float(v["mean_brier"]) for v in by_regime.values()) / len(by_regime)) if by_regime else None
    return {
        "by_regime": by_regime,
        "regimes_seen": len(by_regime),
        "mean_regime_brier": None if mean is None else round(mean, 6),
        "worst_regime_brier": None if worst is None else round(worst, 6),
    }


def _aggregate_validation(bundle: MachineShadowBundle) -> dict[str, Any]:
    briers: list[float] = []
    refs: list[float] = []
    wins = 0
    compared = 0
    horizon_rows: dict[str, Any] = {}
    rug_briers: list[float] = []
    rug_refs: list[float] = []
    eces: list[float] = []
    ref_eces: list[float] = []
    horizon_wins = 0
    for h, net in sorted(bundle.horizons.items()):
        hb: list[float] = []
        hr: list[float] = []
        for name, metric in net.metrics.items():
            b = metric.get("brier")
            r = metric.get("reference_brier")
            if b is not None:
                briers.append(float(b)); hb.append(float(b))
            if r is not None:
                refs.append(float(r)); hr.append(float(r)); compared += 1
                if float(b) < float(r):
                    wins += 1
            ece = metric.get("ece")
            ref_ece = metric.get("reference_ece")
            if ece is not None:
                eces.append(float(ece))
            if ref_ece is not None:
                ref_eces.append(float(ref_ece))
            if name == "p_rug" and b is not None:
                rug_briers.append(float(b))
                if r is not None:
                    rug_refs.append(float(r))
        cand = sum(hb) / len(hb) if hb else None
        ref = sum(hr) / len(hr) if hr else None
        if cand is not None and ref is not None and cand < ref:
            horizon_wins += 1
        horizon_rows[str(h)] = {
            "candidate_mean_brier": None if cand is None else round(cand, 6),
            "reference_mean_brier": None if ref is None else round(ref, 6),
            "beats_reference": None if cand is None or ref is None else cand < ref,
        }
    return {
        "candidate_mean_brier": None if not briers else round(sum(briers) / len(briers), 6),
        "reference_mean_brier": None if not refs else round(sum(refs) / len(refs), 6),
        "outputs_compared": compared,
        "outputs_beating_reference": wins,
        "horizons_beating_reference": horizon_wins,
        "candidate_rug_brier": None if not rug_briers else round(sum(rug_briers) / len(rug_briers), 6),
        "reference_rug_brier": None if not rug_refs else round(sum(rug_refs) / len(rug_refs), 6),
        "candidate_mean_ece": None if not eces else round(sum(eces) / len(eces), 6),
        "reference_mean_ece": None if not ref_eces else round(sum(ref_eces) / len(ref_eces), 6),
        "horizons": horizon_rows,
    }


def _champion_promotion_allowed(summary: dict[str, Any], drift_level: str) -> tuple[bool, list[str]]:
    cand = summary.get("candidate_mean_brier")
    ref = summary.get("reference_mean_brier")
    rug = summary.get("candidate_rug_brier")
    rug_ref = summary.get("reference_rug_brier")
    if cand is None or ref is None or ref <= 0:
        return False, ["missing_reference_validation"]
    relative = cand / ref - 1.0
    outputs = int(summary.get("outputs_beating_reference") or 0)
    horizons = int(summary.get("horizons_beating_reference") or 0)
    max_horizon_regression = 0.0
    for row in (summary.get("horizons") or {}).values():
        c, r = row.get("candidate_mean_brier"), row.get("reference_mean_brier")
        if c is not None and r not in (None, 0):
            max_horizon_regression = max(max_horizon_regression, float(c) / float(r) - 1.0)
    rug_regression = 0.0 if rug is None or rug_ref is None else float(rug) - float(rug_ref)
    cand_ece = summary.get("candidate_mean_ece")
    ref_ece = summary.get("reference_mean_ece")
    calibration_regression = 0.0 if cand_ece is None or ref_ece is None else float(cand_ece) - float(ref_ece)
    if drift_level == "high":
        rules = [
            (relative <= .002, f"aggregate_relative={relative:.4f}<=0.002"),
            (outputs >= 12, f"output_wins={outputs}>=12"),
            (horizons >= 2, f"horizon_wins={horizons}>=2"),
            (rug_regression <= .003, f"rug_regression={rug_regression:.5f}<=0.003"),
            (calibration_regression <= .015, f"calibration_regression={calibration_regression:.5f}<=0.015"),
            (max_horizon_regression <= .07, f"max_horizon_regression={max_horizon_regression:.4f}<=0.07"),
        ]
    else:
        rules = [
            (relative <= -.005, f"aggregate_relative={relative:.4f}<=-0.005"),
            (outputs >= 15, f"output_wins={outputs}>=15"),
            (horizons >= 3, f"horizon_wins={horizons}>=3"),
            (rug_regression <= .002, f"rug_regression={rug_regression:.5f}<=0.002"),
            (calibration_regression <= .010, f"calibration_regression={calibration_regression:.5f}<=0.010"),
            (max_horizon_regression <= .05, f"max_horizon_regression={max_horizon_regression:.4f}<=0.05"),
        ]
    return all(flag for flag, _ in rules), [text if flag else f"FAIL:{text}" for flag, text in rules]


def adaptive_learning_tick(store: Any, *, max_rows_per_horizon: int = 6500,
                           bootstrap_minimum_rows: int = 500,
                           normal_new_rows_per_horizon: int = 120,
                           high_drift_new_rows_per_horizon: int = 60,
                           hidden_units: int = 12, update_epochs: int = 3,
                           update_learning_rate: float = .0045,
                           seed: int = 20260903) -> dict[str, Any]:
    """One bounded healthy-maximum continual-learning iteration.

    v0.8.22 keeps the zero-authority candidate population with stable, hard-example,
    uncertainty, rare-regime and economically weighted specialists. A meta-selector uses
    launch-disjoint selection evidence, calibration and worst-regime robustness;
    only then is the winner evaluated on untouched holdout launches.
    The scanner remains isolated in its own process and trading parameters are
    unchanged.
    """
    _require_real_training_domain(store)
    adaptive_path = store.path.with_name(ML_MODEL_FILENAME)
    champion_path = store.path.with_name(ML_CHAMPION_FILENAME)
    state_path = store.path.with_name(ML_STATE_FILENAME)

    if not adaptive_path.exists() or not champion_path.exists():
        bundle = train_machine_shadow(
            store, adaptive_path, max_rows_per_horizon=max_rows_per_horizon,
            minimum_rows_per_horizon=bootstrap_minimum_rows,
            hidden_units=hidden_units, epochs=max(8, update_epochs * 3),
            learning_rate=.012, seed=seed,
        )
        return {
            "action": "BOOTSTRAPPED", "adaptive_generation": bundle.generation,
            "champion_generation": bundle.generation, "authority_pct": 0,
        }

    adaptive = MachineShadowBundle.load(adaptive_path)
    champion = MachineShadowBundle.load(champion_path)
    state = _load_state(state_path)
    last_evaluated = int(state.get("last_evaluated_prediction_id") or adaptive.training_anchor_prediction_id or 0)

    new_by_horizon = {h: _new_rows_since(store, h, last_evaluated) for h in HORIZONS}
    counts = {str(h): len(rows) for h, rows in new_by_horizon.items()}
    unique_counts = {str(h): len({str(row["address"]) for row in rows}) for h, rows in new_by_horizon.items()}
    drift_rows = {h: _recent_unique_launch_rows(rows, 80) for h, rows in new_by_horizon.items() if rows}
    drift_parts = {str(h): _drift_for_rows(adaptive.horizons[h], drift_rows.get(h, [])) for h in HORIZONS}
    drift_score = max((float(v.get("score") or 0) for v in drift_parts.values()), default=0.0)
    drift_level = "high" if drift_score >= .75 else ("moderate" if drift_score >= .35 else "stable")
    row_required = high_drift_new_rows_per_horizon if drift_level == "high" else normal_new_rows_per_horizon
    required_unique = max(50 if drift_level == "high" else 80, row_required // 2)
    min_unique = min(unique_counts.values()) if unique_counts else 0

    if min_unique < required_unique:
        state["last_drift"] = {"level": drift_level, "score": round(drift_score, 4), "horizons": drift_parts}
        state["last_event"] = "waiting_for_new_labels"
        state["last_event_at"] = datetime.now(timezone.utc).isoformat()
        _save_state(state_path, state)
        return {
            "action": "WAIT", "new_rows_per_horizon": counts,
            "new_unique_launches_per_horizon": unique_counts,
            "required_unique_launches_per_horizon": required_unique,
            "drift": state["last_drift"], "adaptive_generation": adaptive.generation,
            "champion_generation": champion.generation, "learning_intensity": "CAUSAL_QUALITY_MAX",
            "authority_pct": 0,
        }

    generation = adaptive.generation + 1
    profiles = [
        {"name": "memory_stable", "learning_profile": "stable", "lr_mult": .72, "epoch_add": 0, "seed_add": 101},
        {"name": "hardcase_adaptive", "learning_profile": "hardcase", "lr_mult": 1.00,
         "epoch_add": 1 if drift_level == "high" else 0, "seed_add": 307},
        {"name": "uncertainty_explorer", "learning_profile": "uncertainty", "lr_mult": .88,
         "epoch_add": 1, "seed_add": 509},
        {"name": "rare_regime_specialist", "learning_profile": "rare_regime", "lr_mult": .82,
         "epoch_add": 1 if drift_level != "stable" else 0, "seed_add": 701},
        {"name": "profit_seeking", "learning_profile": "profit_seeking", "lr_mult": .92,
         "epoch_add": 2, "seed_add": 907},
        {"name": "profit_capture", "learning_profile": "profit_capture", "lr_mult": .86,
         "epoch_add": 2, "seed_add": 1103},
        {"name": "loss_avoider", "learning_profile": "loss_avoider", "lr_mult": .84,
         "epoch_add": 1, "seed_add": 1301},
    ]
    candidate_networks: dict[str, dict[int, HorizonNetwork]] = {p["name"]: {} for p in profiles}
    selection_rows_by_h: dict[int, list[Any]] = {}
    holdout_rows_by_h: dict[int, list[Any]] = {}
    split_counts: dict[str, dict[str, int]] = {}
    max_seen_id = last_evaluated

    for h in HORIZONS:
        new_rows = new_by_horizon[h]
        unique_new = _recent_unique_launch_rows(new_rows)
        n = len(unique_new)
        holdout_n = max(20, min(40, int(n * .35)))
        selection_n = max(20, min(32, int(n * .25)))
        while selection_n + holdout_n > n - 2:
            if selection_n >= holdout_n and selection_n > 1:
                selection_n -= 1
            elif holdout_n > 1:
                holdout_n -= 1
            else:
                break
        if selection_n < 1 or holdout_n < 1:
            raise ValueError(f"Need more independent launches for two-stage validation at {h}m")
        holdout = unique_new[-holdout_n:]
        selection = unique_new[-(selection_n + holdout_n):-holdout_n]
        selection_rows_by_h[h] = selection
        holdout_rows_by_h[h] = holdout
        split_counts[str(h)] = {"selection": len(selection), "holdout": len(holdout)}
        max_seen_id = max(max_seen_id, max(int(row["id"]) for row in new_rows))
        first_validation_id = min(int(row["id"]) for row in selection + holdout)
        excluded = {str(row["address"]) for row in selection + holdout}
        train_rows = _rows_before_prediction(store, h, first_validation_id, max_rows_per_horizon, excluded)
        if len(train_rows) < bootstrap_minimum_rows:
            raise ValueError(f"Continual training window too small for {h}m: {len(train_rows)}")
        for profile in profiles:
            candidate_networks[profile["name"]][h] = _fit_network(
                train_rows, selection, h, hidden_units=hidden_units,
                epochs=max(1, update_epochs + int(profile["epoch_add"])),
                learning_rate=update_learning_rate * float(profile["lr_mult"]),
                seed=seed + generation * 97 + int(profile["seed_add"]),
                parent=adaptive.horizons[h], learning_profile=str(profile["learning_profile"]),
            )

    population_scores: dict[str, Any] = {}
    population_bundles: dict[str, MachineShadowBundle] = {}
    for profile in profiles:
        name = str(profile["name"])
        bundle = MachineShadowBundle(
            trained_at=datetime.now(timezone.utc).isoformat(), source_family="balanced", authority_pct=0,
            feature_names=FEATURE_NAMES, horizons=candidate_networks[name],
            training_config={"mode": "continual_population_candidate", "profile": name, "execution_authority_pct": 0},
            model_family=ML_ADAPTIVE_FAMILY,
            model_version=generation_version(generation, ML_ADAPTIVE_FAMILY), generation=generation,
            parent_generation=adaptive.generation, training_anchor_prediction_id=max_seen_id,
        )
        summary = _aggregate_validation(bundle)
        regime_summary = _regime_validation_summary(bundle, selection_rows_by_h)
        summary["regime_validation"] = regime_summary
        profit_summary = profit_alignment_summary(bundle, adaptive, selection_rows_by_h, training_features)
        summary["profit_alignment"] = profit_summary
        brier = float(summary.get("candidate_mean_brier") or 999.0)
        ece = float(summary.get("candidate_mean_ece") or 0.25)
        worst = float(regime_summary.get("worst_regime_brier") or brier)
        # v0.8.22: economics is the PRIMARY selector. Calibration/Brier are
        # guardrails and tie-breakers, not the objective that can overrule money.
        economic_rank = economic_rank_key(profit_summary)
        summary["economic_rank_key"] = list(economic_rank)
        summary["prediction_quality_tiebreak"] = round(brier + .18 * ece + .12 * worst, 6)
        summary["meta_selection_score"] = summary["prediction_quality_tiebreak"]
        population_bundles[name] = bundle
        population_scores[name] = summary

    # Anti-collapse monitor: specialists must not silently converge to identical
    # predictions. This is diagnostic/research evidence only; authority stays zero.
    pair_diffs: list[float] = []
    names = sorted(population_bundles)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = population_bundles[names[i]], population_bundles[names[j]]
            diffs: list[float] = []
            for h, rows in selection_rows_by_h.items():
                for row in rows:
                    x = training_features(row)
                    pa, pb = a.horizons[h].predict(x), b.horizons[h].predict(x)
                    diffs.extend(abs(xa-xb) for xa, xb in zip(pa, pb))
            if diffs:
                pair_diffs.append(sum(diffs) / len(diffs))
    population_diversity = round(sum(pair_diffs) / len(pair_diffs), 6) if pair_diffs else 0.0
    for summary in population_scores.values():
        summary["population_prediction_diversity"] = population_diversity
        summary["anti_collapse_status"] = "DIVERSE" if population_diversity >= .0025 else "LOW_DIVERSITY"

    def _population_rank(item: tuple[str, Any]) -> tuple[Any, ...]:
        summary = item[1]
        econ = tuple(summary.get("economic_rank_key") or (9, 9, 999.0, 999.0, 999.0, 999.0, 999.0))
        prediction = float(summary.get("prediction_quality_tiebreak") or 999.0)
        # Profit/economic forward evidence is lexicographically first. A model
        # with prettier Brier scores cannot beat a model with materially better
        # after-cost economics merely because classification is easier.
        return econ + (prediction,
                -int(summary.get("horizons_beating_reference") or 0),
                -int(summary.get("outputs_beating_reference") or 0))

    selected_profile = min(population_scores.items(), key=_population_rank)[0]
    candidate = population_bundles[selected_profile]
    candidate.training_config = {
        "mode": "continual_population_two_stage",
        "learning_intensity": "EFFICIENT_MAX",
        "candidate_population_size": len(profiles),
        "candidate_profiles": [p["name"] for p in profiles],
        "selected_profile": selected_profile,
        "population_selection_validation": population_scores,
        "two_stage_validation": True,
        "validation_launches_per_horizon": split_counts,
        "max_rows_per_horizon": max_rows_per_horizon,
        "new_rows_per_horizon": counts,
        "new_unique_launches_per_horizon": unique_counts,
        "required_unique_launches_per_horizon": required_unique,
        "evidence_unit": "launch_balanced",
        "per_launch_training_weight": True,
        "hard_example_mining": "bounded_prioritized_replay",
        "uncertainty_sampling": "moderate_uncertainty_plus_internal_disagreement_noise_guarded",
        "regime_balancing": "bounded_inverse_sqrt_frequency_plus_rare_regime_specialist",
        "replay_mix": "55pct_recent_20pct_hard_15pct_uncertainty_10pct_long_memory",
        "meta_learner": "profit_first_forward_economics_then_prediction_quality_tiebreak",
        "profit_aware_learning": "PRIMARY_OBJECTIVE: after_cost_forward_return+profit_factor+positive_rate-rug_rate; prediction metrics are secondary",
        "economic_validation": "selection_and_untouched_holdout; shadow_only",
        "anti_collapse": "7_distinct_learning_profiles_including_profit_capture_and_loss_avoidance_specialists",
        "population_prediction_diversity": population_diversity,
        "confidence_calibration": "weighted_ECE_8_bins_champion_gate",
        "rug_label_policy": "legacy_price_crash_positive_is_ambiguous_weight_0.15",
        "hidden_units": hidden_units,
        "base_epochs": update_epochs,
        "base_learning_rate": update_learning_rate,
        "recency_weighted_replay": True,
        "drift_level": drift_level,
        "drift_score": round(drift_score, 4),
        "execution_authority_pct": 0,
    }

    # Final champion comparison uses untouched holdout launches only. The chosen
    # population member was selected on a different set, limiting selection bias.
    for h in HORIZONS:
        holdout = holdout_rows_by_h[h]
        x_val = [training_features(row) for row in holdout]
        y_val = [_labels(row) for row in holdout]
        candidate_preds = [candidate.horizons[h].predict(x) for x in x_val]
        champion_preds = [champion.horizons[h].predict(x) for x in x_val]
        heur = [_heuristic(row) for row in holdout]
        candidate.horizons[h].metrics = _brier_metrics(
            candidate_preds, y_val, heur, champion_preds,
            groups=[str(row["address"]) for row in holdout],
        )

    summary = _aggregate_validation(candidate)
    profit_summary = profit_alignment_summary(candidate, champion, holdout_rows_by_h, training_features)
    summary["profit_alignment"] = profit_summary
    promote, promotion_rules = _champion_promotion_allowed(summary, drift_level)
    economic_rules = profit_promotion_rules(profit_summary)
    if not all(flag for flag, _ in economic_rules):
        promote = False
    diversity_rule = (population_diversity >= .0010, f"population_diversity={population_diversity:.6f}>=0.001000")
    if not diversity_rule[0]:
        promote = False
    promotion_rules = (
        list(promotion_rules)
        + [text if flag else f"FAIL:{text}" for flag, text in economic_rules]
        + [diversity_rule[1] if diversity_rule[0] else f"FAIL:{diversity_rule[1]}"]
    )
    promote = False
    promotion_rules.append("FAIL:v1_requires_preregistered_coin_forward_pf2_governor")
    candidate.training_config["champion_validation"] = summary
    candidate.training_config["champion_promotion_rules"] = promotion_rules
    candidate.training_config["champion_promoted"] = promote
    candidate_path = store.path.with_name(ML_CANDIDATE_FILENAME)
    candidate.save(candidate_path)

    candidate_path.replace(adaptive_path)
    _register_bundle(store, candidate)

    if promote:
        new_champion = clone_bundle(candidate, ML_CHAMPION_FAMILY)
        new_champion.save(champion_path)
        _register_bundle(store, new_champion)
        champion_generation = generation
        state["champion_promotions"] = int(state.get("champion_promotions") or 0) + 1
        state["last_promoted_prediction_id"] = max_seen_id
        champion_event = "PROMOTED_CHAMPION"
    else:
        champion_generation = champion.generation
        state["rejected_for_champion"] = int(state.get("rejected_for_champion") or 0) + 1
        champion_event = "ADAPTED_CHAMPION_HELD"

    event = {
        "at": candidate.trained_at, "generation": generation,
        "parent_generation": adaptive.generation, "champion_generation": champion_generation,
        "champion_promoted": promote, "selected_profile": selected_profile,
        "drift_level": drift_level, "drift_score": round(drift_score, 4), "validation": summary,
    }
    history = list(state.get("history") or [])
    history.append(event)
    state.update({
        "adaptive_generation": generation, "champion_generation": champion_generation,
        "last_evaluated_prediction_id": max_seen_id,
        "adaptive_updates": int(state.get("adaptive_updates") or 0) + 1,
        "last_event": champion_event, "last_event_at": candidate.trained_at,
        "last_selected_profile": selected_profile,
        "last_drift": {"level": drift_level, "score": round(drift_score, 4), "horizons": drift_parts},
        "last_validation": summary, "history": history[-40:],
    })
    _save_state(state_path, state)
    return {
        "action": champion_event, "adaptive_generation": generation,
        "champion_generation": champion_generation, "champion_promoted": promote,
        "selected_profile": selected_profile, "population_selection": population_scores,
        "validation_split": split_counts, "new_rows_per_horizon": counts,
        "new_unique_launches_per_horizon": unique_counts,
        "drift": state["last_drift"], "validation": summary,
        "promotion_rules": promotion_rules, "learning_intensity": "CAUSAL_QUALITY_MAX",
        "authority_pct": 0,
    }


_MODEL_CACHE: dict[str, tuple[float, MachineShadowBundle]] = {}


def _load_cached(path: Path) -> MachineShadowBundle | None:
    if not path.exists():
        return None
    key = str(path.resolve())
    mtime = path.stat().st_mtime
    cached = _MODEL_CACHE.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    bundle = MachineShadowBundle.load(path)
    _MODEL_CACHE[key] = (mtime, bundle)
    return bundle


def load_machine_shadow_for_store(store: Any) -> MachineShadowBundle | None:
    return _load_cached(store.path.with_name(ML_MODEL_FILENAME))


def load_machine_models_for_store(store: Any) -> tuple[MachineShadowBundle | None, MachineShadowBundle | None]:
    return (
        _load_cached(store.path.with_name(ML_MODEL_FILENAME)),
        _load_cached(store.path.with_name(ML_CHAMPION_FILENAME)),
    )


def _prediction_counts(store: Any, family: str, version: str | None = None) -> dict[str, int]:
    params: list[Any] = [family]
    version_sql = ""
    if version:
        version_sql = " AND p.model_version=?"
        params.append(version)
    row = store.conn.execute(
        f"""SELECT COUNT(*) AS predictions,
        SUM(CASE WHEN o.prediction_id IS NOT NULL THEN 1 ELSE 0 END) AS resolved
        FROM learning_predictions p LEFT JOIN learning_outcomes o ON o.prediction_id=p.id
        WHERE p.model_family=?{version_sql}""", params,
    ).fetchone()
    return {"predictions": int(row["predictions"] or 0), "resolved": int(row["resolved"] or 0)}


def machine_shadow_status(store: Any) -> dict[str, Any]:
    adaptive_path = store.path.with_name(ML_MODEL_FILENAME)
    champion_path = store.path.with_name(ML_CHAMPION_FILENAME)
    state_path = store.path.with_name(ML_STATE_FILENAME)
    adaptive = _load_cached(adaptive_path)
    champion = _load_cached(champion_path)
    state = _load_state(state_path)
    if adaptive is None:
        return {
            "installed": False,
            "lineage_version": ML_LINEAGE_VERSION,
            "authority_pct": 0,
            "adaptive_model_path": str(adaptive_path),
            "champion_model_path": str(champion_path),
            "auto_learning": True,
            "note": "run starts the continual trainer automatically; ml-train can bootstrap immediately",
        }
    comparisons = [
        metric.get("beats_balanced_brier")
        for network in adaptive.horizons.values()
        for metric in network.metrics.values()
        if metric.get("beats_balanced_brier") is not None
    ]
    adaptive_counts = _prediction_counts(store, ML_ADAPTIVE_FAMILY)
    champion_counts = _prediction_counts(store, ML_CHAMPION_FAMILY)
    return {
        "installed": True,
        "lineage_version": ML_LINEAGE_VERSION,
        "authority_pct": 0,
        "auto_learning": True,
        "adaptive": {
            "family": ML_ADAPTIVE_FAMILY,
            "generation": adaptive.generation,
            "model_version": adaptive.model_version,
            "trained_at": adaptive.trained_at,
            "training_anchor_prediction_id": adaptive.training_anchor_prediction_id,
            **adaptive_counts,
        },
        "champion": None if champion is None else {
            "family": ML_CHAMPION_FAMILY,
            "generation": champion.generation,
            "model_version": champion.model_version,
            "trained_at": champion.trained_at,
            **champion_counts,
        },
        "continual_state": {
            "adaptive_updates": int(state.get("adaptive_updates") or 0),
            "champion_promotions": int(state.get("champion_promotions") or 0),
            "rejected_for_champion": int(state.get("rejected_for_champion") or 0),
            "last_event": state.get("last_event"),
            "last_event_at": state.get("last_event_at"),
            "last_selected_profile": state.get("last_selected_profile"),
            "last_drift": state.get("last_drift"),
            "last_validation": state.get("last_validation"),
            "last_evaluated_prediction_id": int(state.get("last_evaluated_prediction_id") or 0),
        },
        "bootstrap_validation": {
            "outputs_compared_to_balanced": len(comparisons),
            "outputs_beating_balanced_brier": sum(1 for value in comparisons if value),
        },
        "training_config": adaptive.training_config,
        "validation": {
            str(h): {
                "train_rows": n.train_rows,
                "validation_rows": n.validation_rows,
                "metrics": n.metrics,
            } for h, n in adaptive.horizons.items()
        },
        "model_paths": {
            "adaptive": str(adaptive_path),
            "champion": str(champion_path),
            "state": str(state_path),
        },
    }
