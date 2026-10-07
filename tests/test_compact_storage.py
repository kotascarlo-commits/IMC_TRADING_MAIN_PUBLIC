"""Isolated SQLite compatibility tests; never open the project database."""
import sqlite3

import pytest

from mykonos_meme_scout import compact_storage as cs


@pytest.fixture
def db():
    c=sqlite3.connect(':memory:')
    cs.initialize_dictionary_schema(c)
    yield c
    c.close()


def prediction(identity=1, reasons='["keep"]'):
    return (identity,'f','v','a','s','time',5,1.0,100.0,.1,.2,.3,.1,.3,.2,.4,reasons)


def physical_fixture(db):
    store=cs.DictionaryStorage(db)
    for i in (1,2):store.snapshot((i,str(i),'a','s',1.,'{"captured_at":"'+str(i)+'","x":1}'))
    store.decision((1,'t','a','s','BUY',1,2,'r','c'))
    store.prediction(prediction())
    db.commit()


def test_offline_dictionary_physical_validation_is_readonly(db):
    physical_fixture(db)
    before=db.total_changes
    assert cs.validate_dictionary_contents(db)=={
        '_snapshot_payloads':1,'_decision_details':1,'_prediction_reasons':1}
    assert db.total_changes==before


@pytest.mark.parametrize('table,columns,values',[
    ('_snapshot_payloads',('prefix','suffix'),('extra','payload')),
    ('_decision_details',('reasons_json','components_json'),('extra','components')),
    ('_prediction_reasons',('reasons_json',),('extra',)),
])
def test_unused_dictionary_rows_cannot_hide_behind_equal_public_views(db,table,columns,values):
    physical_fixture(db)
    before=[db.execute('SELECT * FROM '+name).fetchall() for name in cs.PUBLIC]
    db.execute('INSERT INTO '+table+'(id,digest,'+','.join(columns)+') VALUES('
        +','.join('?' for _ in range(2+len(values)))+')',(999,cs.dictionary_key(*values),*values))
    assert before==[db.execute('SELECT * FROM '+name).fetchall() for name in cs.PUBLIC]
    with pytest.raises(ValueError,match='unused_entry'):cs.validate_dictionary_contents(db)


def test_dictionary_digest_corruption_detected_even_with_equal_public_views(db):
    physical_fixture(db)
    name='_decision_details_update'
    sql=db.execute('SELECT sql FROM sqlite_schema WHERE name=?',(name,)).fetchone()[0]
    before=db.execute('SELECT * FROM decisions').fetchall()
    db.execute('DROP TRIGGER '+name)
    db.execute("UPDATE _decision_details SET digest='wrong'")
    db.execute(sql)
    assert db.execute('SELECT * FROM decisions').fetchall()==before
    with pytest.raises(ValueError,match='digest_mismatch'):cs.validate_dictionary_contents(db)


@pytest.mark.parametrize('table,column,expected',[
    ('_snapshot_records','payload_id','reference_mismatch'),
    ('_decision_records','details_id','hidden_record'),
])
def test_physical_orphan_records_rejected_with_foreign_keys_disabled(db,table,column,expected):
    physical_fixture(db)
    if table=='_decision_records':
        cs.DictionaryStorage(db).decision((2,'t','a','s','BUY',1,2,'r','c'))
    name=table+'_update_reference'
    sql=db.execute('SELECT sql FROM sqlite_schema WHERE name=?',(name,)).fetchone()[0]
    assert db.execute('PRAGMA foreign_keys').fetchone()[0]==0
    db.execute('DROP TRIGGER '+name)
    db.execute('UPDATE '+table+' SET '+column+'=999 WHERE id=1')
    db.execute(sql)
    assert cs.detect_format(db)==cs.FORMAT
    with pytest.raises(ValueError,match=expected):cs.validate_dictionary_contents(db)


def test_physical_dictionary_deadline_is_checked_inside_stream(db):
    physical_fixture(db)
    calls=[]
    def check():
        calls.append(True)
        if len(calls)==2:raise TimeoutError('fixture_deadline')
    before=db.total_changes
    with pytest.raises(TimeoutError,match='fixture_deadline'):
        cs.validate_dictionary_contents(db,check=check)
    assert db.total_changes==before and len(calls)==2


@pytest.mark.parametrize('query',['EXCEPT','SELECT count(*) FROM _snapshot_records',
    'SELECT count(*) FROM snapshots','PRAGMA foreign_key_check'])
def test_sql_deadline_handler_covers_dictionary_set_count_and_fk_queries(db,query):
    physical_fixture(db)
    expired=[False]
    def trace(sql):
        if query in sql:expired[0]=True
    db.set_trace_callback(trace)
    db.set_progress_handler(lambda:int(expired[0]),1)
    before=db.total_changes
    try:
        with pytest.raises(sqlite3.OperationalError,match='interrupted'):
            cs.validate_dictionary_contents(db)
        assert expired[0] and db.total_changes==before
    finally:
        db.set_progress_handler(None,0)
        db.set_trace_callback(None)


@pytest.mark.parametrize('raw',[
    '{"captured_at":"first","price":1.00,"x":"日本"}',
    ' \n{"captured\\u005fat":"a\\/b", "price":1e-3}\r\n',
    '{"captured_at":"a","captured_at":"b"}',
    '{"captured_at":null}', 'invalid', b'\xff\x00',
])
def test_snapshot_exact_types_and_bytes_roundtrip(db,raw):
    row=(19,'different column timestamp','a','s',1.0,raw)
    assert cs.DictionaryStorage(db).snapshot(row)==19
    actual=db.execute('SELECT * FROM snapshots').fetchone()
    assert actual==row and type(actual[-1]) is type(raw)
    assert db.execute('PRAGMA integrity_check').fetchall()==[('ok',)]
    assert db.execute('PRAGMA foreign_key_check').fetchall()==[]


def test_snapshots_share_fragments_without_changing_capture(db):
    store=cs.DictionaryStorage(db)
    for i in (1,2):store.snapshot((i,str(i),'a','s',1.,'{"captured_at":"'+str(i)+'","x":1.00}'))
    assert db.execute('SELECT count(*) FROM _snapshot_payloads').fetchone()[0]==1
    assert [r[0] for r in db.execute('SELECT payload_json FROM snapshots ORDER BY id')]==[
        '{"captured_at":"1","x":1.00}','{"captured_at":"2","x":1.00}']


@pytest.mark.parametrize('reasons,components',[('["one"]','{"x":1.00}'),(b'\xff',b'\x00'),('text',b'text')])
def test_decision_exact_dictionary_types(db,reasons,components):
    row=(7,'now','a','s','BUY',3,4,reasons,components)
    assert cs.DictionaryStorage(db).decision(row)==7
    actual=db.execute('SELECT * FROM decisions').fetchone()
    assert actual==row
    assert [type(v) for v in actual]==[type(v) for v in row]


def test_prediction_duplicate_does_not_create_unused_dictionary(db):
    store=cs.DictionaryStorage(db)
    assert store.prediction(prediction())==1
    assert store.prediction(prediction(2,'different')) is None
    assert db.execute('SELECT * FROM learning_predictions').fetchall()==[prediction()]
    assert db.execute('SELECT count(*) FROM _prediction_reasons').fetchone()[0]==1
    assert db.in_transaction
    db.rollback()
    assert db.execute('SELECT count(*) FROM learning_predictions').fetchone()[0]==0
    assert db.execute('SELECT count(*) FROM _prediction_reasons').fetchone()[0]==0


def test_collision_rolls_back_entire_write_preserving_prior_transaction(db,monkeypatch):
    monkeypatch.setattr(cs,'dictionary_key',lambda *values:'forced digest')
    store=cs.DictionaryStorage(db)
    first=(1,'now','a','s','HOLD',1,2,'first','parts')
    store.decision(first)
    with pytest.raises(ValueError,match='dictionary_collision'):
        store.decision((2,*first[1:-2],'second','parts'))
    assert db.execute('SELECT * FROM decisions').fetchall()==[first]
    assert db.execute('SELECT count(*) FROM _decision_details').fetchone()[0]==1
    db.rollback()
    assert db.execute('SELECT count(*) FROM decisions').fetchone()[0]==0


def test_failed_row_insert_does_not_leave_dictionary(db):
    store=cs.DictionaryStorage(db)
    with pytest.raises(sqlite3.IntegrityError):store.decision((1,'t',None,'s','BUY',1,2,'r','c'))
    assert not db.in_transaction
    assert db.execute('SELECT count(*) FROM _decision_details').fetchone()[0]==0


@pytest.mark.parametrize('table',['_snapshot_payloads','_decision_details','_prediction_reasons','_prediction_records'])
@pytest.mark.parametrize('operation',['UPDATE','DELETE'])
def test_append_only_even_when_foreign_keys_disabled(db,table,operation):
    store=cs.DictionaryStorage(db)
    store.snapshot((1,'t','a','s',1.,'{"captured_at":"t"}'))
    store.decision((1,'t','a','s','BUY',1,2,'r','c'))
    store.prediction(prediction())
    db.commit()
    assert db.execute('PRAGMA foreign_keys').fetchone()[0]==0
    sql=('UPDATE '+table+' SET id=id' if operation=='UPDATE' else 'DELETE FROM '+table)
    with pytest.raises(sqlite3.IntegrityError,match='append-only'):db.execute(sql)
    assert db.execute('SELECT count(*) FROM '+table).fetchone()[0]==1


def test_reference_and_exclusive_fallback_guards(db):
    with pytest.raises(sqlite3.IntegrityError,match='reference missing'):
        db.execute('INSERT INTO _decision_records VALUES(1,?,?,?,?,?,?,?)',('t','a','s','BUY',1,2,999))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute('INSERT INTO _snapshot_records VALUES(1,?,?,?,?,?,?,?)',('t','a','s',1.,None,'"t"','original'))
    assert db.execute('SELECT count(*) FROM snapshots').fetchone()[0]==0


@pytest.mark.parametrize('mutation',[
    'DROP TRIGGER _prediction_records_no_such_trigger',
    'DROP TRIGGER _prediction_records_update',
    "UPDATE _imc_storage_meta SET value='unknown'",
    'CREATE INDEX extra ON _decision_records(address)',
])
def test_schema_validator_rejects_modified_contract(db,mutation):
    if 'no_such_trigger' in mutation:
        db.execute('DROP VIEW decisions')
        db.execute('CREATE VIEW decisions AS SELECT * FROM _decision_records')
    else:db.execute(mutation)
    with pytest.raises(ValueError):cs.detect_format(db)


def test_initialization_refuses_legacy_and_partial_without_changing_them():
    for table in ('snapshots','_snapshot_records'):
        with sqlite3.connect(':memory:') as c:
            c.execute('CREATE TABLE '+table+'(id INTEGER)')
            before=c.execute('SELECT * FROM sqlite_schema').fetchall()
            with pytest.raises(ValueError):cs.initialize_dictionary_schema(c)
            assert c.execute('SELECT * FROM sqlite_schema').fetchall()==before
            if table=='_snapshot_records':
                with pytest.raises(ValueError):cs.detect_format(c)
            else:assert cs.detect_format(c) is None


def test_read_queries_use_core_indexes_and_keep_limit_order(db):
    store=cs.DictionaryStorage(db)
    for i in range(1,5):
        store.snapshot((i,str(i),'a','s',float(i),'raw '+str(i)))
        store.decision((i,str(i),'a','s','BUY',1,2,'r','c'))
    assert db.execute("SELECT id,payload_json FROM snapshots WHERE address='a' ORDER BY captured_at DESC,id DESC LIMIT 1").fetchall()==[(4,'raw 4')]
    for view,index in [('snapshots','idx_snapshots_address_time'),('decisions','idx_decisions_address_time')]:
        plan=db.execute('EXPLAIN QUERY PLAN SELECT * FROM '+view+" WHERE address='a' ORDER BY captured_at DESC LIMIT 1").fetchall()
        assert any(index in row[-1] for row in plan)
    assert set(cs.PUBLIC)<=cs.logical_table_names(db)


def test_commit_false_persists_only_after_explicit_commit(tmp_path):
    path=tmp_path/'isolated.sqlite'
    c=sqlite3.connect(path);cs.initialize_dictionary_schema(c)
    cs.DictionaryStorage(c).prediction(prediction())
    with sqlite3.connect(path) as reader:
        assert reader.execute('SELECT count(*) FROM learning_predictions').fetchone()[0]==0
    c.commit();c.close()
    with sqlite3.connect(path) as reader:
        assert cs.detect_format(reader)==cs.FORMAT
        assert reader.execute('SELECT * FROM learning_predictions').fetchall()==[prediction()]


def test_complete_legacy_schema_is_accepted_without_reinterpretation():
    from mykonos_meme_scout.store import SCHEMA
    with sqlite3.connect(':memory:') as c:
        c.executescript(SCHEMA)
        assert cs.detect_format(c) is None
        assert set(cs.PUBLIC)<=cs.logical_table_names(c)


@pytest.mark.parametrize('table',['_snapshot_payloads','_decision_details','_prediction_reasons','_prediction_records'])
@pytest.mark.parametrize('conflict',['primary','unique'])
def test_replace_cannot_mutate_or_orphan_append_only_rows(db,table,conflict):
    store=cs.DictionaryStorage(db)
    store.snapshot((1,'t','a','s',1.,'{"captured_at":"t"}'))
    store.decision((1,'t','a','s','BUY',1,2,'r','c'))
    store.prediction(prediction())
    db.commit()
    assert db.execute('PRAGMA recursive_triggers').fetchone()[0]==0
    before=db.execute('SELECT * FROM '+table).fetchall()
    row=list(before[0])
    if conflict=='unique':row[0]=99
    elif table=='_prediction_records':row[1]='changed family'
    else:row[1]='changed digest'
    if table!='_prediction_records':row[-1]='changed raw value'
    with pytest.raises(sqlite3.IntegrityError,match='append-only'):
        db.execute('INSERT OR REPLACE INTO '+table+' VALUES('+','.join('?' for _ in row)+')',row)
    assert db.execute('SELECT * FROM '+table).fetchall()==before
    assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
    assert db.execute('SELECT count(*) FROM snapshots').fetchone()[0]==1
    assert db.execute('SELECT count(*) FROM decisions').fetchone()[0]==1
    assert db.execute('SELECT count(*) FROM learning_predictions').fetchone()[0]==1
