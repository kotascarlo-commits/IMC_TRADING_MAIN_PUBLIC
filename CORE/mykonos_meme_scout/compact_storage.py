"""Dictionary-backed Store representation. No original-file migration here.

Initialization refuses an existing legacy/compact representation. Callers
own source freeze, copying, rowid preservation and final publication.
"""
from __future__ import annotations

import functools
import sqlite3
from contextlib import contextmanager
from types import MappingProxyType

from .storage_codec import dictionary_key,split_snapshot_json

FORMAT='IMC_DICTIONARY_1'
PUBLIC=('snapshots','decisions','learning_predictions')
SNAPSHOT_COLUMNS=('id','captured_at','address','symbol','price_usd','payload_json')
DECISION_COLUMNS=('id','captured_at','address','symbol','decision','score','confidence','reasons_json','components_json')
PREDICTION_COLUMNS=('id','model_family','model_version','address','symbol','predicted_at','horizon_minutes',
    'entry_price','entry_liquidity','p5','p10','p25','p_rug','p_stagnation','uncertainty','expected_utility','reasons_json')

_TABLES={
    '_imc_storage_meta':'key TEXT PRIMARY KEY,value TEXT NOT NULL',
    '_snapshot_payloads':'id INTEGER PRIMARY KEY,digest TEXT NOT NULL UNIQUE,prefix TEXT NOT NULL,suffix TEXT NOT NULL',
    '_decision_details':'id INTEGER PRIMARY KEY,digest TEXT NOT NULL UNIQUE,reasons_json NOT NULL,components_json NOT NULL',
    '_prediction_reasons':'id INTEGER PRIMARY KEY,digest TEXT NOT NULL UNIQUE,reasons_json NOT NULL',
    '_snapshot_records':'''id INTEGER PRIMARY KEY,captured_at TEXT NOT NULL,address TEXT NOT NULL,
        symbol TEXT NOT NULL,price_usd REAL NOT NULL,payload_id INTEGER REFERENCES _snapshot_payloads(id),
        capture_value TEXT,payload_original,
        CHECK ((payload_id IS NULL AND capture_value IS NULL AND payload_original IS NOT NULL)
            OR (payload_id IS NOT NULL AND typeof(capture_value)='text' AND payload_original IS NULL))''',
    '_decision_records':'''id INTEGER PRIMARY KEY,captured_at TEXT NOT NULL,address TEXT NOT NULL,
        symbol TEXT NOT NULL,decision TEXT NOT NULL,score INTEGER NOT NULL,confidence INTEGER NOT NULL,
        details_id INTEGER NOT NULL REFERENCES _decision_details(id)''',
    '_prediction_records':'''id INTEGER PRIMARY KEY,model_family TEXT NOT NULL,model_version TEXT NOT NULL,
        address TEXT NOT NULL,symbol TEXT NOT NULL,predicted_at TEXT NOT NULL,horizon_minutes INTEGER NOT NULL,
        entry_price REAL NOT NULL,entry_liquidity REAL NOT NULL,p5 REAL NOT NULL,p10 REAL NOT NULL,p25 REAL NOT NULL,
        p_rug REAL NOT NULL,p_stagnation REAL NOT NULL,uncertainty REAL NOT NULL,expected_utility REAL NOT NULL,
        reasons_id INTEGER NOT NULL REFERENCES _prediction_reasons(id),
        UNIQUE(model_family,model_version,address,predicted_at,horizon_minutes)''',
}
_INDEXES={
    'idx_snapshots_address_time':('_snapshot_records','address,captured_at DESC'),
    'idx_decisions_address_time':('_decision_records','address,captured_at DESC'),
    'idx_decisions_captured_address_id':('_decision_records','captured_at,address,id'),
    'idx_learning_address_time':('_prediction_records','address,predicted_at DESC'),
}
_VIEWS={
    'snapshots':'''SELECT r.id,r.captured_at,r.address,r.symbol,r.price_usd,
        CASE WHEN r.payload_id IS NULL THEN r.payload_original
             ELSE d.prefix||r.capture_value||d.suffix END AS payload_json
        FROM _snapshot_records r LEFT JOIN _snapshot_payloads d ON d.id=r.payload_id''',
    'decisions':'''SELECT r.id,r.captured_at,r.address,r.symbol,r.decision,r.score,r.confidence,
        d.reasons_json,d.components_json FROM _decision_records r JOIN _decision_details d ON d.id=r.details_id''',
    'learning_predictions':'''SELECT r.id,r.model_family,r.model_version,r.address,r.symbol,r.predicted_at,
        r.horizon_minutes,r.entry_price,r.entry_liquidity,r.p5,r.p10,r.p25,r.p_rug,r.p_stagnation,
        r.uncertainty,r.expected_utility,d.reasons_json
        FROM _prediction_records r JOIN _prediction_reasons d ON d.id=r.reasons_id''',
}


def schema_statements():
    for name,columns in _TABLES.items():yield 'CREATE TABLE '+name+'('+columns+')'
    for name,(table,columns) in _INDEXES.items():yield 'CREATE INDEX '+name+' ON '+table+'('+columns+')'
    for name,select in _VIEWS.items():yield 'CREATE VIEW '+name+' AS '+select
    # All dictionary entries are immutable, including entries not yet referenced.
    for table in ('_snapshot_payloads','_decision_details','_prediction_reasons','_prediction_records'):
        for operation in ('UPDATE','DELETE'):
            yield ('CREATE TRIGGER '+table+'_'+operation.lower()+' BEFORE '+operation+' ON '+table+
                " BEGIN SELECT RAISE(ABORT,'compact storage is append-only'); END")
        conflict='id=NEW.id'
        if table=='_prediction_records':
            conflict+=' OR (model_family=NEW.model_family AND model_version=NEW.model_version AND address=NEW.address AND predicted_at=NEW.predicted_at AND horizon_minutes=NEW.horizon_minutes)'
        else:conflict+=' OR digest=NEW.digest'
        yield ('CREATE TRIGGER '+table+'_insert_conflict BEFORE INSERT ON '+table+
            ' WHEN EXISTS(SELECT 1 FROM '+table+' WHERE '+conflict+
            ") BEGIN SELECT RAISE(ABORT,'compact storage is append-only'); END")
    # Enforce join cardinality even on connections with foreign_keys disabled.
    for table,column,dictionary in (('_snapshot_records','payload_id','_snapshot_payloads'),
            ('_decision_records','details_id','_decision_details'),('_prediction_records','reasons_id','_prediction_reasons')):
        for operation in ('INSERT','UPDATE'):
            yield ('CREATE TRIGGER '+table+'_'+operation.lower()+'_reference BEFORE '+operation+' ON '+table+
                ' WHEN NEW.'+column+' IS NOT NULL AND NOT EXISTS(SELECT 1 FROM '+dictionary+' WHERE id=NEW.'+column+
                ") BEGIN SELECT RAISE(ABORT,'compact reference missing'); END")


def _schema_rows(connection):
    return {row[1]:(row[0],row[2],row[3]) for row in connection.execute(
        'SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE name NOT GLOB \'sqlite_*\'')}


@functools.lru_cache(maxsize=1)
def expected_schema():
    connection=sqlite3.connect(':memory:')
    try:
        for sql in schema_statements():connection.execute(sql)
        return MappingProxyType(_schema_rows(connection))
    finally:connection.close()


def detect_format(connection):
    """Strict shared reader/writer validator; returns None for legacy sources."""
    actual=_schema_rows(connection);expected=expected_schema()
    if '_imc_storage_meta' not in actual:
        exclusive=set(expected)-set(PUBLIC)-set(_INDEXES)
        if set(actual)&exclusive:raise ValueError('partial_compact_schema')
        return None
    for name,shape in expected.items():
        if actual.get(name)!=shape:raise ValueError('compact_schema_mismatch')
    for name,shape in actual.items():
        if shape[1] in expected and name not in expected:raise ValueError('unexpected_compact_schema_object')
    marker=connection.execute('SELECT key,value FROM _imc_storage_meta ORDER BY key').fetchall()
    if [tuple(row) for row in marker]!=[('format',FORMAT)]:raise ValueError('compact_marker_mismatch')
    return FORMAT


def logical_table_names(connection):
    names={row[0] for row in connection.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
    if detect_format(connection)==FORMAT:names.update(PUBLIC)
    return names


def validate_store_schema(connection,legacy_schema):
    """Require the exact final Store schema before skipping legacy DDL."""
    if detect_format(connection)!=FORMAT:raise ValueError('dictionary_storage_required')
    reference=sqlite3.connect(':memory:')
    try:
        reference.executescript(legacy_schema)
        required={name:shape for name,shape in _schema_rows(reference).items()
            if shape[1] not in PUBLIC}
    finally:reference.close()
    actual=_schema_rows(connection)
    if actual!={**expected_schema(),**required}:
        raise ValueError('compact_store_remainder_mismatch')


def validate_dictionary_contents(connection,*,check=lambda:None):
    """Offline full physical validation, including data hidden by public views.

    Caller supplies a frozen read snapshot and SQL progress/deadline handler.
    This is intentionally separate from the runtime Store constructor.
    """
    check()
    if detect_format(connection)!=FORMAT:raise ValueError('dictionary_storage_required')
    contracts=(
        ('_snapshot_payloads',('prefix','suffix'),'_snapshot_records','payload_id','snapshots'),
        ('_decision_details',('reasons_json','components_json'),'_decision_records','details_id','decisions'),
        ('_prediction_reasons',('reasons_json',),'_prediction_records','reasons_id','learning_predictions'),
    )
    counts={}
    for table,columns,records,key,public in contracts:
        count=0
        for row in connection.execute('SELECT id,digest,'+','.join(columns)+' FROM '+table+' ORDER BY id'):
            check()
            identifier,digest,*values=tuple(row)
            if (type(identifier)is not int or identifier<=0
                    or any(type(v)not in (str,bytes) for v in values)
                    or type(digest)is not str or digest!=dictionary_key(*values)):
                raise ValueError('compact_dictionary_digest_mismatch')
            count+=1
        check()
        # EXCEPT bounds memory through SQLite's temp store rather than keeping
        # millions of dictionary IDs in a Python set. LIMIT is on the result.
        unused=connection.execute('SELECT id FROM '+table+' EXCEPT SELECT '+key+
            ' FROM '+records+' WHERE '+key+' IS NOT NULL LIMIT 1').fetchone()
        if unused is not None:raise ValueError('compact_dictionary_unused_entry')
        physical=connection.execute('SELECT count(*) FROM '+records).fetchone()[0]
        logical=connection.execute('SELECT count(*) FROM '+public).fetchone()[0]
        if physical!=logical:raise ValueError('compact_dictionary_hidden_record')
        counts[table]=count
    if connection.execute('PRAGMA foreign_key_check').fetchone() is not None:
        raise ValueError('compact_dictionary_reference_mismatch')
    check()
    return counts


def initialize_dictionary_schema(connection):
    # Never reinterpret or drop a populated legacy representation in place.
    actual=_schema_rows(connection)
    if set(actual)&(set(expected_schema())|set(PUBLIC)):raise ValueError('storage_representation_already_exists')
    if connection.in_transaction:raise ValueError('initialization_requires_idle_connection')
    connection.execute('BEGIN')
    try:
        for sql in schema_statements():connection.execute(sql)
        connection.execute('INSERT INTO _imc_storage_meta VALUES(?,?)',('format',FORMAT))
        detect_format(connection)
        connection.commit()
    except Exception:connection.rollback();raise


@contextmanager
def _write(connection):
    # Preserve Python sqlite3 DML semantics, including commit=False callers.
    started=not connection.in_transaction
    if started:connection.execute('BEGIN')
    connection.execute('SAVEPOINT imc_dictionary_write')
    try:
        yield
        connection.execute('RELEASE imc_dictionary_write')
    except Exception:
        connection.execute('ROLLBACK TO imc_dictionary_write')
        connection.execute('RELEASE imc_dictionary_write')
        if started:connection.rollback()
        raise


class DictionaryStorage:
    def __init__(self,connection):
        if detect_format(connection)!=FORMAT:raise ValueError('dictionary_storage_required')
        self.connection=connection

    def _intern(self,table,columns,values):
        if any(type(v) not in (str,bytes) for v in values):raise TypeError('unsupported_dictionary_value')
        digest=dictionary_key(*values)
        row=self.connection.execute('SELECT id,'+','.join(columns)+' FROM '+table+' WHERE digest=?',(digest,)).fetchone()
        if row is None:
            self.connection.execute('INSERT INTO '+table+'(digest,'+','.join(columns)+') VALUES('
                +','.join('?' for _ in range(len(values)+1))+')',(digest,*values))
            row=self.connection.execute('SELECT id,'+','.join(columns)+' FROM '+table+' WHERE digest=?',(digest,)).fetchone()
        # SQLite value types are part of equality; never trust a digest alone.
        if row is None or any(type(a)is not type(b) or a!=b for a,b in zip(tuple(row)[1:],values)):
            raise ValueError('dictionary_collision')
        return row[0]

    def snapshot(self,row):
        if len(row)!=6:raise ValueError('snapshot_row_shape')
        parts=split_snapshot_json(row[-1])
        with _write(self.connection):
            if parts is None:payload_id,capture,original=None,None,row[-1]
            else:
                payload_id=self._intern('_snapshot_payloads',('prefix','suffix'),(parts[0],parts[2]))
                capture,original=parts[1],None
            cursor=self.connection.execute('INSERT INTO _snapshot_records VALUES(?,?,?,?,?,?,?,?)',
                (*row[:-1],payload_id,capture,original))
            return cursor.lastrowid

    def decision(self,row):
        if len(row)!=9:raise ValueError('decision_row_shape')
        with _write(self.connection):
            details=self._intern('_decision_details',('reasons_json','components_json'),tuple(row[-2:]))
            cursor=self.connection.execute('INSERT INTO _decision_records VALUES(?,?,?,?,?,?,?,?)',(*row[:-2],details))
            return cursor.lastrowid

    def prediction(self,row):
        if len(row)!=17:raise ValueError('prediction_row_shape')
        with _write(self.connection):
            existing=self.connection.execute('SELECT 1 FROM _prediction_records WHERE id=? OR '
                '(model_family=? AND model_version=? AND address=? AND predicted_at=? AND horizon_minutes=?)',
                (row[0],row[1],row[2],row[3],row[5],row[6])).fetchone()
            if existing is not None:return None
            reasons=self._intern('_prediction_reasons',('reasons_json',),(row[-1],))
            cursor=self.connection.execute('INSERT OR IGNORE INTO _prediction_records VALUES('
                +','.join('?' for _ in range(17))+')',(*row[:-1],reasons))
            if cursor.rowcount==0:
                # A duplicate must not leave a new, unused dictionary entry.
                self.connection.execute('ROLLBACK TO imc_dictionary_write')
                return None
            return cursor.lastrowid
