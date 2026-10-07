from __future__ import annotations

import hashlib
import json
import math
import random
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from .sqlite_source import sha256_file as _sha256_file, verified_source


TEMPORAL_DATASET_SCHEMA_VERSION = 1
TEMPORAL_MODEL_SCHEMA_VERSION = 1
TEMPORAL_LINEAGE_VERSION = "0.7.0-temporal-shadow"
TEMPORAL_MODEL_FILENAME = "temporal-shadow-v0.7.0.json"
SIMULATION_DOMAIN = "SIMULATION"
SPLITS = ("train", "validation", "test")
OUTPUT_NAMES = ("p5", "p10", "p25", "p_rug", "p_stagnation")
# Profit heads and risk heads each receive exactly half of the training objective.
OUTPUT_LOSS_WEIGHTS = (1 / 6, 1 / 6, 1 / 6, 1 / 4, 1 / 4)
FEATURE_NAMES = (
    "price_log_return_1s",
    "price_log_return_from_window_start",
    "liquidity_log_return_1s",
    "liquidity_log_return_from_window_start",
    "market_cap_log_return_1s",
    "volume_to_liquidity_bps",
    "transaction_count_1s",
    "buy_sell_imbalance_1s",
    "social_mentions_1s",
    "social_sentiment",
    "smart_wallet_flow_to_liquidity_bps",
    "social_data_available",
    "rug_seen",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0]) for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _safe_log_ratio(current: float, previous: float) -> float:
    if current <= 0 or previous <= 0:
        return 0.0
    return math.log(current / previous)


def _tick_features(window: Sequence[sqlite3.Row]) -> list[list[float]]:
    """Encode an already-observed window; no row after the cutoff is accepted."""
    if not window:
        raise ValueError("Temporal window cannot be empty")
    base = window[0]
    encoded: list[list[float]] = []
    rug_seen = False
    for index, tick in enumerate(window):
        previous = window[index - 1] if index else tick
        rug_seen = rug_seen or bool(tick["rug_event"])
        liquidity = max(float(tick["liquidity_usd"]), 1e-9)
        transactions = int(tick["buys_1s"]) + int(tick["sells_1s"])
        encoded.append([
            _safe_log_ratio(float(tick["price_usd"]), float(previous["price_usd"])),
            _safe_log_ratio(float(tick["price_usd"]), float(base["price_usd"])),
            _safe_log_ratio(float(tick["liquidity_usd"]), float(previous["liquidity_usd"])),
            _safe_log_ratio(float(tick["liquidity_usd"]), float(base["liquidity_usd"])),
            _safe_log_ratio(float(tick["market_cap_usd"]), float(previous["market_cap_usd"])),
            float(tick["volume_1s_usd"]) / liquidity * 10_000.0,
            float(transactions),
            (int(tick["buys_1s"]) - int(tick["sells_1s"])) / max(1, transactions),
            float(tick["social_mentions_1s"]),
            float(tick["social_sentiment"]) if tick["social_data_available"] else 0.0,
            float(tick["smart_wallet_flow_1s_usd"]) / liquidity * 10_000.0,
            float(bool(tick["social_data_available"])),
            float(rug_seen),
        ])
    return encoded


@dataclass(frozen=True, slots=True)
class TemporalRecord:
    record_id: str
    group_id: str
    source_fingerprint: str
    source_sequence_checksum: str
    scenario_id: str
    origin: str
    family: str
    window_start_at: str
    observed_at: str
    target_at: str
    observed_second: int
    horizon_seconds: int
    sequence: list[list[float]]
    labels: dict[str, Any]

    def payload(self) -> dict[str, Any]:
        return {
            "temporal_dataset_schema_version": TEMPORAL_DATASET_SCHEMA_VERSION,
            "record_id": self.record_id,
            "group_id": self.group_id,
            "split": "train",
            "data_domain": SIMULATION_DOMAIN,
            "source_fingerprint": self.source_fingerprint,
            "source_sequence_checksum": self.source_sequence_checksum,
            "scenario_id": self.scenario_id,
            "origin": self.origin,
            "family": self.family,
            "window_start_at": self.window_start_at,
            "observed_at": self.observed_at,
            "target_at": self.target_at,
            "observed_second": self.observed_second,
            "horizon_seconds": self.horizon_seconds,
            "feature_names": list(FEATURE_NAMES),
            "sequence": self.sequence,
            "labels": {name: self.labels[name] for name in (
                "hit_5", "hit_10", "hit_25", "rug", "stagnant",
                "profit_potential_pct", "risk_exposure_pct", "balanced_utility_pct",
            )},
            "provenance": {
                "feature_window_start_second": self.observed_second - len(self.sequence) + 1,
                "feature_cutoff_second": self.observed_second,
                "label_start_second": self.observed_second + 1,
                "label_end_second": self.observed_second + self.horizon_seconds,
                "one_second_cadence": True,
                "profit_risk_weights": {"profit": 0.5, "risk": 0.5},
            },
            "permissions": {
                "temporal_shadow_training": True,
                "market_validation_evidence": False,
                "real_champion_promotion_evidence": False,
                "execution_authority_pct": 0,
            },
        }


def _load_simulation_temporal_records(
    path: Path,
    *,
    window_seconds: int,
    stride_seconds: int,
    horizons_seconds: set[int] | None,
    maximum_rows: int,
) -> tuple[list[TemporalRecord], dict[str, Any]]:
    with verified_source(path) as (connection, integrity):
        source_hash = integrity["source_fingerprint"]
        tables = _table_names(connection)
        required = {
            "simulation_metadata", "simulation_scenarios", "simulation_ticks",
            "simulation_training_examples",
        }
        if not required.issubset(tables):
            raise ValueError(f"Temporal sources must be v0.6 simulation databases: {path}")
        metadata = connection.execute(
            "SELECT * FROM simulation_metadata WHERE id=1"
        ).fetchone()
        if (
            metadata is None
            or metadata["data_domain"] != SIMULATION_DOMAIN
            or not int(metadata["eligible_for_neural_research"])
            or int(metadata["eligible_for_real_training"])
            or int(metadata["eligible_for_real_champion"])
            or float(metadata["execution_authority_pct"]) != 0.0
        ):
            raise ValueError("Simulation source has an invalid research-only domain marker")

        examples = connection.execute(
            """SELECT e.*,s.origin,s.family,s.sequence_checksum
            FROM simulation_training_examples e
            JOIN simulation_scenarios s ON s.scenario_id=e.scenario_id
            ORDER BY s.sequence_checksum,e.observed_second,e.horizon_seconds"""
        ).fetchall()
        ticks_by_scenario: dict[str, list[sqlite3.Row]] = {}
        records: list[TemporalRecord] = []
        skipped_incomplete = 0
        for example in examples:
            observed_second = int(example["observed_second"])
            horizon_seconds = int(example["horizon_seconds"])
            if observed_second < window_seconds - 1:
                continue
            if observed_second % stride_seconds:
                continue
            if horizons_seconds is not None and horizon_seconds not in horizons_seconds:
                continue
            scenario_id = str(example["scenario_id"])
            if scenario_id not in ticks_by_scenario:
                ticks_by_scenario[scenario_id] = connection.execute(
                    """SELECT * FROM simulation_ticks WHERE scenario_id=?
                    ORDER BY second_offset""",
                    (scenario_id,),
                ).fetchall()
            ticks = ticks_by_scenario[scenario_id]
            start_second = observed_second - window_seconds + 1
            if observed_second + horizon_seconds >= len(ticks):
                skipped_incomplete += 1
                continue
            window = ticks[start_second:observed_second + 1]
            if (
                len(window) != window_seconds
                or [int(row["second_offset"]) for row in window]
                != list(range(start_second, observed_second + 1))
            ):
                skipped_incomplete += 1
                continue
            labels = json.loads(str(example["labels_json"]))
            sequence_checksum = str(example["sequence_checksum"])
            key = f"{sequence_checksum}|{window_seconds}|{observed_second}|{horizon_seconds}"
            records.append(TemporalRecord(
                record_id="temporal-" + _sha256_text(key)[:32],
                group_id="scenario-" + sequence_checksum,
                source_fingerprint=source_hash,
                source_sequence_checksum=sequence_checksum,
                scenario_id=scenario_id,
                origin=str(example["origin"]),
                family=str(example["family"]),
                window_start_at=str(window[0]["captured_at"]),
                observed_at=str(window[-1]["captured_at"]),
                target_at=str(ticks[observed_second + horizon_seconds]["captured_at"]),
                observed_second=observed_second,
                horizon_seconds=horizon_seconds,
                sequence=_tick_features(window),
                labels=labels,
            ))
            if len(records) >= maximum_rows:
                break
    return records, {
        "role": "simulation",
        "path": str(path),
        **integrity,
        "rows_read": len(records),
        "skipped_incomplete_windows": skipped_incomplete,
        "opened_read_only": True,
        "data_domain": SIMULATION_DOMAIN,
    }


def _write_jsonl(path: Path, payloads: Iterable[dict[str, Any]]) -> dict[str, Any]:
    temporary = path.with_suffix(path.suffix + ".tmp")
    digest = hashlib.sha256()
    rows = 0
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for payload in payloads:
            line = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
            handle.write(line)
            digest.update(line.encode("utf-8"))
            rows += 1
    temporary.replace(path)
    return {"path": str(path), "rows": rows, "sha256": digest.hexdigest()}


def build_temporal_dataset(
    output_directory: Path,
    *,
    simulation_databases: Sequence[Path],
    window_seconds: int = 60,
    stride_seconds: int = 30,
    horizons_seconds: Sequence[int] = (60, 180, 300, 600, 900),
    max_rows_per_source: int = 50_000,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Create one-second shadow windows from verified idle SQLite sources."""
    window_seconds = int(window_seconds)
    stride_seconds = int(stride_seconds)
    max_rows_per_source = int(max_rows_per_source)
    if window_seconds < 2:
        raise ValueError("window_seconds must be at least two")
    if stride_seconds < 1:
        raise ValueError("stride_seconds must be positive")
    if max_rows_per_source < 1:
        raise ValueError("max_rows_per_source must be positive")
    horizons = {int(value) for value in horizons_seconds if int(value) > 0}
    if not horizons:
        raise ValueError("At least one positive horizon is required")
    sources = [Path(path).resolve() for path in simulation_databases]
    if not sources:
        raise ValueError("At least one v0.6 simulation database is required")
    if len(set(sources)) != len(sources):
        raise ValueError("Each temporal source database may be supplied once")
    for source in sources:
        if not source.is_file():
            raise FileNotFoundError(f"Temporal source database not found: {source}")

    output_directory = Path(output_directory).resolve()
    if output_directory in sources:
        raise ValueError("Temporal dataset output must differ from every source database")
    output_directory.mkdir(parents=True, exist_ok=True)
    split_paths = {name: output_directory / f"{name}.jsonl" for name in SPLITS}
    manifest_path = output_directory / "temporal_dataset_manifest.json"
    existing = [path for path in (*split_paths.values(), manifest_path) if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(
            "Temporal dataset output exists; use a new directory or explicitly enable overwrite"
        )

    records: list[TemporalRecord] = []
    source_manifest: list[dict[str, Any]] = []
    for source in sources:
        loaded, details = _load_simulation_temporal_records(
            source,
            window_seconds=window_seconds,
            stride_seconds=stride_seconds,
            horizons_seconds=horizons,
            maximum_rows=max_rows_per_source,
        )
        records.extend(loaded)
        source_manifest.append(details)

    unique: dict[str, TemporalRecord] = {}
    duplicates = 0
    for record in records:
        if record.record_id in unique:
            duplicates += 1
        else:
            unique[record.record_id] = record
    records = sorted(
        unique.values(), key=lambda row: (
            row.source_sequence_checksum, row.observed_second, row.horizon_seconds
        )
    )
    if not records:
        raise ValueError("No complete causal temporal windows were found")

    causal = all(
        datetime.fromisoformat(row.target_at) > datetime.fromisoformat(row.observed_at)
        for row in records
    )
    contiguous = all(len(row.sequence) == window_seconds for row in records)
    source_unchanged = all(details["source_main_wal_verified_unchanged"] for details in source_manifest)
    if not (causal and contiguous and source_unchanged):
        raise RuntimeError("Temporal dataset leakage or source-integrity invariant failed")

    split_files = {
        "train": _write_jsonl(split_paths["train"], (row.payload() for row in records)),
        "validation": _write_jsonl(split_paths["validation"], ()),
        "test": _write_jsonl(split_paths["test"], ()),
    }
    manifest = {
        "temporal_dataset_schema_version": TEMPORAL_DATASET_SCHEMA_VERSION,
        "created_at": _utc_now(),
        "data_domain": SIMULATION_DOMAIN,
        "feature_names": list(FEATURE_NAMES),
        "output_names": list(OUTPUT_NAMES),
        "window_seconds": window_seconds,
        "stride_seconds": stride_seconds,
        "horizons_seconds": sorted(horizons),
        "sources": source_manifest,
        "split_files": split_files,
        "records": len(records),
        "scenario_groups": len({row.group_id for row in records}),
        "duplicate_windows_dropped": duplicates,
        "profit_risk_weights": {"profit": 0.5, "risk": 0.5},
        "leakage_checks": {
            "source_databases_opened_read_only": True,
            "source_sha256_unchanged": source_unchanged,
            "source_main_wal_verified_unchanged": source_unchanged,
            "one_second_windows_contiguous": contiguous,
            "features_end_at_observation": True,
            "targets_start_after_observation": causal,
            "whole_scenario_grouping": True,
            "repeat_paths_deduplicated_by_sequence_checksum": True,
            "simulation_confined_to_train_split": True,
            "validation_and_test_require_future_real_evidence": True,
            "preprocessors_fit_on_fit_partition_only": True,
            "simulation_can_promote_real_champion": False,
            "execution_authority_pct": 0,
        },
        "consumer_contract": {
            "training_mode": "temporal_shadow_only",
            "automatic_runtime_integration": False,
            "automatic_model_promotion": False,
            "paper_or_live_decision_input": False,
            "execution_authority_pct": 0,
        },
    }
    temporary = manifest_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(manifest_path)
    return {**manifest, "manifest": str(manifest_path)}


def _labels(row: dict[str, Any]) -> list[float]:
    labels = row["labels"]
    return [
        float(bool(labels["hit_5"])),
        float(bool(labels["hit_10"])),
        float(bool(labels["hit_25"])),
        float(bool(labels["rug"])),
        float(bool(labels["stagnant"])),
    ]


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-min(value, 60.0))
        return 1.0 / (1.0 + z)
    z = math.exp(max(value, -60.0))
    return z / (1.0 + z)


def _means_stds(rows: Sequence[dict[str, Any]]) -> tuple[list[float], list[float]]:
    columns = [[] for _ in FEATURE_NAMES]
    for row in rows:
        for tick in row["sequence"]:
            for index, value in enumerate(tick):
                columns[index].append(float(value))
    means = [sum(column) / len(column) for column in columns]
    stds = []
    for column, mean in zip(columns, means):
        variance = sum((value - mean) ** 2 for value in column) / max(1, len(column) - 1)
        stds.append(max(math.sqrt(variance), 1e-6))
    return means, stds


@dataclass(slots=True)
class TemporalConvNetwork:
    means: list[float]
    stds: list[float]
    kernel_seconds: int
    kernels: list[list[list[float]]]
    hidden_bias: list[float]
    output_weights: list[list[float]]
    output_bias: list[float]

    def _standardize(self, sequence: Sequence[Sequence[float]]) -> list[list[float]]:
        if not sequence or any(len(tick) != len(FEATURE_NAMES) for tick in sequence):
            raise ValueError("Temporal sequence has an invalid feature shape")
        return [
            [
                max(-8.0, min(8.0, (float(value) - self.means[index]) / self.stds[index]))
                for index, value in enumerate(tick)
            ]
            for tick in sequence
        ]

    def _forward_details(
        self, sequence: Sequence[Sequence[float]]
    ) -> tuple[list[list[float]], list[list[float]], list[float], list[float]]:
        standardized = self._standardize(sequence)
        if len(standardized) < self.kernel_seconds:
            raise ValueError("Temporal sequence is shorter than the causal kernel")
        states: list[list[float]] = []
        for end in range(self.kernel_seconds - 1, len(standardized)):
            state = []
            for hidden_index, kernel in enumerate(self.kernels):
                value = self.hidden_bias[hidden_index]
                start = end - self.kernel_seconds + 1
                for lag in range(self.kernel_seconds):
                    value += sum(
                        kernel[lag][feature] * standardized[start + lag][feature]
                        for feature in range(len(FEATURE_NAMES))
                    )
                state.append(math.tanh(value))
            states.append(state)
        pooled = [
            sum(state[hidden] for state in states) / len(states)
            for hidden in range(len(self.kernels))
        ]
        outputs = [
            _sigmoid(self.output_bias[index] + sum(
                self.output_weights[index][hidden] * pooled[hidden]
                for hidden in range(len(pooled))
            ))
            for index in range(len(OUTPUT_NAMES))
        ]
        return standardized, states, pooled, outputs

    def predict(self, sequence: Sequence[Sequence[float]]) -> dict[str, float]:
        outputs = self._forward_details(sequence)[-1]
        return {name: outputs[index] for index, name in enumerate(OUTPUT_NAMES)}

    def as_dict(self) -> dict[str, Any]:
        return {
            "means": self.means,
            "stds": self.stds,
            "kernel_seconds": self.kernel_seconds,
            "kernels": self.kernels,
            "hidden_bias": self.hidden_bias,
            "output_weights": self.output_weights,
            "output_bias": self.output_bias,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "TemporalConvNetwork":
        return cls(
            means=[float(value) for value in raw["means"]],
            stds=[float(value) for value in raw["stds"]],
            kernel_seconds=int(raw["kernel_seconds"]),
            kernels=[[[float(value) for value in tick] for tick in kernel]
                     for kernel in raw["kernels"]],
            hidden_bias=[float(value) for value in raw["hidden_bias"]],
            output_weights=[[float(value) for value in row] for row in raw["output_weights"]],
            output_bias=[float(value) for value in raw["output_bias"]],
        )


@dataclass(slots=True)
class TemporalShadowBundle:
    trained_at: str
    dataset_manifest_sha256: str
    dataset_manifest_path: str
    network: TemporalConvNetwork
    training_config: dict[str, Any]
    metrics: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "temporal_model_schema_version": TEMPORAL_MODEL_SCHEMA_VERSION,
            "lineage_version": TEMPORAL_LINEAGE_VERSION,
            "model_family": "temporal_causal_conv_shadow_v070",
            "trained_at": self.trained_at,
            "dataset_manifest_sha256": self.dataset_manifest_sha256,
            "dataset_manifest_path": self.dataset_manifest_path,
            "feature_names": list(FEATURE_NAMES),
            "output_names": list(OUTPUT_NAMES),
            "profit_risk_loss_weights": {
                "profit": 0.5,
                "risk": 0.5,
                "per_output": dict(zip(OUTPUT_NAMES, OUTPUT_LOSS_WEIGHTS)),
            },
            "shadow_only": True,
            "eligible_for_real_champion": False,
            "runtime_integration_enabled": False,
            "execution_authority_pct": 0,
            "training_config": self.training_config,
            "metrics": self.metrics,
            "network": self.network.as_dict(),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "TemporalShadowBundle":
        if raw.get("lineage_version") != TEMPORAL_LINEAGE_VERSION:
            raise ValueError("Unsupported temporal model lineage")
        if float(raw.get("execution_authority_pct") or 0.0) != 0.0:
            raise ValueError("Temporal v0.7 models must have zero execution authority")
        if not bool(raw.get("shadow_only")):
            raise ValueError("Temporal v0.7 models must remain shadow-only")
        if bool(raw.get("eligible_for_real_champion")):
            raise ValueError("Temporal v0.7 models cannot promote the real champion")
        if bool(raw.get("runtime_integration_enabled")):
            raise ValueError("Temporal v0.7 models cannot be runtime decision inputs")
        if tuple(raw.get("feature_names") or ()) != FEATURE_NAMES:
            raise ValueError("Temporal model feature schema does not match v0.7")
        return cls(
            trained_at=str(raw["trained_at"]),
            dataset_manifest_sha256=str(raw["dataset_manifest_sha256"]),
            dataset_manifest_path=str(raw["dataset_manifest_path"]),
            network=TemporalConvNetwork.from_dict(raw["network"]),
            training_config=dict(raw.get("training_config") or {}),
            metrics=dict(raw.get("metrics") or {}),
        )

    def save(self, path: Path, *, overwrite: bool = False) -> None:
        path = Path(path).resolve()
        if path.exists() and not overwrite:
            raise FileExistsError("Temporal model exists; use a new path or explicitly enable overwrite")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(self.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(path)

    @classmethod
    def load(cls, path: Path) -> "TemporalShadowBundle":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def _load_split(dataset_directory: Path, manifest: dict[str, Any], split: str) -> list[dict[str, Any]]:
    details = manifest["split_files"][split]
    path = Path(details["path"])
    if not path.is_absolute():
        path = dataset_directory / path
    if not path.is_file() or _sha256_file(path) != details["sha256"]:
        raise ValueError(f"Temporal {split} split is missing or its checksum changed")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if len(rows) != int(details["rows"]):
        raise ValueError(f"Temporal {split} split row count changed")
    for row in rows:
        if row.get("split") != split:
            raise ValueError("Temporal row split marker does not match its file")
        if float(row.get("permissions", {}).get("execution_authority_pct", -1)) != 0.0:
            raise ValueError("Temporal row attempted to acquire execution authority")
        if len(row.get("sequence") or ()) != int(manifest["window_seconds"]):
            raise ValueError("Temporal row has an invalid window length")
        if any(len(tick) != len(FEATURE_NAMES) for tick in row["sequence"]):
            raise ValueError("Temporal row has an invalid feature width")
        if row.get("data_domain") == SIMULATION_DOMAIN and split != "train":
            raise ValueError("Simulation temporal rows cannot enter validation or test")
        if datetime.fromisoformat(row["target_at"]) <= datetime.fromisoformat(row["observed_at"]):
            raise ValueError("Temporal target must start after the feature cutoff")
    return rows


def load_temporal_dataset(dataset_directory: Path) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    dataset_directory = Path(dataset_directory).resolve()
    manifest_path = dataset_directory / "temporal_dataset_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if int(manifest.get("temporal_dataset_schema_version") or 0) != TEMPORAL_DATASET_SCHEMA_VERSION:
        raise ValueError("Unsupported temporal dataset schema")
    if tuple(manifest.get("feature_names") or ()) != FEATURE_NAMES:
        raise ValueError("Temporal dataset feature schema does not match v0.7")
    if float(manifest.get("leakage_checks", {}).get("execution_authority_pct", -1)) != 0.0:
        raise ValueError("Temporal dataset attempted to acquire execution authority")
    rows = {split: _load_split(dataset_directory, manifest, split) for split in SPLITS}
    return manifest, rows


def _metrics(network: TemporalConvNetwork, rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"rows": 0, "brier_by_output": {}, "balanced_brier": None}
    totals = [0.0 for _ in OUTPUT_NAMES]
    prevalence = [0.0 for _ in OUTPUT_NAMES]
    for row in rows:
        predicted = list(network.predict(row["sequence"]).values())
        actual = _labels(row)
        for index in range(len(OUTPUT_NAMES)):
            totals[index] += (predicted[index] - actual[index]) ** 2
            prevalence[index] += actual[index]
    brier = [value / len(rows) for value in totals]
    return {
        "rows": len(rows),
        "brier_by_output": {name: brier[index] for index, name in enumerate(OUTPUT_NAMES)},
        "prevalence_by_output": {
            name: prevalence[index] / len(rows) for index, name in enumerate(OUTPUT_NAMES)
        },
        "balanced_brier": sum(
            brier[index] * OUTPUT_LOSS_WEIGHTS[index] for index in range(len(OUTPUT_NAMES))
        ),
    }


def _new_network(
    means: list[float], stds: list[float], *, hidden_units: int, kernel_seconds: int, seed: int
) -> TemporalConvNetwork:
    rng = random.Random(seed)
    scale = 1.0 / math.sqrt(max(1, kernel_seconds * len(FEATURE_NAMES)))
    kernels = [
        [
            [rng.uniform(-scale, scale) for _ in FEATURE_NAMES]
            for _ in range(kernel_seconds)
        ]
        for _ in range(hidden_units)
    ]
    output_scale = 1.0 / math.sqrt(max(1, hidden_units))
    return TemporalConvNetwork(
        means=means,
        stds=stds,
        kernel_seconds=kernel_seconds,
        kernels=kernels,
        hidden_bias=[0.0 for _ in range(hidden_units)],
        output_weights=[
            [rng.uniform(-output_scale, output_scale) for _ in range(hidden_units)]
            for _ in OUTPUT_NAMES
        ],
        output_bias=[0.0 for _ in OUTPUT_NAMES],
    )


def _train_row(
    network: TemporalConvNetwork, row: dict[str, Any], *, learning_rate: float
) -> None:
    standardized, states, pooled, predicted = network._forward_details(row["sequence"])
    actual = _labels(row)
    output_delta = [
        (predicted[index] - actual[index]) * OUTPUT_LOSS_WEIGHTS[index]
        for index in range(len(OUTPUT_NAMES))
    ]
    hidden_gradient = [
        sum(
            output_delta[output] * network.output_weights[output][hidden]
            for output in range(len(OUTPUT_NAMES))
        )
        for hidden in range(len(network.kernels))
    ]

    for output in range(len(OUTPUT_NAMES)):
        for hidden in range(len(network.kernels)):
            network.output_weights[output][hidden] -= (
                learning_rate * output_delta[output] * pooled[hidden]
            )
        network.output_bias[output] -= learning_rate * output_delta[output]

    state_count = len(states)
    for position, state in enumerate(states):
        end = position + network.kernel_seconds - 1
        start = end - network.kernel_seconds + 1
        for hidden in range(len(network.kernels)):
            delta = hidden_gradient[hidden] * (1.0 - state[hidden] ** 2) / state_count
            for lag in range(network.kernel_seconds):
                for feature in range(len(FEATURE_NAMES)):
                    network.kernels[hidden][lag][feature] -= (
                        learning_rate * delta * standardized[start + lag][feature]
                    )
            network.hidden_bias[hidden] -= learning_rate * delta


def train_temporal_shadow(
    dataset_directory: Path,
    model_path: Path,
    *,
    hidden_units: int = 12,
    kernel_seconds: int = 5,
    epochs: int = 8,
    learning_rate: float = 0.02,
    minimum_sequences: int = 40,
    seed: int = 20260904,
    overwrite: bool = False,
) -> TemporalShadowBundle:
    """Train an isolated causal-convolution model; never touch Store or the paper engine."""
    manifest, splits = load_temporal_dataset(dataset_directory)
    train_rows = splits["train"]
    if len(train_rows) < int(minimum_sequences):
        raise ValueError(
            f"Temporal training requires at least {int(minimum_sequences)} sequences; found {len(train_rows)}"
        )
    if hidden_units < 1 or epochs < 1:
        raise ValueError("hidden_units and epochs must be positive")
    if kernel_seconds < 2 or kernel_seconds > int(manifest["window_seconds"]):
        raise ValueError("kernel_seconds must be between two and the sequence window length")
    if not 0 < learning_rate <= 1:
        raise ValueError("learning_rate must be between zero and one")

    groups: dict[str, list[dict[str, Any]]] = {}
    for row in train_rows:
        groups.setdefault(str(row["group_id"]), []).append(row)
    ordered_groups = sorted(groups)
    if len(ordered_groups) < 2:
        raise ValueError("Temporal optimization needs at least two scenario groups")
    holdout_count = max(1, int(len(ordered_groups) * 0.20))
    fit_group_ids = set(ordered_groups[:-holdout_count])
    holdout_group_ids = set(ordered_groups[-holdout_count:])
    fit_rows = [row for row in train_rows if row["group_id"] in fit_group_ids]
    holdout_rows = [row for row in train_rows if row["group_id"] in holdout_group_ids]
    means, stds = _means_stds(fit_rows)
    network = _new_network(
        means, stds, hidden_units=hidden_units, kernel_seconds=kernel_seconds, seed=seed
    )
    rng = random.Random(seed)
    for _ in range(int(epochs)):
        epoch_rows = list(fit_rows)
        rng.shuffle(epoch_rows)
        for row in epoch_rows:
            _train_row(network, row, learning_rate=learning_rate)

    metrics = {
        "fit": _metrics(network, fit_rows),
        "simulation_optimization_holdout": _metrics(network, holdout_rows),
        "market_validation": _metrics(network, splits["validation"]),
        "market_test": _metrics(network, splits["test"]),
        "fit_scenario_groups": len(fit_group_ids),
        "optimization_holdout_scenario_groups": len(holdout_group_ids),
        "group_disjoint": fit_group_ids.isdisjoint(holdout_group_ids),
        "simulation_metrics_are_market_proof": False,
        "market_validation_available": bool(splits["validation"]),
        "eligible_for_real_champion": False,
        "execution_authority_pct": 0,
    }
    manifest_path = Path(dataset_directory).resolve() / "temporal_dataset_manifest.json"
    bundle = TemporalShadowBundle(
        trained_at=_utc_now(),
        dataset_manifest_sha256=_sha256_file(manifest_path),
        dataset_manifest_path=str(manifest_path),
        network=network,
        training_config={
            "architecture": "causal_1d_convolution_with_temporal_mean_pooling",
            "window_seconds": int(manifest["window_seconds"]),
            "kernel_seconds": kernel_seconds,
            "hidden_units": hidden_units,
            "epochs": epochs,
            "learning_rate": learning_rate,
            "seed": seed,
            "preprocessors_fit_on": "fit_partition_only",
            "optimization_split": "scenario_group_disjoint_80_20_within_train",
            "profit_risk_objective_weights": {"profit": 0.5, "risk": 0.5},
            "automatic_runtime_integration": False,
            "automatic_promotion": False,
            "execution_authority_pct": 0,
        },
        metrics=metrics,
    )
    bundle.save(model_path, overwrite=overwrite)
    return bundle


def temporal_status(model_path: Path) -> dict[str, Any]:
    path = Path(model_path).resolve()
    bundle = TemporalShadowBundle.load(path)
    return {
        "installed": True,
        "model_path": str(path),
        "model_sha256": _sha256_file(path),
        "lineage_version": TEMPORAL_LINEAGE_VERSION,
        "trained_at": bundle.trained_at,
        "architecture": bundle.training_config.get("architecture"),
        "window_seconds": bundle.training_config.get("window_seconds"),
        "kernel_seconds": bundle.training_config.get("kernel_seconds"),
        "metrics": bundle.metrics,
        "shadow_only": True,
        "runtime_integration_enabled": False,
        "eligible_for_real_champion": False,
        "execution_authority_pct": 0,
    }


def temporal_report(dataset_directory: Path, model_path: Path | None = None) -> dict[str, Any]:
    manifest, rows = load_temporal_dataset(dataset_directory)
    report = {
        "dataset": {
            "manifest": str(Path(dataset_directory).resolve() / "temporal_dataset_manifest.json"),
            "records_by_split": {split: len(values) for split, values in rows.items()},
            "scenario_groups": manifest["scenario_groups"],
            "window_seconds": manifest["window_seconds"],
            "stride_seconds": manifest["stride_seconds"],
            "horizons_seconds": manifest["horizons_seconds"],
            "duplicate_windows_dropped": manifest["duplicate_windows_dropped"],
            "leakage_checks": manifest["leakage_checks"],
        },
        "model": temporal_status(model_path) if model_path else None,
        "release_gate": {
            "simulation_only_training": True,
            "market_validation_pending": True,
            "can_promote_real_champion": False,
            "paper_or_live_authority_changed": False,
            "execution_authority_pct": 0,
        },
    }
    return report
