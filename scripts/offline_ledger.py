"""Quiesced execution-ledger checks and narrowly scoped SQLite recovery.

Recovery callers must hold the global maintenance fence, persistent ACTIVE
state and RuntimeMutex, and exclude/recheck writers. No application DML,
DDL, constructor, order or checkpoint is executed. Only SQLite's normal
hot-journal rollback may change the original. Noncooperating writers are
outside the caller's mutex guarantee; every mismatch blocks restart.
"""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import struct
import sys
import time
from contextlib import ExitStack
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent))
import windows_read_guard as native

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'RUNTIME/EXECUTOR_GATEWAY/GMGN_BRIDGE'))
from execution_policy import owner_gate
from execution_state import Ledger,raw
import execution_machine as machine
from mykonos_meme_scout.execution_config import load_execution_config

MAGIC=bytes.fromhex('d9d505f920a163d7')
MAX_FILE=64*1024*1024


def unique(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('duplicate_field')
        result[key]=value
    return result


def decode(text):
    return json.loads(text,object_pairs_hook=unique,
        parse_constant=lambda _:(_ for _ in ()).throw(ValueError('invalid_constant')))


def gate_off(gate):
    if owner_gate(gate)!={'value':'OFF','valid':True}:raise ValueError('gate_not_off')
    load_execution_config()


def connect(path, *, writable=False, deadline=None):
    c=sqlite3.connect(Path(path).resolve(strict=True).as_uri()+('?mode=rw' if writable else '?mode=ro'),uri=True,timeout=1)
    try:
        c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
        if deadline is not None:c.set_progress_handler(lambda:int(time.monotonic()>=deadline),1000)
        c.execute('SELECT COUNT(1) FROM sqlite_schema').fetchone()
        return c
    except BaseException:c.close();raise


def passive_safety(path, gate, *, deadline=None):
    gate_off(gate)
    c=connect(path,deadline=deadline)
    try:
        version=decode(c.execute('SELECT value FROM meta WHERE key=?',('schema_version',)).fetchone()[0])
        if type(version)is not int or version!=2:raise ValueError('invalid_schema')
        trades={}
        for key,text in c.execute('SELECT id,doc FROM trades'):
            stored=decode(text)
            if not isinstance(stored,dict) or 'state' in stored:raise ValueError('persisted_legacy_state')
            t=Ledger.validate('trades',machine.read(text))
            if t['trade_id']!=key or key in trades:raise ValueError('invalid_trade_identity')
            trades[key]=t
        for t in trades.values():
            if not isinstance(t['remaining_raw'],str) or not re.fullmatch('[0-9]+',t['remaining_raw']):raise ValueError('invalid_quantity')
            if t['execution_state'] not in {'CANDIDATE','RISK_CHECK','READY','REJECTED'}:raise ValueError('trade_not_passive')
            if any(raw(t[k],signed=k=='wallet_delta_lamports')!=0 for k in ('buy_raw','sold_raw','remaining_raw','fees_lamports','wallet_delta_lamports')):raise ValueError('trade_not_empty')
            if t['terminal_unreconciled'] or t['exit_requested'] or t['unknown_reason']:raise ValueError('trade_not_passive')
        positions=sum(int(t['remaining_raw'])>0 for t in trades.values())
        events={}
        for key,text in c.execute('SELECT id,doc FROM events'):
            e=Ledger.validate('events',decode(text))
            if e['event_id']!=key or key in events:raise ValueError('invalid_event_identity')
            t=trades.get(e['trade_id'])
            if not t or e['mint']!=t['mint'] or e['wallet']!=t['wallet'] or e['side']!='BUY':raise ValueError('invalid_event_binding')
            if any(e.get(k) is not None for k in ('sent_at','order_id','signature','actual_input_raw')):raise ValueError('event_was_sent')
            if raw(e['filled_input_raw']) or raw(e['filled_output_raw']) or e['execution'] is not False:raise ValueError('event_not_empty')
            if e['state']=='READY' and (t['execution_state']!='READY' or t['execution_event_id']!=key or t['execution_side']!=e['side']):raise ValueError('invalid_ready_event')
            events[key]=e
        for t in trades.values():
            active=events.get(t['execution_event_id'])
            if t['execution_event_id'] is not None and (not active or active['trade_id']!=t['trade_id'] or t['execution_side']!=active['side']):raise ValueError('invalid_active_event')
            if t['execution_event_id'] is None and t['execution_side'] is not None:raise ValueError('invalid_active_event')
            if t['execution_state']=='READY' and (not active or active['state']!='READY'):raise ValueError('invalid_ready_event')
            if t['execution_state'] in {'CANDIDATE','RISK_CHECK'} and any(e['trade_id']==t['trade_id'] for e in events.values()):raise ValueError('invalid_candidate_event')
        uncertain=sum(e['state'] not in {'READY','REJECTED'} for e in events.values())
        receipts=c.execute('SELECT COUNT(1) FROM receipts').fetchone()[0]
        if positions!=0 or uncertain!=0 or receipts!=0:raise ValueError('execution_not_empty')
        return {'gate':'OFF','schema_version':version,'positions':positions,'uncertain':uncertain,'receipts':receipts}
    finally:c.close()


def metadata(path):
    s=Path(path).stat()
    return {'device':s.st_dev,'file_id':s.st_ino,'bytes':s.st_size,'mtime_ns':s.st_mtime_ns,'ctime_ns':s.st_ctime_ns}


def hash_file(path,deadline):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1048576),b''):
            if time.monotonic()>=deadline:raise TimeoutError('recovery_deadline')
            digest.update(chunk)
    return digest.hexdigest()


def checked_database(path,gate,deadline):
    c=connect(path,deadline=deadline)
    try:
        if c.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:raise ValueError('ledger_integrity_failed')
        digest=hashlib.sha256()
        for table in ('sqlite_schema','meta','trades','events','feedback','receipts','audit'):
            order='type,name' if table=='sqlite_schema' else 'rowid'
            digest.update(table.encode()+b'\0')
            for row in c.execute('SELECT * FROM '+table+' ORDER BY '+order):
                encoded=json.dumps(list(row),ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf-8')
                digest.update(len(encoded).to_bytes(8,'big')+encoded)
        logical=digest.hexdigest()
    finally:c.close()
    return {'integrity':True,'safety':passive_safety(path,gate,deadline=deadline),'logical_sha256':logical,'main_sha256':hash_file(path,deadline)}


def journal_header(path):
    size=Path(path).stat().st_size
    if not 512<size<=MAX_FILE:raise ValueError('invalid_journal_size')
    with Path(path).open('rb') as stream:
        header=stream.read(28);stream.seek(-16,2);tail=stream.read(16)
    if header[:8]!=MAGIC or tail[-8:]==MAGIC:raise ValueError('invalid_or_super_journal')
    count,_,pages,sector,page=struct.unpack('>IIIII',header[8:28])
    if (not pages or any(v<512 or v>65536 or v&(v-1) for v in (sector,page))
            or not count):raise ValueError('invalid_journal_header')
    return {'records':count,'original_pages':pages,'sector_bytes':sector,'page_bytes':page,'super_journal':False}


def copy_exact(source,destination,deadline):
    with Path(source).open('rb') as src,Path(destination).open('xb') as dst:
        for chunk in iter(lambda:src.read(1048576),b''):
            if time.monotonic()>=deadline:raise TimeoutError('recovery_deadline')
            dst.write(chunk)
        dst.flush();os.fsync(dst.fileno())
    if hash_file(source,deadline)!=hash_file(destination,deadline):raise ValueError('recovery_copy_mismatch')


def recover_quiesced(path,gate,backup,*,seconds=30,before_original=None):
    """Caller owns the maintenance/runtime locks. RAW is never recovered."""
    if type(seconds) not in (int,float) or not 2<=seconds<=30:raise ValueError('recovery_budget')
    deadline=time.monotonic()+seconds
    path=Path(path).resolve(strict=True);journal=Path(str(path)+'-journal')
    gate_off(gate)
    if path.stat().st_size>MAX_FILE:raise ValueError('ledger_size_limit')
    if any(Path(str(path)+suffix).exists() for suffix in ('-wal','-shm')):raise ValueError('unexpected_wal_state')
    try:
        c=connect(path);c.close()
    except sqlite3.Error as error:
        if error.sqlite_errorcode!=776:raise
    else:
        with native.ReadFiles([path],deadline=deadline):result=checked_database(path,gate,deadline)
        return {'recovered':False,'complete':True,'cleanup_confirmed':True,**result}
    backup=Path(backup).resolve()
    if backup==path.parent or path.parent in backup.parents:raise ValueError('backup_inside_ledger_directory')
    backup.mkdir(parents=True,exist_ok=False)
    raw_dir=backup/'raw';work_dir=backup/'work';raw_dir.mkdir();work_dir.mkdir()
    with ExitStack() as stack:
        source_guards=stack.enter_context(native.ReadFiles([path,journal],deadline=deadline))
        before={p.name:metadata(p) for p in (path,journal)}
        header=journal_header(journal)
        hashes={p.name:hash_file(p,deadline) for p in (path,journal)}
        for p in (path,journal):
            copy_exact(p,raw_dir/p.name,deadline);copy_exact(raw_dir/p.name,work_dir/p.name,deadline)
        stack.enter_context(native.ReadFiles([raw_dir/path.name,raw_dir/journal.name],deadline=deadline))
        if hashes!={p.name:hash_file(raw_dir/p.name,deadline) for p in (path,journal)}:
            raise ValueError('recovery_raw_backup_changed')
        work=work_dir/path.name
        c=connect(work,writable=True,deadline=deadline);c.close() # SQLite rollback only
        with native.ReadFiles([work],deadline=deadline):expected=checked_database(work,gate,deadline)
        if before!={p.name:metadata(p) for p in (path,journal)} or hashes!={p.name:hash_file(p,deadline) for p in (path,journal)}:
            raise ValueError('recovery_source_changed')
        if before_original is not None:before_original()
        gate_off(gate)
        if before!={p.name:metadata(p) for p in (path,journal)} or hashes!={p.name:hash_file(p,deadline) for p in (path,journal)}:
            raise ValueError('recovery_source_changed')
        # Only source guards close here. RAW remains guarded through original
        # verification. Persistent ACTIVE/fence/RuntimeMutex stay with caller.
        source_guards.close()
        c=connect(path,writable=True,deadline=deadline);c.close()
        with native.ReadFiles([path],deadline=deadline):
            actual=checked_database(path,gate,deadline)
            after=metadata(path)
            if (after['device'],after['file_id'])!=(before[path.name]['device'],before[path.name]['file_id']) or actual!=expected:
                raise ValueError('recovery_original_mismatch')
            if hashes!={p.name:hash_file(raw_dir/p.name,deadline) for p in (path,journal)}:
                raise ValueError('recovery_raw_backup_changed')
    return {'recovered':True,'complete':True,'cleanup_confirmed':not native._RETAINED_GUARDS,
        'sqlite_version':sqlite3.sqlite_version,'backup':str(backup),'raw_sha256':hashes,'source_before':before,
        'journal_header':header,**actual}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('operation',choices=['verify','recover'])
    parser.add_argument('--ledger',required=True);parser.add_argument('--gate',required=True)
    parser.add_argument('--backup');parser.add_argument('--output');parser.add_argument('--nonce');parser.add_argument('--ack')
    args=parser.parse_args()
    report=None
    try:
        if args.operation=='verify':print(json.dumps(passive_safety(args.ledger,args.gate)));return 0
        if not args.output or not args.ack or not args.backup or not re.fullmatch('[a-f0-9]{32}',args.nonce or ''):
            raise ValueError('recovery_parameters')
        import db_offline_probe
        report={'schema_version':1,'nonce':args.nonce,'pid':os.getpid(),'phase':'RUNNING','complete':False,'cleanup_confirmed':False}
        db_offline_probe.save(report,Path(args.output))
        deadline=time.monotonic()+5
        while not Path(args.ack).exists():
            if time.monotonic()>=deadline:raise TimeoutError('recovery_native_ack_missing')
            time.sleep(.01)
        with Path(args.ack).open('rb') as stream:body=stream.read(1025)
        if len(body)>1024:raise ValueError('recovery_native_ack_size')
        ack=decode(body.decode('utf-8'))
        if (set(ack)!={'nonce','pid'} or ack['nonce']!=args.nonce or type(ack['pid'])is not int or ack['pid']!=os.getpid()):
            raise ValueError('recovery_native_ack_invalid')
        report.update(recover_quiesced(args.ledger,args.gate,args.backup))
        report['phase']='FINAL';db_offline_probe.save(report,Path(args.output));return 0
    except Exception:
        if report is not None:
            report.update(phase='FINAL',complete=False,cleanup_confirmed=False,code='OFFLINE_LEDGER_RECOVERY_FAILED')
            try:
                import db_offline_probe;db_offline_probe.save(report,Path(args.output))
            except Exception:pass
        return 2


if __name__=='__main__':raise SystemExit(main())
