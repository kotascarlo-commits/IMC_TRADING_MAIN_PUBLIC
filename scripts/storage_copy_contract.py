"""Independent read-only completion contract; never publishes WORK or restarts.

Caller holds original/RAW/WORK guards and proves owned native Job shutdown.
Report equality alone is insufficient: bytes and all logical rows are reread.
"""
import argparse
from contextlib import ExitStack,closing
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parent))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'CORE'))
import db_offline_probe as reports
import offline_ledger
import windows_read_guard as native
from storage_copy_worker import ack,publish
from offline_worker_diagnostics import evidence
from mykonos_meme_scout import compact_storage,storage_copy
from mykonos_meme_scout.store import SCHEMA

MAX_VERIFICATION_SECONDS=21600


def read_report(path):
    with Path(path).open('rb') as stream:body=stream.read(131073)
    if len(body)>131072:raise ValueError('copy_report_size')
    return offline_ledger.decode(body.decode('utf-8'))


def metadata_shape(value):
    if not isinstance(value,dict) or set(value)!={'main','wal'}:raise ValueError('copy_file_state_shape')
    for key,member in value.items():
        if member is None:
            if key!='wal':raise ValueError('copy_missing_main')
            continue
        if (not isinstance(member,dict) or set(member)!={'device','file_id','bytes','mtime_ns','ctime_ns'}
                or any(type(v) is not int or v<0 for v in member.values())):
            raise ValueError('copy_file_metadata')


def proof_shape(proofs,tables):
    if not isinstance(proofs,dict) or set(proofs)!=set(tables):raise ValueError('copy_proof_inventory')
    for value in proofs.values():
        if not isinstance(value,dict) or set(value)!={'rows','sha256','first_rowid','last_rowid'}:
            raise ValueError('copy_row_proof_shape')
        if type(value['rows']) is not int or value['rows']<0 or not re.fullmatch('[a-f0-9]{64}',str(value['sha256'])):
            raise ValueError('copy_row_proof_values')
        bounds=(value['first_rowid'],value['last_rowid'])
        if value['rows']==0:
            if bounds!=(None,None):raise ValueError('copy_empty_bounds')
        elif any(type(v) is not int or not -(2**63)<=v<2**63 for v in bounds) or bounds[0]>bounds[1]:
            raise ValueError('copy_row_bounds')


def readonly(path,deadline):
    connection=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=1)
    try:
        connection.set_progress_handler(lambda:int(time.monotonic()>=deadline),1000)
        connection.execute('PRAGMA busy_timeout=1000');connection.execute('PRAGMA query_only=ON')
        connection.execute('BEGIN');connection.execute('SELECT count(*) FROM sqlite_schema').fetchone()
        return connection
    except Exception:connection.close();raise


def prove_retained_candidate(failed,*,source,scope,old_nonce,nonce,pid,ledger,gate,
                             deadline,progress=lambda value:None):
    """Re-prove an existing candidate read-only; never resume writes or repair.

    Owning controller must retain original/RAW/WORK guards and ACTIVE, bind the
    failed report to the native old producer, and observe this new native PID.
    The returned proof alone grants no publication, restart or freeze authority.
    """
    def check():
        if time.monotonic()>=deadline:raise TimeoutError('retained_copy_deadline')
    if (type(deadline)not in (int,float) or not math.isfinite(deadline)
            or not time.monotonic()<deadline<=time.monotonic()+MAX_VERIFICATION_SECONDS):
        raise ValueError('retained_copy_budget')
    if (type(failed)is not dict or type(failed.get('schema_version'))is not int
            or failed['schema_version']!=1 or failed.get('nonce')!=old_nonce
            or type(old_nonce)is not str or re.fullmatch('[a-f0-9]{32}',old_nonce)is None
            or type(nonce)is not str or re.fullmatch('[a-f0-9]{32}',nonce)is None or nonce==old_nonce
            or type(pid)is not int or pid<=0 or type(failed.get('pid'))is not int or failed['pid']<=0
            or failed.get('phase')!='FINAL' or failed.get('stage')!='BUILD'
            or failed.get('complete')is not False or failed.get('cleanup_confirmed')is not False
            or failed.get('code')!='COPY_PROOF_FAILED' or failed.get('exception_kind')!='TimeoutError'):
        raise ValueError('retained_copy_origin_invalid')
    source=Path(source).resolve(strict=True);scope=Path(scope).resolve(strict=True)
    raw=scope/'raw'/source.name;target=scope/'work'/source.name
    for key in ('source_before','raw_before'):metadata_shape(failed[key])
    if reports.state(source)!=failed['source_before'] or reports.state(raw)!=failed['raw_before']:
        raise ValueError('retained_copy_origin_changed')
    hashes=failed['source_sha256']
    if type(hashes)is not dict or set(hashes)!={'main','wal'}:
        raise ValueError('retained_copy_hash_shape')
    paths=[]
    for database in (source,raw,target):
        if Path(str(database)+'-journal').exists():raise ValueError('retained_copy_journal_present')
        paths.append(database)
        wal=Path(str(database)+'-wal')
        if wal.exists():paths.append(wal)
    target_before=reports.state(target)
    if target_before['wal']is not None:raise ValueError('retained_copy_work_wal_present')
    with native.ReadFiles(paths,deadline=deadline),ExitStack() as stack:
        safety=offline_ledger.passive_safety(ledger,gate,deadline=deadline)
        for key in ('main','wal'):
            check()
            suffix='' if key=='main' else '-wal'
            if key=='wal' and failed['source_before'][key]is None:
                if (hashes[key]is not None or failed.get('raw_empty_wal_created')is not True
                        or failed['raw_before']['wal']is None or failed['raw_before']['wal']['bytes']!=0):
                    raise ValueError('retained_copy_absent_wal_mismatch')
            else:
                if type(hashes[key])is not str or re.fullmatch('[a-f0-9]{64}',hashes[key])is None:
                    raise ValueError('retained_copy_hash_value')
                for database in (source,raw):
                    if offline_ledger.hash_file(Path(str(database)+suffix),deadline)!=hashes[key]:
                        raise ValueError('retained_copy_source_raw_hash_mismatch')
        original=stack.enter_context(closing(readonly(raw,deadline)))
        copied=stack.enter_context(closing(readonly(target,deadline)))
        storage_copy.validate_source(original)
        compact_storage.validate_store_schema(copied,SCHEMA)
        progress({'stage':'SOURCE_INTEGRITY'})
        if original.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:
            raise ValueError('retained_copy_source_integrity_failed')
        progress({'stage':'DICTIONARY_CONTENTS'})
        dictionary_counts=compact_storage.validate_dictionary_contents(copied,check=check)
        tables=sorted(name for name,shape in storage_copy.legacy_schema().items() if shape[0]=='table')
        proofs=[]
        for connection,is_dictionary in ((original,False),(copied,True)):
            rows={}
            for table in tables:
                progress({'stage':'TARGET_ROWS' if is_dictionary else 'SOURCE_ROWS','table':table})
                rows[table]=storage_copy._proof(storage_copy._rows(connection,table,dictionary=is_dictionary),table,check)
            proofs.append(rows)
        if proofs[0]!=proofs[1]:raise ValueError('retained_copy_rows_mismatch')
        progress({'stage':'REFERENCES'})
        references=storage_copy.semantic_references(original)
        if references!=storage_copy.semantic_references(copied):raise ValueError('retained_copy_references_mismatch')
        progress({'stage':'TARGET_INTEGRITY'})
        if copied.execute('PRAGMA integrity_check').fetchall()!=[('ok',)] or copied.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('retained_copy_target_integrity_failed')
        target_hash=offline_ledger.hash_file(target,deadline)
        after=offline_ledger.passive_safety(ledger,gate,deadline=deadline)
        if after!=safety:raise ValueError('retained_copy_safety_changed')
        if (reports.state(source)!=failed['source_before'] or reports.state(raw)!=failed['raw_before']
                or reports.state(target)!=target_before):raise ValueError('retained_copy_files_changed')
        check()
        return {'kind':'RETAINED_READONLY_PROOF','old_copy_nonce':old_nonce,
            'source_before':failed['source_before'],'source_after':reports.state(source),
            'raw_before':failed['raw_before'],'raw_after':reports.state(raw),'target':target_before,
            'source_sha256':hashes,'target_sha256':target_hash,
            'raw_empty_wal_created':failed['raw_empty_wal_created'],'safety_before':safety,'safety_after':after,
            'builder':{'schema_version':1,'format':compact_storage.FORMAT,'complete':True,
                'requires_controller_review':True,'target_bytes':target_before['main']['bytes'],
                'source_integrity':True,'integrity':True,'foreign_keys':True,
                'source_modified':False,'operational_freeze_verified':False,'raw_backup_verified':False,
                'eligible_for_publication':False,'source_row_proofs':proofs[0],
                'destination_row_proofs':proofs[1],'semantic_reference_counts':references,
                'physical_dictionary_counts':dictionary_counts}}


def validate_final(report,*,source,scope,nonce,worker_pid,exit_code,deadline):
    def check():
        if time.monotonic()>=deadline:raise TimeoutError('copy_validation_deadline')
    if type(deadline) not in (int,float) or not math.isfinite(deadline) or not time.monotonic()<deadline<=time.monotonic()+MAX_VERIFICATION_SECONDS:
        raise ValueError('copy_validation_budget')
    if (not isinstance(report,dict) or type(report.get('schema_version'))is not int or report['schema_version']!=1
            or report.get('nonce')!=nonce or not re.fullmatch('[a-f0-9]{32}',nonce)
            or type(worker_pid)is not int or worker_pid<=0 or type(report.get('pid'))is not int or report['pid']!=worker_pid
            or type(exit_code)is not int or exit_code!=0 or report.get('phase')!='FINAL' or report.get('stage')!='FINAL'
            or report.get('complete')is not True or report.get('cleanup_confirmed')is not True or 'code' in report):
        raise ValueError('copy_final_identity')
    budget=report.get('budget_seconds')
    if type(budget) not in (int,float) or not math.isfinite(budget) or not 5<=budget<=MAX_VERIFICATION_SECONDS:
        raise ValueError('copy_report_budget')
    for key in ('safety_before','safety_after'):
        safety=report[key]
        if (not isinstance(safety,dict) or set(safety)!={'gate','schema_version','positions','uncertain','receipts'}
                or safety['gate']!='OFF' or type(safety['schema_version'])is not int or safety['schema_version']!=2
                or any(type(safety[name])is not int or safety[name]!=0 for name in ('positions','uncertain','receipts'))):
            raise ValueError('copy_safety_mismatch')
    source=Path(source).resolve(strict=True);scope=Path(scope).resolve(strict=True)
    raw=scope/'raw'/source.name;target=scope/'work'/source.name
    for key in ('source_before','source_after','raw_before','raw_after','target'):metadata_shape(report[key])
    if (report['source_before']!=report['source_after'] or reports.state(source)!=report['source_before']
            or report['raw_before']!=report['raw_after'] or reports.state(raw)!=report['raw_after']
            or reports.state(target)!=report['target'] or report['target']['wal']is not None):
        raise ValueError('copy_final_file_mismatch')
    if any(Path(str(path)+'-journal').exists() for path in (source,raw,target)):
        raise ValueError('copy_unexpected_journal')
    hashes=report['source_sha256']
    if not isinstance(hashes,dict) or set(hashes)!={'main','wal'}:raise ValueError('copy_hash_shape')
    before=report['source_before']
    if type(report.get('raw_empty_wal_created'))is not bool or report['raw_empty_wal_created']is not (before['wal']is None):
        raise ValueError('copy_raw_wal_marker')
    for key in ('main','wal'):
        suffix='' if key=='main' else '-wal'
        if key=='wal' and before[key]is None:
            if hashes[key]is not None or report['raw_after']['wal']is None or report['raw_after']['wal']['bytes']!=0:
                raise ValueError('copy_absent_wal_mismatch')
            continue
        if type(hashes[key])is not str or not re.fullmatch('[a-f0-9]{64}',hashes[key]):raise ValueError('copy_hash_value')
        if any(offline_ledger.hash_file(Path(str(path)+suffix),deadline)!=hashes[key] for path in (source,raw)):
            raise ValueError('copy_source_raw_hash_mismatch')
    if (type(report.get('target_sha256'))is not str or not re.fullmatch('[a-f0-9]{64}',report['target_sha256'])
            or offline_ledger.hash_file(target,deadline)!=report['target_sha256']):raise ValueError('copy_target_hash_mismatch')
    builder=report['builder']
    if (not isinstance(builder,dict) or type(builder.get('schema_version'))is not int or builder['schema_version']!=1
            or builder.get('format')!=compact_storage.FORMAT or builder.get('requires_controller_review')is not True
            or type(builder.get('target_bytes'))is not int or builder['target_bytes']!=report['target']['main']['bytes']):
        raise ValueError('copy_builder_contract')
    for key in ('complete','source_integrity','integrity','foreign_keys'):
        if builder.get(key)is not True:raise ValueError('copy_builder_incomplete')
    for key in ('source_modified','operational_freeze_verified','raw_backup_verified','eligible_for_publication'):
        if builder.get(key)is not False:raise ValueError('copy_builder_scope_mismatch')
    tables=sorted(name for name,shape in storage_copy.legacy_schema().items() if shape[0]=='table')
    for key in ('source_row_proofs','destination_row_proofs'):proof_shape(builder[key],tables)
    with ExitStack() as stack:
        original=stack.enter_context(closing(readonly(raw,deadline)))
        copied=stack.enter_context(closing(readonly(target,deadline)))
        storage_copy.validate_source(original)
        compact_storage.validate_store_schema(copied,SCHEMA)
        physical_counts=compact_storage.validate_dictionary_contents(copied,check=check)
        if report.get('kind')=='RETAINED_READONLY_PROOF':
            old_nonce=report.get('old_copy_nonce')
            if (type(old_nonce)is not str or re.fullmatch('[a-f0-9]{32}',old_nonce)is None or old_nonce==nonce
                    or type(builder.get('physical_dictionary_counts'))is not dict
                    or any(type(v)is not int for v in builder['physical_dictionary_counts'].values())
                    or builder['physical_dictionary_counts']!=physical_counts):
                raise ValueError('copy_retained_provenance_mismatch')
        a={table:storage_copy._proof(storage_copy._rows(original,table),table,check) for table in tables}
        b={table:storage_copy._proof(storage_copy._rows(copied,table,dictionary=True),table,check) for table in tables}
        if a!=builder['source_row_proofs'] or b!=builder['destination_row_proofs'] or a!=b:
            raise ValueError('copy_independent_row_mismatch')
        references=storage_copy.semantic_references(original)
        if references!=storage_copy.semantic_references(copied) or references!=builder['semantic_reference_counts']:
            raise ValueError('copy_independent_reference_mismatch')
        if copied.execute('PRAGMA integrity_check').fetchall()!=[('ok',)] or copied.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('copy_independent_integrity_failed')
    check()
    if reports.state(source)!=before or reports.state(raw)!=report['raw_after'] or reports.state(target)!=report['target']:
        raise ValueError('copy_final_files_changed')
    return {'complete':True,'independent_comparison':True,'logical_tables':len(tables),
        'source_unchanged':True,'raw_verified':True,'target_verified':True,'eligible_for_publication':False,
        'semantic_orphans':sum(references.values())}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--operation',choices=('validate','prove-retained'),default='validate')
    for key in ('old-nonce','ledger','gate','root'):parser.add_argument('--'+key)
    for key in ('report','source','scope','nonce','ack','output'):parser.add_argument('--'+key,required=True)
    parser.add_argument('--worker-pid',type=int,required=True);parser.add_argument('--exit-code',type=int,required=True)
    parser.add_argument('--seconds',type=float,required=True)
    args=parser.parse_args()
    if not math.isfinite(args.seconds) or not 0<args.seconds<=MAX_VERIFICATION_SECONDS:return 2
    if re.fullmatch('[a-f0-9]{32}',args.nonce)is None:return 2
    if args.operation=='prove-retained' and (args.seconds<5 or args.old_nonce is None
            or re.fullmatch('[a-f0-9]{32}',args.old_nonce)is None or args.old_nonce==args.nonce):return 2
    output=Path(args.output).resolve();deadline=time.monotonic()+args.seconds
    value={'schema_version':1,'nonce':args.nonce,'pid':os.getpid(),'phase':'RUNNING','complete':False,'cleanup_confirmed':False}
    may_publish=False
    failure_stage='PROTOCOL'
    try:
        source_path=Path(args.source).resolve();scope_path=Path(args.scope).resolve()
        protected={Path(args.report).resolve()}
        for database in (source_path,scope_path/'raw'/source_path.name,scope_path/'work'/source_path.name):
            protected.update(Path(str(database)+suffix) for suffix in ('','-wal','-shm','-journal'))
        if output in protected or Path(args.ack).resolve() in protected or output==Path(args.ack).resolve():
            raise ValueError('copy_protocol_protected_path')
        if args.operation=='prove-retained':
            root=Path(args.root).resolve(strict=True)
            scope=Path(args.scope).resolve(strict=True)
            source=Path(args.source).resolve(strict=True)
            expected=root/'.local-tools'/('offline-audit-'+str(args.old_nonce))
            source.relative_to(root)
            if (scope!=expected or output.parent!=root/'.local-tools'/('retained-repair-'+args.nonce)
                    or output.name!='copy-proof.json' or output.exists()
                    or Path(args.ack).resolve()!=output.parent/'copy-proof-seen.json'
                    or Path(args.ack).exists() or args.exit_code!=2):
                raise ValueError('retained_copy_protocol_paths')
            failed=read_report(args.report)
            if failed.get('pid')!=args.worker_pid:raise ValueError('retained_copy_old_producer')
            may_publish=True
            value.update(stage='AWAIT_NATIVE_ACK',budget_seconds=args.seconds)
            failure_stage='FINAL_PUBLISH'
            publish(value,output,deadline)
            failure_stage='WAIT_ACK'
            ack(args.ack,args.nonce,value['pid'],deadline)
            def progress(change):
                nonlocal failure_stage
                value.update(change)
                failure_stage='FINAL_PUBLISH'
                publish(value,output,deadline)
                failure_stage='PROVE'
            failure_stage='PROVE'
            result=prove_retained_candidate(failed,source=source,scope=scope,old_nonce=args.old_nonce,
                nonce=args.nonce,pid=value['pid'],ledger=args.ledger,gate=args.gate,
                deadline=deadline,progress=progress)
            value.update(result,phase='FINAL',stage='FINAL',complete=True,cleanup_confirmed=True)
            failure_stage='FINAL_PUBLISH'
            publish(value,output,deadline)
            return 0
        may_publish=True
        failure_stage='FINAL_PUBLISH'
        publish(value,output,deadline)
        failure_stage='WAIT_ACK'
        ack(args.ack,args.nonce,value['pid'],deadline)
        failure_stage='VALIDATE'
        result=validate_final(read_report(args.report),source=args.source,scope=args.scope,nonce=args.nonce,
            worker_pid=args.worker_pid,exit_code=args.exit_code,deadline=deadline)
        value.update(phase='FINAL',complete=True,cleanup_confirmed=True,result=result)
        failure_stage='FINAL_PUBLISH'
        publish(value,output,deadline);return 0
    except Exception as error:
        value.update(phase='FINAL',complete=False,cleanup_confirmed=False,code='COPY_VALIDATION_FAILED')
        value.update(evidence(error,failure_stage))
        if may_publish:
            try:reports.save(value,output)
            except Exception:pass
        return 2


if __name__=='__main__':raise SystemExit(main())
