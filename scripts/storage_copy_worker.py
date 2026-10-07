"""Owned offline copy worker. Never replaces or repairs the original database.

The controller must hold its source guards/fence before launch, observe the
native Job member before ACK, and take RAW/WORK guards at both handoffs.
"""
import argparse
from contextlib import ExitStack
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import time
import traceback

sys.path.insert(0,str(Path(__file__).resolve().parent))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'CORE'))
import db_offline_probe as reports
import offline_ledger
import windows_read_guard as native
from offline_ack_read import read_ack_bytes

from mykonos_meme_scout.storage_copy import build_dictionary_copy


def publish(report,path,deadline):
    """Retry only publication of this already-fsynced private report generation."""
    try:
        reports.save(report,path)
        if time.monotonic()>=deadline:raise TimeoutError('copy_report_deadline')
        return
    except PermissionError as error:
        if getattr(error,'winerror',None) not in (5,32):raise
    temporary=path.with_name(path.name+'.'+report['nonce']+'.tmp')
    expected=json.dumps(report,sort_keys=True,indent=2,allow_nan=False).encode('utf-8')
    with temporary.open('rb') as stream:actual=stream.read(131073)
    if actual!=expected:raise ValueError('copy_pending_report_mismatch')
    limit=min(deadline,time.monotonic()+1)
    while time.monotonic()<limit:
        try:
            os.replace(temporary,path)
            if time.monotonic()>=deadline:raise TimeoutError('copy_report_deadline')
            return
        except PermissionError as error:
            if getattr(error,'winerror',None) not in (5,32):raise
        time.sleep(.01)
    raise TimeoutError('copy_report_publication_timeout')


def ack(path,nonce,pid,deadline):
    limit=min(deadline,time.monotonic()+5)
    while not Path(path).exists():
        if time.monotonic()>=limit:raise TimeoutError('copy_ack_missing')
        time.sleep(.01)
    body=read_ack_bytes(path,deadline=limit)
    if len(body)>1024:raise ValueError('copy_ack_size')
    value=offline_ledger.decode(body.decode('utf-8'))
    if set(value)!={'nonce','pid'} or value['nonce']!=nonce or type(value['pid']) is not int or value['pid']!=pid:
        raise ValueError('copy_ack_identity')
    if time.monotonic()>=limit:raise TimeoutError('copy_ack_deadline')


def protocol_paths(output,ack_paths):
    paths=[Path(path).resolve() for path in (output,*ack_paths)]
    if len(set(paths))!=4 or any(path.exists() for path in paths[1:]):
        raise ValueError('copy_ack_paths_not_fresh_and_distinct')


def copy_bytes(source,target,deadline):
    digest=hashlib.sha256()
    with source.open('rb') as src,target.open('xb') as dst:
        for chunk in iter(lambda:src.read(1048576),b''):
            if time.monotonic()>=deadline:raise TimeoutError('copy_deadline')
            digest.update(chunk);dst.write(chunk)
        dst.flush();os.fsync(dst.fileno())
    if offline_ledger.hash_file(target,deadline)!=digest.hexdigest():raise ValueError('raw_copy_mismatch')
    return digest.hexdigest()


def frozen_copy(database,scope,ledger,gate,report,output,raw_ack,end_ack,deadline):
    # Only reached after the initial native-PID ACK. This does not independently
    # prove the controller's freeze; its operative record supplies that evidence.
    report['safety_before']=offline_ledger.passive_safety(ledger,gate,deadline=deadline)
    before=reports.state(database)
    if Path(str(database)+'-journal').exists():raise ValueError('main_journal_requires_separate_review')
    paths=[database]+([Path(str(database)+'-wal')] if before['wal'] is not None else [])
    required=3*before['main']['bytes']+2*(before['wal']['bytes'] if before['wal'] else 0)+268435456
    if shutil.disk_usage(scope).free<required:raise ValueError('copy_disk_reserve')
    raw=scope/'raw';work=scope/'work'
    with ExitStack() as stack:
        stack.enter_context(native.ReadFiles(paths,deadline=deadline))
        if reports.state(database)!=before:raise ValueError('source_identity_changed')
        raw.mkdir(exist_ok=False)
        hashes={}
        for key,path in [('main',database),('wal',Path(str(database)+'-wal'))]:
            if key=='wal' and before['wal'] is None:hashes[key]=None;continue
            report.update(stage='RAW_COPY',copy_member=key)
            publish(report,output,deadline)
            hashes[key]=copy_bytes(path,raw/path.name,deadline)
        raw_database=raw/database.name
        # Guard byte-identical RAW members before SQLite opens them. A missing
        # input WAL is represented by an explicitly empty RAW-only helper.
        # Precreate and guard it so SQLite cannot retain a write handle.
        raw_paths=[raw/path.name for path in paths]
        raw_wal=Path(str(raw_database)+'-wal')
        empty_created=before['wal'] is None
        if empty_created:
            with raw_wal.open('xb') as stream:stream.flush();os.fsync(stream.fileno())
            raw_paths.append(raw_wal)
        stack.enter_context(native.ReadFiles(raw_paths,deadline=deadline))
        connection=sqlite3.connect(raw_database.as_uri()+'?mode=ro',uri=True,timeout=1)
        try:
            connection.execute('PRAGMA query_only=ON');connection.execute('PRAGMA busy_timeout=1000')
            connection.set_progress_handler(lambda:int(time.monotonic()>=deadline),1000)
            connection.execute('BEGIN');connection.execute('SELECT count(*) FROM sqlite_schema').fetchone()
            if empty_created:
                if raw_wal.stat().st_size!=0:raise ValueError('unexpected_raw_wal')
            report.update(stage='RAW_READY',source_before=before,source_sha256=hashes,
                raw_before=reports.state(raw_database),raw_empty_wal_created=empty_created)
            publish(report,output,deadline)
            # Parent takes its own RAW handles before WORK is touched.
            ack(raw_ack,report['nonce'],report['pid'],deadline)
            work.mkdir(exist_ok=False)
            def progress(value):
                report.update(stage='BUILD',builder_progress=value)
                publish(report,output,deadline)
            result=build_dictionary_copy(connection,work/database.name,progress=progress,deadline=deadline)
        finally:connection.close()
        target=work/database.name
        stack.enter_context(native.ReadFiles([target],deadline=deadline))
        current=reports.state(database)
        source_hashes={key:offline_ledger.hash_file(path,deadline) if key=='main' or before['wal'] is not None else None
            for key,path in [('main',database),('wal',Path(str(database)+'-wal'))]}
        raw_hashes={key:offline_ledger.hash_file(raw/path.name,deadline) if key=='main' or before['wal'] is not None else None
            for key,path in [('main',database),('wal',Path(str(database)+'-wal'))]}
        if current!=before or source_hashes!=hashes or raw_hashes!=hashes:
            raise ValueError('source_or_raw_changed')
        if reports.state(raw_database)!=report['raw_before']:raise ValueError('raw_identity_changed')
        report.update(stage='WORK_READY',builder=result,source_after=current,raw_after=reports.state(raw_database),
            target=reports.state(target),target_sha256=offline_ledger.hash_file(target,deadline),
            safety_after=offline_ledger.passive_safety(ledger,gate,deadline=deadline))
        publish(report,output,deadline)
        # Parent guards verified WORK before this worker releases any protection.
        ack(end_ack,report['nonce'],report['pid'],deadline)
    if native._RETAINED_GUARDS:raise ValueError('copy_native_cleanup_unconfirmed')


def main():
    parser=argparse.ArgumentParser()
    for name in ('database','scope','ledger','gate','output','nonce','ack','raw-ack','end-ack'):
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--seconds',type=float,default=7200)
    args=parser.parse_args()
    if not re.fullmatch('[a-f0-9]{32}',args.nonce) or not math.isfinite(args.seconds) or not 5<=args.seconds<=7200:
        return 2
    deadline=time.monotonic()+args.seconds
    report={'schema_version':1,'nonce':args.nonce,'pid':os.getpid(),'phase':'RUNNING','stage':'AWAIT_NATIVE_ACK',
        'complete':False,'cleanup_confirmed':False,'budget_seconds':args.seconds}
    output=Path(args.output).resolve()
    try:
        protocol_paths(output,(args.ack,args.raw_ack,args.end_ack))
        publish(report,output,deadline)
        ack(args.ack,args.nonce,report['pid'],deadline)
        frozen_copy(Path(args.database).resolve(strict=True),Path(args.scope).resolve(strict=True),
            args.ledger,args.gate,report,output,args.raw_ack,args.end_ack,deadline)
        if time.monotonic()>=deadline:raise TimeoutError('copy_deadline')
        report.update(phase='FINAL',stage='FINAL',complete=True,cleanup_confirmed=True)
        publish(report,output,deadline)
        return 0
    except Exception as error:
        report.update(phase='FINAL',complete=False,cleanup_confirmed=False,code='COPY_PROOF_FAILED')
        allowed={'ValueError','PermissionError','TimeoutError','OperationalError','FileExistsError','RuntimeError','OSError'}
        report['exception_kind']=type(error).__name__ if type(error).__name__ in allowed else 'UNKNOWN'
        if type(getattr(error,'winerror',None))is int:report['native_error']=error.winerror
        known={'storage_copy_worker.py','storage_copy.py','storage_codec.py','offline_ledger.py','windows_read_guard.py'}
        for frame in traceback.extract_tb(error.__traceback__):
            if Path(frame.filename).name in known:
                report['failure_location']={'file':Path(frame.filename).name,'line':frame.lineno}
        try:reports.save(report,output)
        except Exception:
            try:reports.save(report,output.with_suffix('.failure.json'))
            except Exception:pass
        return 2


if __name__=='__main__':raise SystemExit(main())
