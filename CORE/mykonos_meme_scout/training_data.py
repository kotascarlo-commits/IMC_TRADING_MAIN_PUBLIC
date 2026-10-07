from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from .sqlite_source import sha256_file as _sha256_file, verified_source as _verified_source
from .compact_storage import logical_table_names


DATASET_SCHEMA_VERSION = 1
SIMULATION_DOMAIN = "SIMULATION"
HISTORICAL_DOMAIN = "HISTORICAL_REAL"
REAL_PAPER_DOMAIN = "REAL_PAPER"
SPLITS = ("train", "validation", "test")
FEATURE_SCHEMA = (
    "observed_second",
    "price_usd",
    "liquidity_usd",
    "market_cap_usd",
    "liquidity_to_market_cap",
    "return_5s_pct",
    "return_15s_pct",
    "return_60s_pct",
    "liquidity_change_60s_pct",
    "volume_60s_usd",
    "transactions_60s",
    "buy_ratio_60s",
    "social_mentions_60s",
    "social_sentiment_60s",
    "smart_wallet_net_flow_60s_usd",
    "social_flow_divergence",
    "social_data_available",
    "sequence_native",
    "rug_seen",
)
LABEL_SCHEMA = (
    "hit_5",
    "hit_10",
    "hit_25",
    "rug",
    "stagnant",
    "max_return_pct",
    "min_return_pct",
    "return_pct",
    "profit_potential_pct",
    "risk_exposure_pct",
    "balanced_utility_pct",
    "liquidity_drawdown_pct",
)


@dataclass(frozen=True, slots=True)
class DatasetPolicy:
    train_fraction: float = .70
    validation_fraction: float = .15
    purge_seconds: int = 900
    max_rows_per_source: int = 50_000

    def __post_init__(self) -> None:
        if not 0 < self.train_fraction < 1:
            raise ValueError("train_fraction must be between zero and one")
        if not 0 <= self.validation_fraction < 1:
            raise ValueError("validation_fraction must be between zero and one")
        if self.train_fraction + self.validation_fraction >= 1:
            raise ValueError("train_fraction + validation_fraction must be below one")
        if self.purge_seconds < 0:
            raise ValueError("purge_seconds cannot be negative")
        if self.max_rows_per_source < 1:
            raise ValueError("max_rows_per_source must be positive")


@dataclass(frozen=True, slots=True)
class TrainingRecord:
    record_id: str
    group_id: str
    data_domain: str
    source_fingerprint: str
    observed_at: str
    target_at: str
    horizon_seconds: int
    features: dict[str, Any]
    labels: dict[str, Any]
    provenance: dict[str, Any]

    def payload(self, split: str) -> dict[str, Any]:
        if split not in SPLITS:
            raise ValueError(f"Unknown dataset split: {split}")
        is_simulation = self.data_domain == SIMULATION_DOMAIN
        return {
            "dataset_schema_version": DATASET_SCHEMA_VERSION,
            "record_id": self.record_id,
            "group_id": self.group_id,
            "split": split,
            "data_domain": self.data_domain,
            "source_fingerprint": self.source_fingerprint,
            "observed_at": self.observed_at,
            "target_at": self.target_at,
            "horizon_seconds": self.horizon_seconds,
            "features": {name: self.features.get(name) for name in FEATURE_SCHEMA},
            "labels": {name: self.labels.get(name) for name in LABEL_SCHEMA},
            "provenance": self.provenance,
            "permissions": {
                "neural_population_training": split == "train",
                "validation_evidence": not is_simulation and split in {"validation", "test"},
                "champion_promotion_evidence": (
                    self.data_domain == REAL_PAPER_DOMAIN and split in {"validation", "test"}
                ),
                "execution_authority_pct": 0,
            },
        }


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return logical_table_names(connection)


def _pct_change(current: float, previous: float) -> float:
    return (current / previous - 1.0) * 100.0 if previous > 0 else 0.0


def _complete_features(raw: dict[str, Any]) -> dict[str, Any]:
    numeric_defaults = {name: 0.0 for name in FEATURE_SCHEMA}
    numeric_defaults.update({"social_data_available": False, "sequence_native": False, "rug_seen": False})
    numeric_defaults.update(raw)
    return {name: numeric_defaults[name] for name in FEATURE_SCHEMA}


def _complete_labels(raw: dict[str, Any]) -> dict[str, Any]:
    result = {name: raw.get(name) for name in LABEL_SCHEMA}
    result.update({
        "hit_5": bool(raw.get("hit_5")),
        "hit_10": bool(raw.get("hit_10")),
        "hit_25": bool(raw.get("hit_25")),
        "rug": bool(raw.get("rug")),
        "stagnant": bool(raw.get("stagnant")),
    })
    return result


def _load_simulation_records(path: Path, maximum_rows: int) -> tuple[list[TrainingRecord], dict[str, Any]]:
    with _verified_source(path) as (connection, integrity):
        source_hash = integrity["source_fingerprint"]
        tables = _table_names(connection)
        required = {"simulation_metadata", "simulation_training_examples", "simulation_scenarios", "simulation_ticks"}
        if not required.issubset(tables):
            raise ValueError(f"Not a marked simulation database: {path}")
        metadata = connection.execute("SELECT * FROM simulation_metadata WHERE id=1").fetchone()
        if metadata is None or str(metadata["data_domain"]) != SIMULATION_DOMAIN:
            raise ValueError(f"Simulation domain marker missing: {path}")
        if int(metadata["eligible_for_real_training"]) or int(metadata["eligible_for_real_champion"]):
            raise ValueError("Simulation source illegally declares real-training or champion eligibility")
        rows = connection.execute(
            """SELECT e.*,s.origin,s.family,s.sequence_checksum,s.run_id,s.metadata_json,
            observed.captured_at AS observed_at,target.captured_at AS target_at
            FROM simulation_training_examples e
            JOIN simulation_scenarios s ON s.scenario_id=e.scenario_id
            JOIN simulation_ticks observed ON observed.scenario_id=e.scenario_id
              AND observed.second_offset=e.observed_second
            JOIN simulation_ticks target ON target.scenario_id=e.scenario_id
              AND target.second_offset=e.observed_second+e.horizon_seconds
            ORDER BY observed.captured_at,e.example_id LIMIT ?""",
            (int(maximum_rows),),
        ).fetchall()
        records = []
        for row in rows:
            features = _complete_features(json.loads(str(row["features_json"])))
            labels = _complete_labels(json.loads(str(row["labels_json"])))
            provenance = json.loads(str(row["provenance_json"]))
            scenario_metadata = json.loads(str(row["metadata_json"] or "{}"))
            checksum = str(row["sequence_checksum"])
            # v0.6.0 did not copy this identity into example provenance. Fall back
            # to scenario metadata so already-created v0.6 DBs remain leakage-safe.
            source_token_hash = str(
                provenance.get("source_address_sha256")
                or scenario_metadata.get("source_address_sha256")
                or ""
            )
            group_id = (
                "token-" + source_token_hash
                if str(row["origin"]) == "HISTORICAL_RESAMPLE" and source_token_hash
                else "scenario-" + checksum
            )
            key = f"simulation|{checksum}|{row['observed_second']}|{row['horizon_seconds']}"
            records.append(TrainingRecord(
                record_id="record-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:32],
                group_id=group_id,
                data_domain=SIMULATION_DOMAIN,
                source_fingerprint=source_hash,
                observed_at=_parse_time(str(row["observed_at"])).isoformat(),
                target_at=_parse_time(str(row["target_at"])).isoformat(),
                horizon_seconds=int(row["horizon_seconds"]),
                features=features,
                labels=labels,
                provenance={
                    **provenance,
                    "source_role": "simulation",
                    "source_origin": str(row["origin"]),
                    "scenario_family": str(row["family"]),
                    "simulation_run_id": str(row["run_id"]),
                    "source_address_sha256": source_token_hash or None,
                    "source_row_eligible_for_real_training": False,
                    "source_row_eligible_for_real_champion": False,
                },
            ))
    return records, {
        "role": "simulation",
        "path": str(path),
        **integrity,
        "rows_read": len(records),
        "opened_read_only": True,
        "data_domain": SIMULATION_DOMAIN,
    }


def _snapshot_features(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    observed_at = _parse_time(str(row["predicted_at"]))
    snapshots = connection.execute(
        """SELECT captured_at,price_usd,payload_json FROM snapshots
        WHERE address=? AND captured_at<=? ORDER BY captured_at DESC,id DESC LIMIT 240""",
        (str(row["address"]), str(row["predicted_at"])),
    ).fetchall()
    history: list[tuple[datetime, float, dict[str, Any]]] = []
    for snapshot in snapshots:
        try:
            history.append((
                _parse_time(str(snapshot["captured_at"])),
                float(snapshot["price_usd"]),
                json.loads(str(snapshot["payload_json"])),
            ))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    history.sort(key=lambda item: item[0])
    current_payload = history[-1][2] if history else {}
    current_price = float(row["entry_price"])
    current_liquidity = float(row["entry_liquidity"])
    current_market_cap = float(current_payload.get("market_cap_usd") or 0.0)

    def before(seconds: int) -> tuple[datetime, float, dict[str, Any]] | None:
        cutoff = observed_at - timedelta(seconds=seconds)
        eligible = [item for item in history if item[0] <= cutoff]
        return eligible[-1] if eligible else (history[0] if history else None)

    prior_5 = before(5)
    prior_15 = before(15)
    prior_60 = before(60)
    previous_liquidity = float((prior_60[2] if prior_60 else {}).get("liquidity_usd") or current_liquidity)
    created_ms = int(current_payload.get("pair_created_at_ms") or 0)
    observed_second = max(0, int(observed_at.timestamp() - created_ms / 1000)) if created_ms else 0
    age_seconds = max(20.0, min(300.0, float(observed_second or 300)))
    volume_5m = max(0.0, float(current_payload.get("volume_5m_usd") or 0.0))
    buys_5m = max(0, int(current_payload.get("buys_5m") or 0))
    sells_5m = max(0, int(current_payload.get("sells_5m") or 0))
    scale = min(1.0, 60.0 / age_seconds)
    transactions_60s = (buys_5m + sells_5m) * scale
    mentions_5m = max(0.0, float(current_payload.get("social_mentions_5m") or 0.0))
    social_sentiment = max(-1.0, min(1.0, float(current_payload.get("social_sentiment") or 0.0)))
    smart_flow_5m = float(current_payload.get("smart_wallet_net_flow_5m_usd") or 0.0)
    social_available = any(
        key in current_payload for key in (
            "social_mentions_5m", "social_sentiment", "smart_wallet_net_flow_5m_usd"
        )
    )
    mentions_60s = mentions_5m * scale
    smart_flow_60s = smart_flow_5m * scale
    market_flow = (buys_5m - sells_5m) / max(1, buys_5m + sells_5m)
    smart_flow_ratio = smart_flow_60s / max(1.0, volume_5m * scale)
    divergence = max(0.0, social_sentiment) * math.log1p(mentions_60s) - market_flow - smart_flow_ratio
    return _complete_features({
        "observed_second": observed_second,
        "price_usd": current_price,
        "liquidity_usd": current_liquidity,
        "market_cap_usd": current_market_cap,
        "liquidity_to_market_cap": current_liquidity / max(current_market_cap, 1e-12),
        "return_5s_pct": _pct_change(current_price, prior_5[1]) if prior_5 else 0.0,
        "return_15s_pct": _pct_change(current_price, prior_15[1]) if prior_15 else 0.0,
        "return_60s_pct": _pct_change(current_price, prior_60[1]) if prior_60 else 0.0,
        "liquidity_change_60s_pct": _pct_change(current_liquidity, previous_liquidity),
        "volume_60s_usd": volume_5m * scale,
        "transactions_60s": transactions_60s,
        "buy_ratio_60s": buys_5m / max(1, buys_5m + sells_5m),
        "social_mentions_60s": mentions_60s,
        "social_sentiment_60s": social_sentiment,
        "smart_wallet_net_flow_60s_usd": smart_flow_60s,
        "social_flow_divergence": divergence,
        "social_data_available": social_available,
        "sequence_native": False,
        "rug_seen": False,
    })


def _load_real_records(
    path: Path,
    maximum_rows: int,
    *,
    domain: str,
) -> tuple[list[TrainingRecord], dict[str, Any]]:
    with _verified_source(path) as (connection, integrity):
        source_hash = integrity["source_fingerprint"]
        tables = _table_names(connection)
        if "simulation_metadata" in tables:
            raise ValueError(f"Simulation DB cannot be assigned the {domain} role: {path}")
        required = {"snapshots", "learning_predictions", "learning_outcomes"}
        if not required.issubset(tables):
            raise ValueError(f"Real-data source lacks settled learning tables: {path}")
        rows = connection.execute(
            """SELECT p.*,o.measured_at,o.hit_5,o.hit_10,o.hit_25,o.rug,o.stagnant,
            o.max_return_pct,o.min_return_pct,o.return_pct
            FROM learning_predictions p JOIN learning_outcomes o ON o.prediction_id=p.id
            WHERE p.model_family='balanced'
            ORDER BY p.predicted_at,p.id LIMIT ?""",
            (int(maximum_rows),),
        ).fetchall()
        records = []
        for row in rows:
            observed_at = _parse_time(str(row["predicted_at"]))
            target_at = _parse_time(str(row["measured_at"]))
            horizon_seconds = int(row["horizon_minutes"]) * 60
            if target_at <= observed_at:
                continue
            token_hash = hashlib.sha256(str(row["address"]).encode("utf-8")).hexdigest()
            max_return = float(row["max_return_pct"])
            min_return = float(row["min_return_pct"])
            rug = bool(row["rug"])
            profit = max(0.0, max_return)
            risk = max(0.0, -min_return, 100.0 if rug else 0.0)
            labels = _complete_labels({
                "hit_5": row["hit_5"],
                "hit_10": row["hit_10"],
                "hit_25": row["hit_25"],
                "rug": rug,
                "stagnant": row["stagnant"],
                "max_return_pct": max_return,
                "min_return_pct": min_return,
                "return_pct": float(row["return_pct"]),
                "profit_potential_pct": profit,
                "risk_exposure_pct": risk,
                "balanced_utility_pct": .5 * profit - .5 * risk,
                "liquidity_drawdown_pct": None,
            })
            dedupe_key = (
                f"real|{token_hash}|{observed_at.isoformat()}|{target_at.isoformat()}|"
                f"{horizon_seconds}|{json.dumps(labels, sort_keys=True)}"
            )
            records.append(TrainingRecord(
                record_id="record-" + hashlib.sha256(dedupe_key.encode("utf-8")).hexdigest()[:32],
                group_id="token-" + token_hash,
                data_domain=domain,
                source_fingerprint=source_hash,
                observed_at=observed_at.isoformat(),
                target_at=target_at.isoformat(),
                horizon_seconds=horizon_seconds,
                features=_snapshot_features(connection, row),
                labels=labels,
                provenance={
                    "source_role": "historical" if domain == HISTORICAL_DOMAIN else "real_paper",
                    "source_model_family": str(row["model_family"]),
                    "source_model_version": str(row["model_version"]),
                    "source_prediction_id": int(row["id"]),
                    "source_address_sha256": token_hash,
                    "feature_cutoff_at": observed_at.isoformat(),
                    "label_measured_at": target_at.isoformat(),
                    "profit_risk_weights": {"profit": .5, "risk": .5},
                },
            ))
    return records, {
        "role": "historical" if domain == HISTORICAL_DOMAIN else "real_paper",
        "path": str(path),
        **integrity,
        "rows_read": len(records),
        "opened_read_only": True,
        "data_domain": domain,
    }


def _deduplicate(records: Iterable[TrainingRecord]) -> tuple[list[TrainingRecord], int]:
    unique: dict[str, TrainingRecord] = {}
    duplicates = 0
    for record in records:
        key = record.record_id
        if key in unique:
            duplicates += 1
            continue
        unique[key] = record
    return sorted(unique.values(), key=lambda row: (_parse_time(row.observed_at), row.record_id)), duplicates


def _split_records(
    records: Sequence[TrainingRecord], policy: DatasetPolicy
) -> tuple[dict[str, list[TrainingRecord]], int, dict[str, str | None]]:
    splits = {name: [] for name in SPLITS}
    simulation_groups = {
        record.group_id for record in records if record.data_domain == SIMULATION_DOMAIN
    }
    for record in records:
        if record.group_id in simulation_groups:
            splits["train"].append(record)

    real_records = [
        record for record in records
        if record.data_domain != SIMULATION_DOMAIN and record.group_id not in simulation_groups
    ]
    grouped: dict[str, list[TrainingRecord]] = {}
    for record in real_records:
        grouped.setdefault(record.group_id, []).append(record)
    ordered_groups = sorted(
        grouped.items(), key=lambda item: min(_parse_time(row.observed_at) for row in item[1])
    )
    if not ordered_groups:
        return splits, 0, {"train_validation": None, "validation_test": None}

    group_count = len(ordered_groups)
    train_count = max(1, int(group_count * policy.train_fraction))
    validation_count = int(group_count * policy.validation_fraction)
    if policy.validation_fraction > 0 and group_count >= 3:
        validation_count = max(1, validation_count)
    if train_count + validation_count >= group_count and group_count > 1:
        validation_count = max(0, group_count - train_count - 1)

    base_assignment: dict[str, str] = {}
    for index, (group_id, _) in enumerate(ordered_groups):
        if index < train_count:
            base_assignment[group_id] = "train"
        elif index < train_count + validation_count:
            base_assignment[group_id] = "validation"
        else:
            base_assignment[group_id] = "test"

    purge = timedelta(seconds=policy.purge_seconds)
    train_rows = [row for group, values in ordered_groups if base_assignment[group] == "train" for row in values]
    train_boundary = max((_parse_time(row.target_at) for row in train_rows), default=None)
    validation_rows = [
        row for group, values in ordered_groups if base_assignment[group] == "validation" for row in values
    ]
    valid_validation_groups = {
        group for group, values in ordered_groups
        if base_assignment[group] == "validation"
        and (train_boundary is None or min(_parse_time(row.observed_at) for row in values) > train_boundary + purge)
    }
    accepted_validation = [row for row in validation_rows if row.group_id in valid_validation_groups]
    validation_boundary = max((_parse_time(row.target_at) for row in accepted_validation), default=train_boundary)

    purged = 0
    for group_id, values in ordered_groups:
        split = base_assignment[group_id]
        if split == "validation" and group_id not in valid_validation_groups:
            purged += len(values)
            continue
        if split == "test" and validation_boundary is not None:
            first_observation = min(_parse_time(row.observed_at) for row in values)
            if first_observation <= validation_boundary + purge:
                purged += len(values)
                continue
        splits[split].extend(values)

    boundaries = {
        "train_validation": train_boundary.isoformat() if train_boundary else None,
        "validation_test": validation_boundary.isoformat() if validation_boundary else None,
    }
    return splits, purged, boundaries


def _write_jsonl(path: Path, records: Sequence[TrainingRecord], split: str) -> dict[str, Any]:
    temporary = path.with_suffix(path.suffix + ".tmp")
    digest = hashlib.sha256()
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            line = json.dumps(record.payload(split), sort_keys=True, separators=(",", ":")) + "\n"
            handle.write(line)
            digest.update(line.encode("utf-8"))
    temporary.replace(path)
    return {"path": str(path), "rows": len(records), "sha256": digest.hexdigest()}


def build_training_dataset(
    output_directory: Path,
    *,
    simulation_databases: Sequence[Path] = (),
    historical_databases: Sequence[Path] = (),
    real_databases: Sequence[Path] = (),
    policy: DatasetPolicy | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Build a domain-aware, group-disjoint and purged Neural Population dataset."""
    policy = policy or DatasetPolicy()
    sources = [
        *(('simulation', Path(path).resolve()) for path in simulation_databases),
        *(('historical', Path(path).resolve()) for path in historical_databases),
        *(('real_paper', Path(path).resolve()) for path in real_databases),
    ]
    if not sources:
        raise ValueError("At least one simulation, historical or real database is required")
    paths = [path for _, path in sources]
    if len(set(paths)) != len(paths):
        raise ValueError("A source database may have exactly one data-domain role")
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(f"Training source database not found: {path}")

    output_directory = Path(output_directory).resolve()
    if output_directory in paths:
        raise ValueError("Dataset output must not be a source database")
    output_directory.mkdir(parents=True, exist_ok=True)
    outputs = {split: output_directory / f"{split}.jsonl" for split in SPLITS}
    manifest_path = output_directory / "dataset_manifest.json"
    existing = [path for path in (*outputs.values(), manifest_path) if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(
            "Dataset output already exists; use a new directory or explicitly enable overwrite"
        )

    records: list[TrainingRecord] = []
    source_manifest: list[dict[str, Any]] = []
    for role, path in sources:
        if role == "simulation":
            loaded, source = _load_simulation_records(path, policy.max_rows_per_source)
        else:
            domain = HISTORICAL_DOMAIN if role == "historical" else REAL_PAPER_DOMAIN
            loaded, source = _load_real_records(
                path, policy.max_rows_per_source, domain=domain
            )
        records.extend(loaded)
        source_manifest.append(source)
    records, duplicates = _deduplicate(records)
    if not records:
        raise ValueError("No eligible causal training records were found in the supplied sources")
    splits, purged, boundaries = _split_records(records, policy)

    split_groups = {name: {row.group_id for row in values} for name, values in splits.items()}
    groups_disjoint = not (
        split_groups["train"] & split_groups["validation"]
        or split_groups["train"] & split_groups["test"]
        or split_groups["validation"] & split_groups["test"]
    )
    simulation_only_train = all(
        row.data_domain != SIMULATION_DOMAIN
        for split in ("validation", "test") for row in splits[split]
    )
    causal_windows = all(
        _parse_time(row.target_at) > _parse_time(row.observed_at) for row in records
    )
    if not (groups_disjoint and simulation_only_train and causal_windows):
        raise RuntimeError("Dataset leakage invariant failed")

    split_files = {
        split: _write_jsonl(outputs[split], splits[split], split) for split in SPLITS
    }
    counts_by_domain = {
        domain: {
            split: sum(row.data_domain == domain for row in splits[split]) for split in SPLITS
        }
        for domain in (SIMULATION_DOMAIN, HISTORICAL_DOMAIN, REAL_PAPER_DOMAIN)
    }
    manifest = {
        "dataset_schema_version": DATASET_SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "feature_schema": list(FEATURE_SCHEMA),
        "label_schema": list(LABEL_SCHEMA),
        "profit_risk_weights": {"profit": .5, "risk": .5},
        "policy": asdict(policy),
        "sources": source_manifest,
        "split_files": split_files,
        "counts_by_domain": counts_by_domain,
        "records_before_purge": len(records),
        "duplicate_records_dropped": duplicates,
        "purged_boundary_records": purged,
        "boundaries": boundaries,
        "leakage_checks": {
            "source_databases_opened_read_only": True,
            "features_end_at_observation": True,
            "targets_start_after_observation": causal_windows,
            "token_and_scenario_groups_disjoint": groups_disjoint,
            "simulation_confined_to_train_split": simulation_only_train,
            "validation_and_test_contain_real_evidence_only": simulation_only_train,
            "preprocessors_must_fit_on_train_split_only": True,
            "simulation_can_promote_champion": False,
            "execution_authority_pct": 0,
        },
        "consumer_contract": {
            "train_inputs": [SIMULATION_DOMAIN, HISTORICAL_DOMAIN, REAL_PAPER_DOMAIN],
            "validation_inputs": [HISTORICAL_DOMAIN, REAL_PAPER_DOMAIN],
            "champion_evidence": [REAL_PAPER_DOMAIN],
            "automatic_model_training_enabled": False,
            "automatic_model_promotion_enabled": False,
            "execution_authority_pct": 0,
        },
    }
    temporary_manifest = manifest_path.with_suffix(".json.tmp")
    temporary_manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary_manifest.replace(manifest_path)
    return {**manifest, "manifest": str(manifest_path)}
