"""WAL-aware training provenance, with no production database access."""
import json
import sqlite3
from pathlib import Path

import pytest

from mykonos_meme_scout import training_data as training
from mykonos_meme_scout import sqlite_source
from mykonos_meme_scout.store import SCHEMA


@pytest.fixture
def source(tmp_path):
    path = tmp_path / 'source.db'
    writer = sqlite3.connect(path)
    writer.executescript(SCHEMA)
    writer.execute('''INSERT INTO snapshots(captured_at,address,symbol,price_usd,payload_json)
        VALUES(?,?,?,?,?)''', ('2026-01-01T12:00:00+00:00', 'mint', 'T', 1,
            json.dumps({'market_cap_usd': 1000, 'liquidity_usd': 100})))
    writer.execute('''INSERT INTO learning_predictions(model_family,model_version,address,symbol,
        predicted_at,horizon_minutes,entry_price,entry_liquidity,p5,p10,p25,p_rug,p_stagnation,
        uncertainty,expected_utility,reasons_json)
        VALUES('balanced','m1','mint','T','2026-01-01T12:00:00+00:00',1,1,100,.5,.4,.3,.1,.2,.3,2,'[]')''')
    writer.execute('''INSERT INTO learning_outcomes VALUES(
        1,'2026-01-01T12:01:00+00:00',1,0,0,0,0,6,-1,5)''')
    writer.commit()
    yield path, writer
    writer.close()


def test_export_reads_wal_and_fingerprints_both_files(source):
    path, writer = source
    files = [path, Path(str(path) + '-wal')]
    before = [p.read_bytes() for p in files]
    records, manifest = training._load_real_records(path, 10, domain=training.HISTORICAL_DOMAIN)
    assert len(records) == 1
    assert records[0].features['market_cap_usd'] == 1000
    assert records[0].source_fingerprint == manifest['source_fingerprint']
    assert manifest['source_fingerprint_version'] == 2
    assert manifest['sha256'] == training._sha256_file(path)
    assert manifest['wal_sha256'] == training._sha256_file(files[1])
    assert manifest['wal_included'] and manifest['source_main_wal_verified_unchanged']
    assert before == [p.read_bytes() for p in files]


def test_wal_only_simulation_marker_cannot_be_real_evidence(source):
    path, writer = source
    writer.execute('CREATE TABLE simulation_metadata(id INTEGER)')
    writer.commit()
    with pytest.raises(ValueError, match='Simulation DB cannot be assigned'):
        training._load_real_records(path, 10, domain=training.REAL_PAPER_DOMAIN)


def test_writer_during_feature_extraction_refuses_export(source, monkeypatch, tmp_path):
    path, writer = source
    original = training._snapshot_features
    def changed(connection, row):
        result = original(connection, row)
        writer.execute("INSERT INTO snapshots(captured_at,address,symbol,price_usd,payload_json) VALUES('later','mint','T',2,'{}')")
        writer.commit()
        return result
    monkeypatch.setattr(training, '_snapshot_features', changed)
    output = tmp_path / 'output'
    with pytest.raises(RuntimeError, match='source changed'):
        training.build_training_dataset(output, historical_databases=[path])
    assert not list(output.glob('*.jsonl'))
    assert not (output / 'dataset_manifest.json').exists()


def test_changed_file_identity_is_rejected(source, monkeypatch):
    path, _ = source
    original = sqlite_source.source_file_state
    with pytest.raises(RuntimeError, match='source changed'):
        with training._verified_source(path):
            def swapped(p):
                state = original(p)
                fields = list(state['main'])
                fields[1] += 1
                state['main'] = tuple(fields)
                return state
            monkeypatch.setattr(sqlite_source, 'source_file_state', swapped)


def test_wal_creation_is_rejected_when_originally_absent(tmp_path):
    path = tmp_path / 'source.db'
    with sqlite3.connect(path) as writer:
        writer.execute('CREATE TABLE marker(id INTEGER)')
    with pytest.raises(RuntimeError, match='source changed'):
        with training._verified_source(path):
            Path(str(path) + '-wal').write_bytes(b'new sidecar')


def test_hash_change_is_rejected_even_if_metadata_matches(source, monkeypatch):
    path, _ = source
    original = sqlite_source.sha256_file
    with pytest.raises(RuntimeError, match='source contents changed'):
        with training._verified_source(path):
            monkeypatch.setattr(sqlite_source, 'sha256_file', lambda _: 'changed')
    monkeypatch.setattr(sqlite_source, 'sha256_file', original)


def test_verified_connection_is_query_only_and_does_not_create_missing_db(source, tmp_path):
    path, _ = source
    with training._verified_source(path) as (connection, _):
        assert connection.in_transaction
        with pytest.raises(sqlite3.OperationalError, match='readonly'):
            connection.execute('CREATE TABLE accidental(id INTEGER)')
    missing = tmp_path / 'missing.db'
    with pytest.raises(FileNotFoundError):
        with training._verified_source(missing):
            pytest.fail('Missing source accepted')
    assert not missing.exists()
