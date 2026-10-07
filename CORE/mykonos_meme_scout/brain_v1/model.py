"""Trainable causal sequence mixture, deliberately without an execution interface.

The recurrent encoder, seven specialist hidden layers, output layers and learned
mixture gate all receive gradients. Specialist names describe loss emphasis, not
established edges. Missing labels never become negative examples. A prediction's
disagreement is a diagnostic, not a calibrated confidence interval.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np


SCHEMA_VERSION = 1
TARGET_NAMES = (
    "return_5s", "return_15s", "return_30s", "return_60s", "return_120s",
    "mfe", "mae", "p5", "p10", "p25", "p_crash", "p_liquidity_collapse",
    "p_stagnation", "net_return", "exit_now", "continuation",
)
PROBABILITY_TARGETS = frozenset((
    "p5", "p10", "p25", "p_crash", "p_liquidity_collapse", "p_stagnation",
    "exit_now", "continuation",
))
SPECIALIST_NAMES = (
    "rocket_momentum", "pullback_recovery", "early_launch", "flow_continuation",
    "reversal", "rug_crash_defense", "exit_profit_taking",
)
_EMPHASIS = (
    ("return_5s", "return_15s", "p10", "p25", "net_return"),
    ("return_30s", "return_60s", "return_120s", "mae", "net_return"),
    ("return_5s", "p5", "p10", "p_crash", "net_return"),
    ("return_15s", "return_30s", "continuation", "net_return"),
    ("return_60s", "return_120s", "p5", "mae", "net_return"),
    ("p_crash", "p_liquidity_collapse", "mae", "net_return"),
    ("exit_now", "continuation", "mfe", "mae", "net_return"),
)


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sigmoid(value: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(value, -40.0, 40.0)))


class SequenceBrain:
    """Small numpy RNN trained by complete BPTT and Adam on causal sequences.

    Inputs are observed rows in time order; gaps are explicit feature values,
    never imputed ticks. Right padding carries the preceding hidden state and
    contributes no recurrent update. Feature/target normalizers are fitted once,
    exclusively on the training records, then frozen through subsequent fits.
    Returns, including MFE/MAE and net_return, are percentage points.
    """

    def __init__(self, feature_names: Sequence[str], hidden_size: int = 24,
                 seed: int = 20260907, max_steps: int = 64):
        self.feature_names = tuple(str(x) for x in feature_names)
        if not self.feature_names or len(set(self.feature_names)) != len(self.feature_names):
            raise ValueError("feature_names must be nonempty and unique")
        if not 2 <= int(hidden_size) <= 256 or not 2 <= int(max_steps) <= 2048:
            raise ValueError("invalid hidden_size or max_steps")
        self.hidden_size, self.seed, self.max_steps = int(hidden_size), int(seed), int(max_steps)
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.means = np.zeros(len(self.feature_names), dtype=np.float64)
        self.stds = np.ones(len(self.feature_names), dtype=np.float64)
        self.target_means = np.zeros(len(TARGET_NAMES), dtype=np.float64)
        self.target_stds = np.ones(len(TARGET_NAMES), dtype=np.float64)
        self.target_support = {name: 0 for name in TARGET_NAMES}
        self.training_coins: set[str] = set()
        self.training_record_ids: set[str] = set()
        self.training_source_domains: set[str] = set()
        self.training_records = 0
        self.fit_calls = 0
        self.history: list[float] = []
        self.normalization_fitted = False
        self.trained = False
        self.model_sha256: str | None = None
        self.calibration = {"fitted": False}
        self.inference_contract = "v1.0.0:net>max(1,2*disagreement);tail<.25;external_safety;costs2.9+impact"
        self._classification = np.array([name in PROBABILITY_TARGETS for name in TARGET_NAMES])
        # A horizon that matures only in a later fit still has a sensible,
        # frozen percentage-point scale; absent labels cannot fit statistics.
        self.target_stds[~self._classification] = 5.0
        self._specialist_emphasis = np.ones((len(SPECIALIST_NAMES), len(TARGET_NAMES)))
        for specialist, emphasized in enumerate(_EMPHASIS):
            for name in emphasized:
                self._specialist_emphasis[specialist, TARGET_NAMES.index(name)] = 2.5
        self._specialist_emphasis /= self._specialist_emphasis.mean(axis=1, keepdims=True)
        rng = np.random.default_rng(self.seed)
        f, h, s, o = len(self.feature_names), self.hidden_size, len(SPECIALIST_NAMES), len(TARGET_NAMES)
        recurrent, _ = np.linalg.qr(rng.normal(size=(h, h)))
        self.params = {
            "wx": rng.normal(0, 1 / np.sqrt(f), (f, h)), "wh": recurrent * .8,
            "bh": np.zeros(h),
            "ws": rng.normal(0, 1 / np.sqrt(h), (s, h, h)), "bs": np.zeros((s, h)),
            "wo": rng.normal(0, .2 / np.sqrt(h), (s, h, o)), "bo": np.zeros((s, o)),
            "wm": rng.normal(0, 1 / np.sqrt(h), (h, h)), "bm": np.zeros(h),
            "wg": rng.normal(0, .2 / np.sqrt(h), (h, s)), "bg": np.zeros(s),
        }

    @property
    def authority_pct(self) -> int:
        return 0

    def _sequence(self, values: Sequence[Sequence[float]]) -> np.ndarray:
        array = np.asarray(values, dtype=np.float64)
        if array.ndim != 2 or array.shape[1] != len(self.feature_names) or not len(array):
            raise ValueError("sequence must contain observed rows with the declared feature width")
        if not np.isfinite(array).all():
            raise ValueError("nonfinite sequence features; use explicit missingness features")
        return array[-self.max_steps:]

    def _batch(self, sequences: Sequence[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
        lengths = [len(x) for x in sequences]
        x = np.zeros((len(sequences), max(lengths), len(self.feature_names)))
        valid = np.zeros((len(sequences), max(lengths)), dtype=np.float64)
        for i, values in enumerate(sequences):
            x[i, :lengths[i]] = np.clip((values - self.means) / self.stds, -10, 10)
            valid[i, :lengths[i]] = 1
        return x, valid

    def _forward(self, x: np.ndarray, valid: np.ndarray) -> dict[str, Any]:
        p = self.params
        h = np.zeros((len(x), self.hidden_size))
        states = [h]
        candidates = []
        for t in range(x.shape[1]):
            candidate = np.tanh(x[:, t] @ p["wx"] + h @ p["wh"] + p["bh"])
            m = valid[:, t, None]
            h = m * candidate + (1 - m) * h
            candidates.append(candidate)
            states.append(h)
        specialist = np.tanh(np.einsum("bh,shk->bsk", h, p["ws"]) + p["bs"])
        logits = np.einsum("bsh,sho->bso", specialist, p["wo"]) + p["bo"]
        meta_hidden = np.tanh(h @ p["wm"] + p["bm"])
        gate_logits = meta_hidden @ p["wg"] + p["bg"]
        gate_logits -= gate_logits.max(axis=1, keepdims=True)
        gate = np.exp(gate_logits)
        gate /= gate.sum(axis=1, keepdims=True)
        mixture = np.einsum("bs,bso->bo", gate, logits)
        return {"states": states, "candidates": candidates, "specialist": specialist,
                "logits": logits, "meta_hidden": meta_hidden, "gate": gate, "mixture": mixture}

    def _point_loss(self, logits: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        difference = logits - y
        absolute = np.abs(difference)
        loss = np.where(absolute <= 1, .5 * difference ** 2, absolute - .5)
        derivative = np.clip(difference, -1, 1)
        c = self._classification
        loss[..., c] = np.logaddexp(0, logits[..., c]) - y[..., c] * logits[..., c]
        derivative[..., c] = _sigmoid(logits[..., c]) - y[..., c]
        return loss, derivative

    def _loss_and_grad(self, x: np.ndarray, valid: np.ndarray, y: np.ndarray,
                       mask: np.ndarray, row_weights: np.ndarray,
                       l2: float = 1e-5) -> tuple[float, dict[str, np.ndarray]]:
        """Differentiable masked objective, also exposed for finite-difference QA."""
        cache = self._forward(x, valid)
        p = self.params
        weights = mask * row_weights[:, None]
        denominator = max(float(weights.sum()), 1e-12)
        loss, derivative = self._point_loss(cache["mixture"], y)
        objective = float((loss * weights).sum() / denominator)
        d_mix = derivative * weights / denominator
        specialist_weights = weights[:, None, :] * self._specialist_emphasis[None, :, :]
        specialist_denominator = max(float(specialist_weights.sum()), 1e-12)
        own_loss, own_derivative = self._point_loss(cache["logits"], y[:, None, :])
        objective += .35 * float((own_loss * specialist_weights).sum() / specialist_denominator)
        d_logits = d_mix[:, None, :] * cache["gate"][:, :, None]
        d_logits += .35 * own_derivative * specialist_weights / specialist_denominator
        d_gate = np.einsum("bo,bso->bs", d_mix, cache["logits"])
        d_gate_logits = cache["gate"] * (d_gate - (d_gate * cache["gate"]).sum(axis=1, keepdims=True))
        gradients: dict[str, np.ndarray] = {}
        gradients["wg"] = cache["meta_hidden"].T @ d_gate_logits
        gradients["bg"] = d_gate_logits.sum(axis=0)
        d_meta = (d_gate_logits @ p["wg"].T) * (1 - cache["meta_hidden"] ** 2)
        gradients["wm"] = cache["states"][-1].T @ d_meta
        gradients["bm"] = d_meta.sum(axis=0)
        gradients["wo"] = np.einsum("bsh,bso->sho", cache["specialist"], d_logits)
        gradients["bo"] = d_logits.sum(axis=0)
        d_specialist = np.einsum("bso,sho->bsh", d_logits, p["wo"]) * (1 - cache["specialist"] ** 2)
        gradients["ws"] = np.einsum("bh,bsk->shk", cache["states"][-1], d_specialist)
        gradients["bs"] = d_specialist.sum(axis=0)
        dh = np.einsum("bsk,shk->bh", d_specialist, p["ws"]) + d_meta @ p["wm"].T
        for name in ("wx", "wh", "bh"):
            gradients[name] = np.zeros_like(p[name])
        for t in range(x.shape[1] - 1, -1, -1):
            m = valid[:, t, None]
            dz = dh * (1 - cache["candidates"][t] ** 2) * m
            gradients["wx"] += x[:, t].T @ dz
            gradients["wh"] += cache["states"][t].T @ dz
            gradients["bh"] += dz.sum(axis=0)
            dh = dz @ p["wh"].T + dh * (1 - m)
        for name, value in p.items():
            if name.startswith("w"):
                objective += .5 * l2 * float((value * value).sum())
                gradients[name] += l2 * value
        return objective, gradients

    def _prepare(self, records: Sequence[dict[str, Any]], *, fit_normalization: bool
                 ) -> tuple[list[np.ndarray], np.ndarray, np.ndarray, np.ndarray]:
        sequences = [self._sequence(row["sequence"]) for row in records]
        y = np.zeros((len(records), len(TARGET_NAMES)))
        mask = np.zeros_like(y)
        coin_keys = [str(row.get("coin_id") or row.get("address") or "") for row in records]
        if any(not key for key in coin_keys):
            raise ValueError("each training record needs a coin_id or address for coin balancing")
        counts = Counter(coin_keys)
        weights = np.array([float(row.get("sample_weight", 1.0)) / counts[key]
                            for row, key in zip(records, coin_keys)])
        if not np.isfinite(weights).all() or np.any(weights <= 0):
            raise ValueError("sample weights must be finite and positive")
        weights *= len(weights) / weights.sum()
        for i, row in enumerate(records):
            for j, name in enumerate(TARGET_NAMES):
                value = (row.get("targets") or {}).get(name)
                if value is None:
                    continue
                value = float(value)
                if not np.isfinite(value):
                    raise ValueError("nonfinite labels must be explicitly masked with None")
                if name in PROBABILITY_TARGETS and not 0 <= value <= 1:
                    raise ValueError("probability labels must lie in [0,1]")
                y[i, j], mask[i, j] = value, 1
        if not mask.any():
            raise ValueError("training requires at least one observed target")
        if fit_normalization:
            # Each coin has equal total weight; repeated windows/policies cannot
            # dominate either the normalizer or the training objective.
            total = float(weights.sum())
            self.means = sum(w * seq.mean(axis=0) for w, seq in zip(weights, sequences)) / total
            variance = sum(w * ((seq - self.means) ** 2).mean(axis=0)
                           for w, seq in zip(weights, sequences)) / total
            self.stds = np.maximum(np.sqrt(variance), 1e-4)
            for j, name in enumerate(TARGET_NAMES):
                selected = mask[:, j].astype(bool)
                if selected.any() and name not in PROBABILITY_TARGETS:
                    value, weight = y[selected, j], weights[selected]
                    mean = float(np.average(value, weights=weight))
                    std = float(np.sqrt(np.average((value - mean) ** 2, weights=weight)))
                    self.target_means[j], self.target_stds[j] = mean, max(std, 5.0)
            self.normalization_fitted = True
        y = (y - self.target_means) / self.target_stds
        y[mask == 0] = 0
        return sequences, y, mask, weights

    def fit(self, records: Iterable[dict[str, Any]], *, epochs: int = 12,
            batch_size: int = 32, learning_rate: float = .003,
            l2: float = 1e-5) -> dict[str, Any]:
        records = list(records)
        if not records or epochs < 1 or batch_size < 1 or not 0 < learning_rate <= .1 or l2 < 0:
            raise ValueError("invalid training arguments")
        sequences, y, mask, weights = self._prepare(records, fit_normalization=not self.normalization_fitted)
        rng = np.random.default_rng(self.seed + self.fit_calls)
        first = {name: np.zeros_like(value) for name, value in self.params.items()}
        second = {name: np.zeros_like(value) for name, value in self.params.items()}
        steps = 0
        epoch_losses = []
        for _ in range(int(epochs)):
            order = rng.permutation(len(records))
            loss_sum, batches = 0.0, 0
            for start in range(0, len(order), int(batch_size)):
                idx = order[start:start + int(batch_size)]
                x, valid = self._batch([sequences[i] for i in idx])
                loss, gradients = self._loss_and_grad(x, valid, y[idx], mask[idx], weights[idx], l2)
                if not np.isfinite(loss) or any(not np.isfinite(g).all() for g in gradients.values()):
                    raise ValueError("nonfinite training loss or gradient; artifact not publishable")
                norm = np.sqrt(sum(float((g * g).sum()) for g in gradients.values()))
                scale = min(1.0, 5.0 / max(float(norm), 1e-12))
                steps += 1
                for name, gradient in gradients.items():
                    gradient = gradient * scale
                    first[name] = .9 * first[name] + .1 * gradient
                    second[name] = .999 * second[name] + .001 * gradient * gradient
                    self.params[name] -= learning_rate * (first[name] / (1 - .9 ** steps)) / (
                        np.sqrt(second[name] / (1 - .999 ** steps)) + 1e-8)
                loss_sum += loss
                batches += 1
            epoch_losses.append(loss_sum / batches)
        for row in records:
            self.training_coins.add(str(row.get("coin_id") or row.get("address")))
            if row.get("record_id") is not None:
                self.training_record_ids.add(str(row["record_id"]))
            if row.get("source_domain") is not None:
                self.training_source_domains.add(str(row["source_domain"]))
        for j, name in enumerate(TARGET_NAMES):
            self.target_support[name] += int(mask[:, j].sum())
        self.training_records += len(records)
        self.fit_calls += 1
        self.trained = True
        self.model_sha256 = None
        self.history.extend(epoch_losses)
        return {"epoch_loss": epoch_losses, "training_records": len(records),
                "unique_coins": len({str(row.get("coin_id") or row.get("address")) for row in records}),
                "target_support": dict(self.target_support), "authority_pct": 0,
                "trainable_layers": "recurrent_encoder_specialist_hidden_output_meta_gate"}

    def _decode(self, logits: np.ndarray) -> np.ndarray:
        values = logits * self.target_stds + self.target_means
        values[..., self._classification] = _sigmoid(logits[..., self._classification])
        return values

    def predict(self, sequence: Sequence[Sequence[float]],
                position_context: dict[str, float] | None = None) -> dict[str, Any]:
        if not self.trained:
            raise ValueError("untrained sequence brain has no usable prediction")
        values = self._sequence(sequence).copy()
        if position_context:
            for name, value in position_context.items():
                if name not in self.feature_names or not np.isfinite(float(value)):
                    raise ValueError("position context must match finite declared causal features")
                # The supplied state is known at this cutoff only. Applying it
                # to earlier ticks would leak later position knowledge backward.
                values[-1, self.feature_names.index(name)] = float(value)
        x, valid = self._batch([values])
        cache = self._forward(x, valid)
        decoded, specialists = self._decode(cache["mixture"])[0], self._decode(cache["logits"])[0]

        def as_targets(vector: np.ndarray) -> dict[str, float | None]:
            return {name: float(vector[i]) if self.target_support[name] > 0 else None
                    for i, name in enumerate(TARGET_NAMES)}

        predictions = as_targets(decoded)
        net_index = TARGET_NAMES.index("net_return")
        uncertainty = float(np.std(specialists[:, net_index])) if self.target_support["net_return"] else None
        tail = [predictions[name] for name in ("p_crash", "p_liquidity_collapse")]
        known_tail = [float(v) for v in tail if v is not None]
        gate = cache["gate"][0]
        return {"targets": predictions,
                "specialists": {name: as_targets(specialists[i]) for i, name in enumerate(SPECIALIST_NAMES)},
                "meta": {"net_edge_pct": predictions["net_return"], "uncertainty": uncertainty,
                         "uncertainty_kind": "cross_specialist_disagreement_not_calibrated",
                         "tail_risk": max(known_tail) if len(known_tail) == 2 else None,
                         "best_specialist": SPECIALIST_NAMES[int(np.argmax(gate))],
                         "specialist_weights": {name: float(gate[i]) for i, name in enumerate(SPECIALIST_NAMES)},
                         "specialist_authority_pct": {name: 0 for name in SPECIALIST_NAMES},
                         "learned_gate": True, "promotion_evidence": False},
                "authority_pct": 0}

    def as_dict(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "version": "1.0.0",
                "architecture": "causal_rnn_seven_neural_specialists_learned_mixture_gate",
                "authority_pct": 0, "execution_authority_pct": 0,
                "feature_names": list(self.feature_names), "target_names": list(TARGET_NAMES),
                "specialist_names": list(SPECIALIST_NAMES), "hidden_size": self.hidden_size,
                "seed": self.seed, "max_steps": self.max_steps, "created_at": self.created_at,
                "means": self.means.tolist(), "stds": self.stds.tolist(),
                "target_means": self.target_means.tolist(), "target_stds": self.target_stds.tolist(),
                "target_support": self.target_support, "training_coins": sorted(self.training_coins),
                "training_record_ids": sorted(self.training_record_ids),
                "training_source_domains": sorted(self.training_source_domains),
                "training_records": self.training_records, "fit_calls": self.fit_calls,
                "history": self.history, "normalization_fitted": self.normalization_fitted,
                "calibration": self.calibration, "inference_contract": self.inference_contract,
                "trained": self.trained, "params": {name: value.tolist() for name, value in self.params.items()}}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SequenceBrain":
        if (payload.get("schema_version") != SCHEMA_VERSION or
                any(type(payload.get(name)) is not int or payload[name] != 0
                    for name in ("authority_pct", "execution_authority_pct"))):
            raise ValueError("unsupported or nonzero-authority model")
        if payload.get("target_names") != list(TARGET_NAMES) or payload.get("specialist_names") != list(SPECIALIST_NAMES):
            raise ValueError("model output schema mismatch")
        model = cls(payload["feature_names"], payload["hidden_size"], payload["seed"], payload["max_steps"])
        if set(payload["params"]) != set(model.params):
            raise ValueError("model parameter set mismatch")
        for name, initial in model.params.items():
            value = np.asarray(payload["params"][name], dtype=np.float64)
            if value.shape != initial.shape or not np.isfinite(value).all():
                raise ValueError("invalid model parameter shape or value")
            model.params[name] = value
        for name in ("means", "stds", "target_means", "target_stds"):
            value = np.asarray(payload[name], dtype=np.float64)
            if value.shape != getattr(model, name).shape or not np.isfinite(value).all():
                raise ValueError("invalid normalization schema")
            if name.endswith("stds") and np.any(value <= 0):
                raise ValueError("normalization scales must be positive")
            setattr(model, name, value)
        support = payload["target_support"]
        if set(support) != set(TARGET_NAMES) or any(type(v) is not int or v < 0 for v in support.values()):
            raise ValueError("invalid target support")
        model.target_support = dict(support)
        model.training_coins = set(map(str, payload["training_coins"]))
        model.training_record_ids = set(map(str, payload["training_record_ids"]))
        model.training_source_domains = set(map(str, payload["training_source_domains"]))
        for name in ("training_records", "fit_calls", "created_at", "normalization_fitted", "trained"):
            setattr(model, name, payload[name])
        model.calibration = payload.get("calibration", {"fitted": False})
        if model.calibration.get("fitted") and (len(model.calibration.get("weights", [])) != 2 or not np.isfinite(model.calibration["weights"]).all()):
            raise ValueError("invalid calibration")
        if payload.get("inference_contract", model.inference_contract) != model.inference_contract:
            raise ValueError("inference policy mismatch")
        model.history = [float(v) for v in payload["history"]]
        if (not all(np.isfinite(model.history)) or type(model.trained) is not bool or
                type(model.normalization_fitted) is not bool or
                type(model.training_records) is not int or model.training_records < 0 or
                type(model.fit_calls) is not int or model.fit_calls < 0 or
                (model.trained and (not model.normalization_fitted or not model.training_coins or
                                   model.training_records == 0 or model.fit_calls == 0 or
                                   not any(model.target_support.values())))):
            raise ValueError("invalid training metadata")
        return model

    def save(self, path: Path) -> str:
        if not self.trained:
            raise ValueError("cannot publish an untrained model")
        payload = self.as_dict()
        digest = hashlib.sha256(_canonical(payload)).hexdigest()
        serialized = _canonical({**payload, "model_sha256": digest}) + b"\n"
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as handle:
                handle.write(serialized)
                handle.flush()
        except FileExistsError:
            if path.read_bytes() != serialized:
                raise FileExistsError("immutable model path already contains a different artifact") from None
        self.model_sha256 = digest
        return digest

    @classmethod
    def load(cls, path: Path) -> "SequenceBrain":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        digest = payload.pop("model_sha256", None)
        if not digest or digest != hashlib.sha256(_canonical(payload)).hexdigest():
            raise ValueError("model checksum mismatch")
        model = cls.from_dict(payload)
        if not model.trained:
            raise ValueError("untrained artifact cannot be loaded for inference")
        model.model_sha256 = digest
        return model
