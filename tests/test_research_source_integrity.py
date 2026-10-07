"""Historical replay and temporal research use the same verified WAL snapshot."""
import json
import sqlite3
from pathlib import Path

import pytest

from mykonos_meme_scout import simulation, sqlite_source, temporal_intelligence as temporal
from mykonos_meme_scout import training_data as training


@pytest.fixture
def historical_source(tmp_path):
    path = tmp_path / "historical source.db"
    writer = sqlite3.connect(path)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("""CREATE TABLE snapshots(id INTEGER PRIMARY KEY,
        captured_at TEXT,address TEXT,symbol TEXT,price_usd REAL,payload_json TEXT)""")
    writer.executemany("INSERT INTO snapshots VALUES(?,?,?,?,?,?)", [
        (i + 1, f"2026-01-01T12:00:0{i * 2}+00:00", "mint", "T", 1 + i / 10,
         json.dumps({"liquidity_usd": 1000, "market_cap_usd": 10000}))
        for i in range(3)
    ])
    writer.commit()
    yield path, writer
    writer.close()


@pytest.fixture
def simulation_source(tmp_path):
    path = tmp_path / "simulation source.db"
    store = simulation.SimulationStore(path)
    scenario = simulation.generate_synthetic_path("rocket", duration_seconds=180, seed=123)
    store.persist_run([scenario], seed=123, config={}, example_stride_seconds=30)
    yield path, store
    store.close()


def source_bytes(path):
    return [file.read_bytes() for file in (path, Path(str(path) + "-wal"))]


def test_historical_wal_provenance_reaches_resampled_scenario(historical_source):
    path, _ = historical_source
    before = source_bytes(path)
    sequences = simulation.load_historical_sequences(path)
    assert len(sequences) == 1
    sequence = sequences[0]
    assert len(sequence.observations) == 3
    proof = sequence.source_integrity
    assert proof["source_main_wal_verified_unchanged"]
    assert proof["source_fingerprint_version"] == 2
    assert proof["sha256"] == sequence.source_database_sha256 == sqlite_source.sha256_file(path)
    assert proof["wal_sha256"] == sqlite_source.sha256_file(Path(str(path) + "-wal"))
    scenario = simulation.resample_historical_path(sequence, duration_seconds=180, seed=42)
    assert scenario.source_fingerprint == proof["source_fingerprint"]
    assert scenario.metadata["source_database_sha256"] == proof["sha256"]
    assert scenario.metadata["source_integrity"] == proof
    assert source_bytes(path) == before


def test_historical_rejects_domain_marker_committed_only_in_wal(historical_source):
    path, writer = historical_source
    writer.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
    main_before = path.read_bytes()
    writer.execute("CREATE TABLE simulation_metadata(id INTEGER)")
    writer.commit()
    assert path.read_bytes() == main_before
    with pytest.raises(ValueError, match="simulation database cannot"):
        simulation.load_historical_sequences(path)


def test_historical_mutation_refuses_run_before_output(historical_source, monkeypatch, tmp_path):
    path, writer = historical_source
    original = simulation.HistoricalObservation
    mutated = False

    def changing_observation(*args, **kwargs):
        nonlocal mutated
        result = original(*args, **kwargs)
        if not mutated:
            mutated = True
            writer.execute("INSERT INTO snapshots VALUES(4,'later','mint','T',2,'{}')")
            writer.commit()
        return result

    monkeypatch.setattr(simulation, "HistoricalObservation", changing_observation)
    output = tmp_path / "replay.db"
    with pytest.raises(RuntimeError, match="source changed"):
        simulation.run_simulation(output, synthetic_per_family=0, families=(),
                                  historical_database=path, historical_paths=1)
    assert not output.exists()


def test_run_retains_extraction_proof_if_source_changes_after_read(historical_source, monkeypatch, tmp_path):
    path, writer = historical_source
    expected = simulation.load_historical_sequences(path)[0].source_integrity.copy()
    original = simulation.resample_historical_path

    def changing_resample(sequence, **kwargs):
        writer.execute("INSERT INTO snapshots VALUES(4,'later','mint','T',2,'{}')")
        writer.commit()
        return original(sequence, **kwargs)

    monkeypatch.setattr(simulation, "resample_historical_path", changing_resample)
    output = tmp_path / "replay.db"
    simulation.run_simulation(output, synthetic_per_family=0, families=(), duration_seconds=180,
                              historical_database=path, historical_paths=1)
    with sqlite3.connect(output) as reader:
        config = json.loads(reader.execute("SELECT config_json FROM simulation_runs").fetchone()[0])
        fingerprint, metadata = reader.execute(
            "SELECT source_fingerprint,metadata_json FROM simulation_scenarios"
        ).fetchone()
    assert expected["wal_sha256"] != sqlite_source.sha256_file(Path(str(path) + "-wal"))
    assert config["historical_source_sha256"] == expected["sha256"]
    assert config["historical_source_integrity"] == expected
    assert json.loads(metadata)["source_integrity"] == expected
    assert fingerprint == expected["source_fingerprint"]


def test_synthetic_run_does_not_claim_unused_historical_provenance(historical_source, tmp_path):
    path, _ = historical_source
    output = tmp_path / "synthetic.db"
    simulation.run_simulation(output, families=("rocket",), duration_seconds=120,
                              historical_database=path, historical_paths=0)
    with sqlite3.connect(output) as reader:
        config = json.loads(reader.execute("SELECT config_json FROM simulation_runs").fetchone()[0])
    assert config["historical_source_sha256"] is None
    assert config["historical_source_integrity"] is None


def test_temporal_and_training_read_wal_only_simulation(simulation_source, tmp_path):
    path, store = simulation_source
    # No schema was checkpointed: an immutable reader sees no tables.
    with sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True) as stale:
        assert not stale.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    before = source_bytes(path)
    records, proof = training._load_simulation_records(path, 100)
    assert records and proof["source_main_wal_verified_unchanged"]
    assert all(row.source_fingerprint == proof["source_fingerprint"] for row in records)
    manifest = temporal.build_temporal_dataset(
        tmp_path / "temporal", simulation_databases=[path], window_seconds=3,
        stride_seconds=1, horizons_seconds=(60,),
    )
    temporal_proof = manifest["sources"][0]
    assert temporal_proof["sha256"] == proof["sha256"]
    assert temporal_proof["wal_sha256"] == proof["wal_sha256"]
    assert temporal_proof["source_fingerprint"] == proof["source_fingerprint"]
    assert temporal_proof["source_fingerprint_version"] == 2
    assert temporal_proof["source_main_wal_verified_unchanged"]
    rows = [json.loads(line) for line in (tmp_path / "temporal/train.jsonl").read_text().splitlines()]
    assert rows
    assert all(row["source_fingerprint"] == temporal_proof["source_fingerprint"] for row in rows)
    assert manifest["leakage_checks"]["source_main_wal_verified_unchanged"]
    assert source_bytes(path) == before


def test_temporal_mutation_refuses_dataset_before_publication(simulation_source, monkeypatch, tmp_path):
    path, store = simulation_source
    original = temporal._tick_features
    mutated = False

    def changing_features(window):
        nonlocal mutated
        result = original(window)
        if not mutated:
            mutated = True
            store.conn.execute("CREATE TABLE changed_source(id INTEGER)")
            store.conn.commit()
        return result

    monkeypatch.setattr(temporal, "_tick_features", changing_features)
    output = tmp_path / "refused"
    with pytest.raises(RuntimeError, match="source changed"):
        temporal.build_temporal_dataset(output, simulation_databases=[path], window_seconds=3,
                                        stride_seconds=1, horizons_seconds=(60,))
    assert not list(output.glob("*.jsonl"))
    assert not (output / "temporal_dataset_manifest.json").exists()


def test_verification_flag_only_becomes_true_after_successful_exit(historical_source):
    path, writer = historical_source
    with sqlite_source.verified_source(path) as (_, proof):
        assert not proof["source_main_wal_verified_unchanged"]
    assert proof["source_main_wal_verified_unchanged"]
    with pytest.raises(RuntimeError, match="source changed"):
        with sqlite_source.verified_source(path) as (_, refused):
            writer.execute("INSERT INTO snapshots VALUES(4,'later','mint','T',2,'{}')")
            writer.commit()
    assert not refused["source_main_wal_verified_unchanged"]


def test_readonly_snapshot_is_fixed_until_connection_close(historical_source):
    path, writer = historical_source
    connection = sqlite_source.readonly_connection(path)
    try:
        assert connection.in_transaction
        writer.execute("INSERT INTO snapshots VALUES(4,'later','mint','T',2,'{}')")
        writer.commit()
        assert connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 3
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("CREATE TABLE forbidden(id INTEGER)")
    finally:
        connection.close()
    with sqlite_source.verified_source(path) as (reader, _):
        assert reader.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 4


def test_simulation_readonly_store_sees_domain_marker_in_wal(simulation_source):
    path, _ = simulation_source
    store = simulation.SimulationStore(path, read_only=True)
    try:
        assert store.conn.execute("SELECT COUNT(*) FROM simulation_scenarios").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            store.conn.execute("CREATE TABLE forbidden(id INTEGER)")
    finally:
        store.close()


@pytest.mark.parametrize("consumer", (simulation.load_historical_sequences,
    lambda path: temporal.build_temporal_dataset(path.parent / "out", simulation_databases=[path])))
def test_missing_sources_are_never_created(tmp_path, consumer):
    path = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        consumer(path)
    assert not path.exists()
