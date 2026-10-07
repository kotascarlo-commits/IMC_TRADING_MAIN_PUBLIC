"""Copy-builder fixtures only. No production file or process access."""
import sqlite3

import pytest

from mykonos_meme_scout import compact_storage,storage_copy
from mykonos_meme_scout.sqlite_source import readonly_connection
from mykonos_meme_scout.store import SCHEMA


@pytest.fixture
def source(tmp_path):
    path=tmp_path/'source.db';writer=sqlite3.connect(path);writer.executescript(SCHEMA)
    writer.executemany('INSERT INTO snapshots VALUES(?,?,?,?,?,?)',[
        (3,'column timestamp','a','s',1.,'{"captured_at":"first","price":1.00}'),
        (99,'column timestamp','a','s',1.,b'\xff\x00')])
    writer.execute("INSERT INTO decisions VALUES(7,'t','a','s','BUY',1,2,?,?)",(b'blob reason','{"one":1.00}'))
    writer.execute("INSERT INTO learning_predictions VALUES(11,'f','v','a','s','t',5,1,100,.1,.2,.3,.1,.3,.2,.4,'raw')")
    writer.executemany('INSERT INTO model_registry(rowid,model_family,model_version,created_at,status,config_json) VALUES(?,?,?,?,?,?)',[
        (9,'f','old','same','SHADOW','old'),(101,'f','new','same','SHADOW','new')])
    writer.executemany('INSERT INTO schema_meta(rowid,key,value) VALUES(?,?,?)',[(8,'a','raw'),(81,'b','preserve')])
    writer.commit();read=readonly_connection(path)
    yield path,writer,read
    read.close();writer.close()


def test_lossless_copy_preserves_hidden_rowids_all_tables_and_raw_types(source,tmp_path,monkeypatch):
    path,writer,read=source
    from mykonos_meme_scout import store
    monkeypatch.setattr(store.Store,'__init__',lambda *args,**kwargs:(_ for _ in ()).throw(AssertionError('no Store constructor')))
    before=path.read_bytes();events=[]
    target=tmp_path/'new.db'
    report=storage_copy.build_dictionary_copy(read,target,batch_size=1,progress=events.append)
    assert report['complete'] and report['integrity'] and report['foreign_keys']
    assert report['source_row_proofs']==report['destination_row_proofs']
    assert not report['eligible_for_publication'] and not report['raw_backup_verified']
    assert read.total_changes==0 and path.read_bytes()==before
    assert set(report['source_row_proofs'])=={name for name,shape in storage_copy.legacy_schema().items() if shape[0]=='table'}
    assert report['source_row_proofs']['model_registry']['first_rowid']==9
    assert report['source_row_proofs']['model_registry']['last_rowid']==101
    assert report['source_row_proofs']['schema_meta']['last_rowid']==81
    with sqlite3.connect(target) as c:
        assert compact_storage.detect_format(c)==compact_storage.FORMAT
        assert c.execute('SELECT rowid,* FROM model_registry ORDER BY rowid').fetchall()==writer.execute('SELECT rowid,* FROM model_registry ORDER BY rowid').fetchall()
        assert c.execute('SELECT model_version FROM model_registry ORDER BY created_at DESC,rowid DESC LIMIT 1').fetchone()[0]=='new'
        assert c.execute('SELECT * FROM snapshots ORDER BY id').fetchall()==writer.execute('SELECT * FROM snapshots ORDER BY id').fetchall()
        assert isinstance(c.execute('SELECT reasons_json FROM decisions').fetchone()[0],bytes)
        assert c.execute('SELECT rowid,* FROM schema_meta ORDER BY rowid').fetchall()==writer.execute('SELECT rowid,* FROM schema_meta ORDER BY rowid').fetchall()
    assert events[-1]['phase']=='VERIFIED'


def test_orphans_are_preserved_and_reported_not_repaired(source,tmp_path):
    _,writer,read=source
    read.rollback()
    writer.execute("INSERT INTO outcomes VALUES(999,5,'t',1,2,100)");writer.commit()
    read.execute('BEGIN');read.execute('SELECT count(*) FROM sqlite_schema').fetchone()
    report=storage_copy.build_dictionary_copy(read,tmp_path/'new.db')
    assert report['semantic_reference_counts']['outcomes.decision_id']==1
    assert report['source_row_proofs']['outcomes']['rows']==1
    assert not report['eligible_for_publication']


@pytest.mark.parametrize('extra', [
    'CREATE TABLE extra_evidence(value)',
    'CREATE INDEX extra_keep_index ON trades(id)',
    'CREATE VIEW extra_keep_view AS SELECT * FROM trades',
    'CREATE TRIGGER extra_keep_trigger AFTER INSERT ON trades BEGIN SELECT 1; END',
    'CREATE TABLE sqliteX_evidence(value)',
], ids=['table', 'index', 'view', 'trigger', 'literal-underscore'])
def test_final_store_schema_rejects_extra_keep_objects(source, tmp_path, extra):
    _, _, read = source
    target = tmp_path / 'new.db'
    storage_copy.build_dictionary_copy(read, target)
    with sqlite3.connect(target) as copied:
        compact_storage.validate_store_schema(copied, SCHEMA)
        copied.execute(extra)
        before = copied.total_changes
        with pytest.raises(ValueError, match='compact_store_remainder_mismatch'):
            compact_storage.validate_store_schema(copied, SCHEMA)
        assert copied.total_changes == before


def test_existing_destination_is_never_overwritten(source,tmp_path):
    _,_,read=source;target=tmp_path/'existing';target.write_bytes(b'owner existing bytes')
    with pytest.raises(FileExistsError):storage_copy.build_dictionary_copy(read,target)
    assert target.read_bytes()==b'owner existing bytes'


def test_source_cannot_be_target(source):
    path,_,read=source
    with pytest.raises(ValueError,match='destination_is_source'):storage_copy.build_dictionary_copy(read,path)


@pytest.mark.parametrize('table',['unknown_evidence','sqliteX_evidence'])
def test_unknown_schema_rejected_before_target_creation(source,tmp_path,table):
    _,writer,read=source;read.rollback()
    writer.execute('CREATE TABLE '+table+'(id INTEGER)')
    writer.execute('INSERT INTO '+table+' VALUES(123)');writer.commit()
    read.execute('BEGIN');read.execute('SELECT count(*) FROM sqlite_schema').fetchone()
    target=tmp_path/'new.db'
    with pytest.raises(ValueError,match='unsupported_source_schema'):storage_copy.build_dictionary_copy(read,target)
    assert not target.exists()


@pytest.mark.parametrize('query_only,transaction',[(0,True),(1,False)])
def test_unprotected_connection_rejected_before_target_creation(tmp_path,query_only,transaction):
    with sqlite3.connect(':memory:') as c:
        c.executescript(SCHEMA);c.execute('PRAGMA query_only='+str(query_only))
        if transaction:c.execute('BEGIN')
        target=tmp_path/'new.db'
        with pytest.raises(ValueError,match='frozen_readonly_snapshot_required'):
            storage_copy.build_dictionary_copy(c,target)
        assert not target.exists()


def test_copy_failure_keeps_work_and_source_unchanged(source,tmp_path):
    path,_,read=source;before=path.read_bytes();target=tmp_path/'failed.db'
    def fail(event):raise RuntimeError('fixture injected abort')
    with pytest.raises(RuntimeError,match='injected abort'):
        storage_copy.build_dictionary_copy(read,target,batch_size=1,progress=fail)
    assert target.exists() and path.read_bytes()==before and read.total_changes==0


def test_full_fingerprint_detects_changed_dictionary_payload(source,tmp_path,monkeypatch):
    _,_,read=source
    original=compact_storage.DictionaryStorage.decision
    def corrupt(self,row):return original(self,(*row[:-1],'changed raw component'))
    monkeypatch.setattr(compact_storage.DictionaryStorage,'decision',corrupt)
    with pytest.raises(ValueError,match='source_target_row_mismatch'):
        storage_copy.build_dictionary_copy(read,tmp_path/'failed.db')


@pytest.mark.parametrize('batch',[True,0,50001,1.5])
def test_invalid_batch_rejected_before_target_creation(source,tmp_path,batch):
    _,_,read=source;target=tmp_path/'new.db'
    with pytest.raises(ValueError,match='invalid_batch_size'):
        storage_copy.build_dictionary_copy(read,target,batch_size=batch)
    assert not target.exists()


@pytest.mark.parametrize('deadline',[True,float('inf'),float('nan'),0])
def test_invalid_deadline_rejected_before_target_creation(source,tmp_path,deadline):
    _,_,read=source;target=tmp_path/'new.db'
    with pytest.raises(ValueError,match='invalid_copy_deadline'):
        storage_copy.build_dictionary_copy(read,target,deadline=deadline)
    assert not target.exists()


def test_shared_deadline_covers_copy_and_keeps_source_unchanged(source,tmp_path,monkeypatch):
    _,_,read=source;clock=[100.]
    monkeypatch.setattr(storage_copy.time,'monotonic',lambda:clock[0])
    def expire(event):clock[0]=102.
    with pytest.raises((TimeoutError,sqlite3.OperationalError)):
        storage_copy.build_dictionary_copy(read,tmp_path/'failed.db',batch_size=1,progress=expire,deadline=101.)
    assert read.total_changes==0


def test_final_callback_cannot_return_success_after_deadline(source,tmp_path,monkeypatch):
    _,_,read=source;clock=[100.]
    monkeypatch.setattr(storage_copy.time,'monotonic',lambda:clock[0])
    def expire(event):
        if event['phase']=='VERIFIED':clock[0]=102.
    with pytest.raises(TimeoutError,match='copy_deadline'):
        storage_copy.build_dictionary_copy(read,tmp_path/'failed.db',progress=expire,deadline=101.)


def test_source_timeout_and_handler_installed_before_initial_validation(source,tmp_path,monkeypatch):
    _,_,read=source
    original=storage_copy.validate_source
    def validation(connection):
        assert connection.execute('PRAGMA busy_timeout').fetchone()[0]==1000
        return original(connection)
    monkeypatch.setattr(storage_copy,'validate_source',validation)
    storage_copy.build_dictionary_copy(read,tmp_path/'new.db')


def test_initial_source_queries_are_deadline_guarded_and_handler_cleans_up(source,tmp_path,monkeypatch):
    _,_,read=source;clock=[100.]
    monkeypatch.setattr(storage_copy.time,'monotonic',lambda:clock[0])
    original=storage_copy.validate_source
    def validation(connection):
        clock[0]=102.
        connection.execute('WITH RECURSIVE x(n) AS (VALUES(1) UNION ALL SELECT n+1 FROM x WHERE n<100000) SELECT sum(n) FROM x').fetchone()
        return original(connection)
    monkeypatch.setattr(storage_copy,'validate_source',validation)
    target=tmp_path/'new.db'
    with pytest.raises(sqlite3.OperationalError,match='interrupted'):
        storage_copy.build_dictionary_copy(read,target,deadline=101.)
    assert not target.exists()
    # The aborted builder removes its handler even before target creation.
    assert read.execute('WITH RECURSIVE x(n) AS (VALUES(1) UNION ALL SELECT n+1 FROM x WHERE n<10000) SELECT max(n) FROM x').fetchone()[0]==10000
