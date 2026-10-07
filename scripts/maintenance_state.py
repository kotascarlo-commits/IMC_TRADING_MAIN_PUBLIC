"""Persistent fail-closed maintenance state. Does not start/stop or trade.

Writers must own the global maintenance mutex. ACTIVE is written before any
protected operation; IDLE requires completed integrity, safety and cleanup.
"""
import argparse
import ctypes
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError('duplicate_field')
        result[key] = value
    return result


def validate(value):
    if not isinstance(value, dict) or set(value) != {
        'schema_version','state','nonce','owner_pid','owner_birth','updated_at','reason','proof'}:
        raise ValueError('maintenance_schema')
    if type(value['schema_version']) is not int or value['schema_version'] != 1:
        raise ValueError('maintenance_schema')
    if value['state'] not in ('ACTIVE','IDLE') or not isinstance(value['nonce'],str) or not re.fullmatch('[a-f0-9]{32}',value['nonce']):
        raise ValueError('maintenance_state')
    if type(value['owner_pid']) is not int or not 0 < value['owner_pid'] <= 2147483647:
        raise ValueError('maintenance_owner')
    for key in ('owner_birth','updated_at'):
        if not isinstance(value[key],str) or len(value[key]) > 40:
            raise ValueError('maintenance_time')
        parsed = datetime.fromisoformat(value[key].replace('Z','+00:00'))
        if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
            raise ValueError('maintenance_time')
    if value['state'] == 'ACTIVE':
        if value['reason'] not in ('OFFLINE_AUDIT','RECOVERY_REQUIRED') or value['proof'] is not None:
            raise ValueError('maintenance_active')
    elif (value['reason'] != 'VERIFIED_CLEANUP' or not isinstance(value['proof'],dict)
          or set(value['proof']) != {'integrity','safety','native_cleanup'}
          or any(v is not True for v in value['proof'].values())):
        raise ValueError('maintenance_unverified_idle')
    return value


def read(path):
    with Path(path).open('rb') as stream: data = stream.read(8193)
    if len(data) > 8192: raise ValueError('maintenance_size')
    return validate(json.loads(data.decode('utf-8-sig'),object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('invalid_constant'))))


def bound_snapshot(path, digest):
    if not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest):
        raise ValueError('maintenance_snapshot_hash')
    with Path(path).open('rb') as stream:data=stream.read(8193)
    if len(data)>8192 or hashlib.sha256(data).hexdigest()!=digest:
        raise ValueError('maintenance_snapshot_changed')
    return validate(json.loads(data.decode('utf-8-sig'),object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('invalid_constant'))))


def write(path, value):
    path = Path(path).resolve()
    validate(value)
    temporary = path.with_name(path.name+'.'+value['nonce']+'.tmp')
    data = json.dumps(value,sort_keys=True,allow_nan=False,separators=(',',':')).encode('utf-8')
    with temporary.open('xb') as stream:
        stream.write(data); stream.flush(); os.fsync(stream.fileno())
    if os.name == 'nt':
        api = ctypes.WinDLL('kernel32',use_last_error=True)
        api.MoveFileExW.argtypes = [ctypes.c_wchar_p,ctypes.c_wchar_p,ctypes.c_uint32]
        api.MoveFileExW.restype = ctypes.c_int
        if not api.MoveFileExW(str(temporary),str(path),0x1 | 0x8):
            raise ctypes.WinError(ctypes.get_last_error())
    else: os.replace(temporary,path)
    if read(path) != value: raise ValueError('maintenance_publish_unverified')


def handover(path, expected, *, nonce, owner_pid, owner_birth, published_state=None):
    """Compare the complete old ACTIVE generation under the owner's mutex.

    The caller must already own the global maintenance mutex and retain file
    guards. This comparison does not provide a lock against arbitrary writers.
    A mismatched or uncertain publication never permits cleanup or restart.
    """
    expected=validate(json.loads(json.dumps(expected,allow_nan=False),object_pairs_hook=unique))
    if expected['state']!='ACTIVE':raise ValueError('maintenance_handover_requires_active')
    value={'schema_version':1,'state':'ACTIVE','nonce':nonce,'owner_pid':owner_pid,
        'owner_birth':owner_birth,'updated_at':datetime.now(timezone.utc).isoformat(),
        'reason':'RECOVERY_REQUIRED','proof':None}
    validate(value)
    if nonce==expected['nonce']:raise ValueError('maintenance_handover_requires_fresh_nonce')
    if read(path)!=expected:raise ValueError('maintenance_generation_changed')
    if published_state:
        if Path(path).resolve()==Path(published_state).resolve():raise ValueError('maintenance_snapshot_path')
        fresh_snapshot(published_state,value)
        if read(path)!=expected:raise ValueError('maintenance_generation_changed')
    write(path,value)
    return value


def idle(path, expected, *, nonce, owner_pid, owner_birth):
    """Release only the complete bound ACTIVE generation under caller's mutex."""
    expected=validate(json.loads(json.dumps(expected,allow_nan=False),object_pairs_hook=unique))
    if expected['state']!='ACTIVE':raise ValueError('maintenance_idle_requires_active')
    if (nonce!=expected['nonce'] or type(owner_pid) is not int or owner_pid!=expected['owner_pid']
            or owner_birth!=expected['owner_birth']):raise ValueError('maintenance_owner_changed')
    value=dict(expected,state='IDLE',updated_at=datetime.now(timezone.utc).isoformat(),
        reason='VERIFIED_CLEANUP',proof={'integrity':True,'safety':True,'native_cleanup':True})
    validate(value)
    if read(path)!=expected:raise ValueError('maintenance_generation_changed')
    write(path,value)
    return value


def fresh_snapshot(path, value):
    """Create the exact publisher's object; never overwrite an older snapshot."""
    validate(value)
    data=json.dumps(value,sort_keys=True,allow_nan=False,separators=(',',':')).encode('utf-8')
    with Path(path).open('xb') as stream:
        stream.write(data);stream.flush();os.fsync(stream.fileno())
    if read(path)!=value:raise ValueError('maintenance_snapshot_unverified')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation',choices=['read','active','idle','handover'])
    parser.add_argument('--path',required=True)
    parser.add_argument('--nonce');parser.add_argument('--owner-pid',type=int);parser.add_argument('--owner-birth')
    parser.add_argument('--reason',choices=['OFFLINE_AUDIT','RECOVERY_REQUIRED'])
    parser.add_argument('--expected-state')
    parser.add_argument('--published-state')
    parser.add_argument('--expected-sha256')
    args = parser.parse_args()
    try:
        if args.operation == 'read': value = read(args.path)
        elif args.operation == 'handover':
            value=handover(args.path,read(args.expected_state),nonce=args.nonce,
                owner_pid=args.owner_pid,owner_birth=args.owner_birth,published_state=args.published_state)
        elif args.operation == 'idle':
            if not args.expected_state:raise ValueError('maintenance_expected_required')
            value=idle(args.path,bound_snapshot(args.expected_state,args.expected_sha256),nonce=args.nonce,
                owner_pid=args.owner_pid,owner_birth=args.owner_birth)
        else:
            value={'schema_version':1,'state':'ACTIVE',
                'nonce':args.nonce,'owner_pid':args.owner_pid,'owner_birth':args.owner_birth,
                'updated_at':datetime.now(timezone.utc).isoformat(),
                'reason':args.reason,'proof':None}
            if args.published_state:
                if Path(args.path).resolve()==Path(args.published_state).resolve():
                    raise ValueError('maintenance_snapshot_path')
                fresh_snapshot(args.published_state,value)
            write(args.path,value)
        response={'state':value['state'],'nonce':value['nonce']}
        if args.operation in ('active','handover') and args.published_state:
            response['snapshot_sha256']=hashlib.sha256(json.dumps(value,sort_keys=True,
                allow_nan=False,separators=(',',':')).encode('utf-8')).hexdigest()
        print(json.dumps(response))
        return 0
    except Exception:
        print(json.dumps({'state':'UNKNOWN','nonce':None}))
        return 2


if __name__ == '__main__': raise SystemExit(main())
