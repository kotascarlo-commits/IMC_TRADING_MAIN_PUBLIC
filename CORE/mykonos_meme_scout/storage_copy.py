"""Lossless copy builder for an externally frozen, read-only legacy snapshot.

This module neither freezes writers nor creates a RAW backup, replaces source
files, starts Store, repairs data, or authorizes publication. Failed WORK files
are retained. The owning controller must provide all operational protections.
"""
from __future__ import annotations

import os
import math
import sqlite3
import time
from pathlib import Path

from . import compact_storage as compact
from .storage_codec import fingerprint_rows
from .store import SCHEMA


def quote(name):
    return '"'+name.replace('"','""')+'"'


def legacy_schema():
    with sqlite3.connect(':memory:') as reference:
        reference.executescript(SCHEMA)
        return compact._schema_rows(reference)


def validate_source(source):
    if not source.in_transaction or source.execute('PRAGMA query_only').fetchone()[0]!=1:
        raise ValueError('frozen_readonly_snapshot_required')
    expected=legacy_schema()
    if compact._schema_rows(source)!=expected:raise ValueError('unsupported_source_schema')
    return expected


REFERENCES=(
    ('trades','position_id','positions','id'),
    ('outcomes','decision_id','decisions','id'),
    ('learning_outcomes','prediction_id','learning_predictions','id'),
    ('cookie_ledger','prediction_id','learning_predictions','id'),
)


def semantic_references(connection):
    result={}
    for child,column,parent,key in REFERENCES:
        result[child+'.'+column]=connection.execute('SELECT count(*) FROM '+quote(child)+
            ' c WHERE NOT EXISTS(SELECT 1 FROM '+quote(parent)+' p WHERE p.'+quote(key)+'=c.'+quote(column)+')').fetchone()[0]
    result['cookie_family_mismatch']=connection.execute('SELECT count(*) FROM cookie_ledger c '
        'JOIN learning_predictions p ON p.id=c.prediction_id WHERE c.model_family!=p.model_family').fetchone()[0]
    return result


def _rows(connection,table,*,dictionary=False):
    rowid='id' if dictionary and table in compact.PUBLIC else 'rowid'
    return connection.execute('SELECT '+rowid+',* FROM '+quote(table)+' ORDER BY '+rowid)


def _proof(rows,table,check=lambda:None):
    bounds=[None,None]
    def values():
        for row in rows:
            check()
            if bounds[0] is None:bounds[0]=row[0]
            bounds[1]=row[0]
            yield tuple(row)
    return {**fingerprint_rows(values(),table=table),'first_rowid':bounds[0],'last_rowid':bounds[1]}


def build_dictionary_copy(source,destination,*,batch_size=5000,progress=None,deadline=None):
    """Copy and fully compare every logical row, including hidden rowids.

    Source connection ownership stays with the caller. query_only and the held
    SQL transaction are necessary checks, not proof of external writer freeze.
    The sole extra logical data is the separate dictionary-format marker.
    """
    if type(batch_size) is not int or not 1<=batch_size<=50000:raise ValueError('invalid_batch_size')
    started=time.monotonic()
    if deadline is None:deadline=started+7200
    if type(deadline) not in (int,float) or not math.isfinite(deadline) or not started<deadline<=started+7200:
        raise ValueError('invalid_copy_deadline')
    def check():
        if time.monotonic()>=deadline:raise TimeoutError('copy_deadline')
    target=None
    source.set_progress_handler(lambda:int(time.monotonic()>=deadline),1000)
    try:
        source.execute('PRAGMA busy_timeout=1000')
        check()
        schema=validate_source(source)
        destination=Path(destination).resolve()
        if any(row[2] and Path(row[2]).resolve()==destination for row in source.execute('PRAGMA database_list')):
            raise ValueError('destination_is_source')
        # No overwrite, cleanup, or source mutation on failure.
        check()
        descriptor=os.open(destination,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        os.close(descriptor)
        target=sqlite3.connect(destination)
        target.set_progress_handler(lambda:int(time.monotonic()>=deadline),1000)
        target.execute('PRAGMA busy_timeout=1000')
        check()
        if [tuple(r) for r in source.execute('PRAGMA integrity_check')]!=[('ok',)]:
            raise ValueError('source_integrity_failed')
        target.execute('PRAGMA journal_mode=DELETE')
        target.execute('PRAGMA synchronous=FULL')
        target.execute('PRAGMA foreign_keys=ON')
        tables=sorted(name for name,shape in schema.items() if shape[0]=='table')
        for name in tables:
            if name not in compact.PUBLIC:target.execute(schema[name][2])
        target.commit()
        compact.initialize_dictionary_schema(target)
        writer=compact.DictionaryStorage(target)
        source_proofs={}
        writes={'snapshots':writer.snapshot,'decisions':writer.decision,'learning_predictions':writer.prediction}
        for table in tables:
            columns=[row[1] for row in source.execute('PRAGMA table_info('+quote(table)+')')]
            insert='INSERT INTO '+quote(table)+'(rowid,'+','.join(quote(c) for c in columns)+') VALUES('
            insert+=','.join('?' for _ in range(len(columns)+1))+')'
            completed=0
            def copied_rows():
                nonlocal completed
                for row in _rows(source,table):
                    check()
                    row=tuple(row)
                    if table in writes:
                        if writes[table](row[1:])!=row[0]:raise ValueError('normalized_row_not_preserved')
                    else:target.execute(insert,row)
                    completed+=1
                    if completed%batch_size==0:
                        target.commit()
                        if progress is not None:progress({'phase':'COPY','table':table,'rows':completed})
                    yield row
            source_proofs[table]=_proof(copied_rows(),table,check)
            target.commit()
            if progress is not None:progress({'phase':'COPIED','table':table,'rows':completed})
        # Preserve the exact remaining index/trigger contracts, built after copy.
        for name,shape in schema.items():
            if shape[0] in ('index','trigger') and shape[1] not in compact.PUBLIC:
                target.execute(shape[2])
        target.commit()
        compact.validate_store_schema(target,SCHEMA)
        compact.validate_dictionary_contents(target,check=check)
        target_proofs={table:_proof(_rows(target,table,dictionary=True),table,check) for table in tables}
        if target_proofs!=source_proofs:raise ValueError('source_target_row_mismatch')
        source_refs=semantic_references(source);target_refs=semantic_references(target)
        if source_refs!=target_refs:raise ValueError('source_target_reference_mismatch')
        integrity=target.execute('PRAGMA integrity_check').fetchall()
        foreign_keys=target.execute('PRAGMA foreign_key_check').fetchall()
        if integrity!=[('ok',)] or foreign_keys:raise ValueError('target_integrity_failed')
        check()
        report={'schema_version':1,'format':compact.FORMAT,'complete':True,
            'source_row_proofs':source_proofs,'destination_row_proofs':target_proofs,
            'semantic_reference_counts':source_refs,'source_integrity':True,
            'integrity':True,'foreign_keys':True,'source_modified':False,
            'operational_freeze_verified':False,'raw_backup_verified':False,
            'eligible_for_publication':False,'requires_controller_review':True,
            'target_bytes':destination.stat().st_size}
        if progress is not None:progress({'phase':'VERIFIED','tables':len(tables)})
        check()
        return report
    except Exception:
        if target is not None:
            target.set_progress_handler(None,0)
            target.rollback()
        raise
    finally:
        source.set_progress_handler(None,0)
        if target is not None:target.close()
