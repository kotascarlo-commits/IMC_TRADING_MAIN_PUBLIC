"""Compare genuine Store callers across two isolated representations."""
import json
import sqlite3
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace

import pytest

from mykonos_meme_scout import compact_storage as cs
from mykonos_meme_scout.models import Assessment
from mykonos_meme_scout.store import Store,SCHEMA
from mykonos_meme_scout.training_data import _table_names
from test_scoring import _base_snapshot


def create_compact(path):
    with sqlite3.connect(':memory:') as reference:
        reference.executescript(SCHEMA)
        statements=[r[0] for r in reference.execute(
            "SELECT sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' AND tbl_name NOT IN ('snapshots','decisions','learning_predictions') AND sql IS NOT NULL ORDER BY CASE type WHEN 'table' THEN 0 WHEN 'index' THEN 1 ELSE 2 END")]
    with sqlite3.connect(path) as c:
        for sql in statements:c.execute(sql)
        cs.initialize_dictionary_schema(c)


def test_store_calls_keep_ids_raw_columns_order_limits_and_batch_transactions(tmp_path):
    legacy=Store(tmp_path/'legacy.db')
    path=tmp_path/'compact.db';create_compact(path);compact=Store(path)
    prediction=SimpleNamespace(family='f',p5=.1,p10=.2,p25=.3,p_rug=.1,p_stagnation=.3,
        uncertainty=.2,expected_utility=.4,reasons=['raw'])
    result=Assessment(True,50,60,'x',[],[],{'one':1})
    samples=[_base_snapshot(address=address,captured_at=(datetime.now(timezone.utc)+timedelta(seconds=i)).isoformat())
        for i,address in enumerate(('a','b','a'))]
    try:
        for store in (legacy,compact):
            for sample in samples:
                store.record_snapshot(sample);store.record_decision(sample,result)
            store.record_learning_prediction(samples[0],prediction,5,'v',commit=False)
            store.record_learning_prediction(samples[0],prediction,5,'v',commit=False)
            assert store.conn.in_transaction
            with sqlite3.connect(store.path) as outside:
                assert outside.execute('SELECT count(*) FROM learning_predictions').fetchone()[0]==0
            store.conn.commit()
        for table in cs.PUBLIC:
            a=[tuple(r) for r in legacy.conn.execute('SELECT * FROM '+table+' ORDER BY id')]
            b=[tuple(r) for r in compact.conn.execute('SELECT * FROM '+table+' ORDER BY id')]
            assert a==b
        assert compact.tracked_addresses(limit=10)==legacy.tracked_addresses(limit=10)
        assert compact.tracked_addresses(limit=1)==legacy.tracked_addresses(limit=1)
        assert compact.latest_snapshot('a').as_dict()==legacy.latest_snapshot('a').as_dict()
        assert compact.snapshot_at_or_before('a',samples[0].captured_at).as_dict()==samples[0].as_dict()
        assert compact.recent_pass_count('a')==legacy.recent_pass_count('a')
        assert _table_names(compact.conn)>=set(cs.PUBLIC)
        # Existing immutable outcome/cookie contracts are still present.
        assert cs.validate_store_schema(compact.conn,SCHEMA) is None
    finally:legacy.close();compact.close()
    reopened=Store(path)
    try:
        assert reopened.latest_snapshot('b').address=='b'
        assert reopened.conn.execute('SELECT count(*) FROM learning_predictions').fetchone()[0]==1
    finally:reopened.close()


def test_incomplete_compact_store_rejected_before_any_legacy_ddl(tmp_path):
    path=tmp_path/'partial.db'
    with sqlite3.connect(path) as c:cs.initialize_dictionary_schema(c)
    with pytest.raises(ValueError,match='remainder'):Store(path)
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT name FROM sqlite_schema WHERE name='account_state'").fetchall()==[]


def test_watch_bus_is_emitted_after_compact_commit(tmp_path,monkeypatch):
    from mykonos_meme_scout import store as module
    path=tmp_path/'compact.db';create_compact(path);store=Store(path)
    seen=[]
    class Bus:
        def sendto(self,payload,target):
            with sqlite3.connect(path) as c:
                assert c.execute('SELECT decision FROM decisions').fetchone()[0]=='WATCH'
            seen.append((json.loads(payload),target))
    monkeypatch.setattr(module,'_DECISION_BUS_SOCKET',Bus())
    try:store.record_decision(_base_snapshot(),Assessment(False,1,2,'x',[],[],{}))
    finally:store.close()
    assert len(seen)==len(module._DECISION_BUS_TARGETS)
    assert all(item[0]['decision']=='WATCH' for item in seen)


def test_historical_and_training_readers_preserve_compact_rows_and_domains(tmp_path):
    from mykonos_meme_scout import simulation,training_data
    path=tmp_path/'compact.db';create_compact(path);store=Store(path)
    start=datetime(2026,1,1,12,tzinfo=timezone.utc)
    for i in range(3):
        store.record_snapshot(_base_snapshot(address='mint',captured_at=(start+timedelta(seconds=i*2)).isoformat(),price_usd=1+i/10))
    row=(1,'balanced','m1','mint','T',start.isoformat(),1,1.,100.,.5,.4,.3,.1,.2,.3,2.,'[]')
    store._dictionary_storage.prediction(row)
    store.conn.execute("INSERT INTO learning_outcomes VALUES(1,'2026-01-01T12:01:00+00:00',1,0,0,0,0,6,-1,5)")
    store.conn.commit()
    sequences=simulation.load_historical_sequences(path)
    assert len(sequences)==1 and len(sequences[0].observations)==3
    assert sequences[0].source_integrity['source_main_wal_verified_unchanged']
    records,manifest=training_data._load_real_records(path,10,domain=training_data.HISTORICAL_DOMAIN)
    assert len(records)==1 and manifest['source_main_wal_verified_unchanged']
    with sqlite3.connect(path) as c:c.execute('CREATE TABLE simulation_metadata(id INTEGER)')
    with pytest.raises(ValueError,match='simulation database'):simulation.load_historical_sequences(path)
    with pytest.raises(ValueError):training_data._load_real_records(path,10,domain=training_data.HISTORICAL_DOMAIN)
    store.close()


def test_brain_reader_recognizes_valid_compact_source(tmp_path,monkeypatch):
    from mykonos_meme_scout.brain_v1 import data
    path=tmp_path/'compact.db';create_compact(path);store=Store(path)
    for i in range(140):
        store.record_snapshot(_base_snapshot(address='mint',captured_at=(datetime(2026,1,1,tzinfo=timezone.utc)+timedelta(seconds=i)).isoformat()))
    store.close()
    monkeypatch.setattr(data,'source_databases',lambda root:[path])
    dataset=data.prepare_dataset(tmp_path,sequence_length=2,stride_seconds=1)
    source=dataset['audit']['sources'][0]
    assert source['role']=='REAL_RAW' and source['rows']==140
    assert dataset['records']
    with sqlite3.connect(path) as c:c.execute('CREATE TABLE simulation_metadata(id INTEGER)')
    blocked=data.prepare_dataset(tmp_path,sequence_length=2,stride_seconds=1)
    assert blocked['records']==[] and blocked['all_coin_ids']==[]
    assert blocked['audit']['sources']==[{'path':'compact.db','role':'SIMULATION','used_for_market_validation':False}]
