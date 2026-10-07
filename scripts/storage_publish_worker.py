"""Two-role native worker, admitted only by a separately reviewed Root host.

CLI values are connection data. Root proves its specific suspended-start Job
member, role, copy evidence, Gate OFF and ledger before sending BOOT. Neither
this worker nor visible JSON grants publication, rollback, IDLE or restart.
"""
import argparse
import ctypes
import os
from pathlib import Path
import time

from storage_publish_authority import ParentAuthority
from storage_publish_channel import Client, _FileTime
from storage_publish_deadline import Deadline
from storage_publish_guard import RenameGuards
from storage_publish_journal_endpoint import JournalEndpoint
from storage_publish_manifest import decode, encoded, generation
from storage_publish_move_endpoint import MoveEndpoint
from storage_publish_parent import ParentReadGuards
from storage_publish_writer import context, digest


_RETAINED_BOOT=[]


def boot(body,*,role,nonce,project_root,parent_pid,parent_birth,worker_pid,worker_birth,deadline_binding):
    value=decode(body)
    keys={'schema_version','kind','role','generation','authority','namespace','binding','deadline'}
    if (type(value) is not dict or set(value)!=keys or encoded(value)!=body
            or type(value['schema_version']) is not int or value['schema_version']!=2
            or value['kind']!='WORKER_BOOT' or value['role']!=role
            or role not in ('journal','rename')):
        raise ValueError('publish_worker_boot_schema')
    clock=value['deadline']
    if (type(clock) is not dict or set(clock)!={'end_tick','frequency'}
            or any(type(clock[key]) is not int or not 0 < clock[key] < 2**63 for key in clock)
            or type(deadline_binding) is not tuple
            or (clock['end_tick'],clock['frequency'])!=deadline_binding):
        raise ValueError('publish_worker_boot_deadline')
    binding=context(value['binding'],role=role)
    generation(value['generation'],project_root=project_root)
    envelope={key:item for key,item in value.items() if key!='binding'}
    if (binding['publish_nonce']!=nonce or value['generation']['publish_nonce']!=nonce
            or binding['parent_pid']!=parent_pid or binding['parent_birth']!=parent_birth
            or binding['worker_pid']!=worker_pid or binding['worker_birth']!=worker_birth
            or binding['generation_sha256']!=digest(encoded(value['generation']))
            or binding['envelope_sha256']!=digest(encoded(envelope))):
        raise ValueError('publish_worker_boot_digest')
    authority=value['authority'];namespace=value['namespace']
    if (type(authority) is not dict or set(authority)!=
            {'thread_id','thread_birth','marker_identity','active_snapshot'}
            or type(namespace) is not dict or set(namespace)!={'directories','sentinels'}
            or type(namespace['directories']) is not dict or type(namespace['sentinels']) is not dict):
        raise ValueError('publish_worker_boot_inventory')
    return value


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--nonce',required=True)
    parser.add_argument('--role',required=True,choices=('journal','rename'))
    parser.add_argument('--parent-pid',required=True,type=int)
    parser.add_argument('--parent-birth',required=True,type=int)
    parser.add_argument('--end-tick',required=True,type=int)
    parser.add_argument('--frequency',required=True,type=int)
    parser.add_argument('--root',required=True)
    args=parser.parse_args()
    held={'phase':'BOOTSTRAP','client':None,'authority':None,'guards':None,'endpoint':None}
    _RETAINED_BOOT.append(held)
    try:
        root=Path(args.root)
        if root!=Path(__file__).resolve().parents[1] or root!=root.resolve(strict=True):
            raise ValueError('publish_worker_root')
        deadline=Deadline(args.end_tick,args.frequency)
        client=Client(args.nonce,args.role,parent_pid=args.parent_pid,parent_birth=args.parent_birth,deadline=deadline)
        held['client']=client
        client.api.GetCurrentProcess.argtypes=[];client.api.GetCurrentProcess.restype=ctypes.c_void_p
        birth,exit_time,kernel,user=(_FileTime() for _ in range(4))
        if not client.api.GetProcessTimes(client.api.GetCurrentProcess(),ctypes.byref(birth),ctypes.byref(exit_time),
                                         ctypes.byref(kernel),ctypes.byref(user)):
            raise ValueError('publish_worker_native_birth')
        value=boot(client.read(),role=args.role,nonce=args.nonce,project_root=str(root),
            parent_pid=client._pid,parent_birth=client._birth,worker_pid=os.getpid(),worker_birth=(birth.high<<32)|birth.low,
            deadline_binding=deadline.binding)
        info=value['authority']
        authority=ParentAuthority(client,project_root=str(root),parent_thread=info['thread_id'],
            parent_thread_birth=info['thread_birth'],marker_identity=info['marker_identity'],active_snapshot=info['active_snapshot'])
        held['authority']=authority
        paths=value['generation']['paths'];namespace=value['namespace']
        deadline.check()
        # An additional local bound cannot replace or extend shared QPC.
        local_end=time.monotonic()+(deadline.binding[0]-deadline._last)/deadline.binding[1]
        if args.role=='journal':
            directory=root/'.local-tools'/('storage-publish-'+args.nonce)/'journal'
            sentinel=directory/('.imc-publish-guard-'+args.nonce+'.sentinel')
            guards=ParentReadGuards(root,{'sentinel':sentinel},deadline=local_end,nonce=args.nonce,
                attached_directories=namespace['directories'],attached_sentinels=namespace['sentinels'],shared_deadline=deadline)
        else:
            members={key:Path(paths[key]) for key,item in value['generation']['members'].items() if item is not None}
            destinations=[Path(paths[key]) for key in ('archive_shm','archive_wal','archive_main','installed_main')]
            guards=RenameGuards(root,members,deadline=local_end,destinations=destinations,nonce=args.nonce,
                attached_directories=namespace['directories'],attached_sentinels=namespace['sentinels'],shared_deadline=deadline)
        held['guards']=guards
        guards.acquire_retained()
        if args.role=='journal':
            endpoint=JournalEndpoint(directory,value['generation'],value['binding'],project_root=str(root),
                client=client,authority=authority,scopes=guards)
        else:
            endpoint=MoveEndpoint(value['generation'],value['binding'],project_root=str(root),
                client=client,authority=authority,guards=guards)
        held['endpoint']=endpoint;held['phase']='ADMITTED'
        client.write(encoded({'schema_version':2,'kind':'WORKER_READY','role':args.role,
            'context_sha256':endpoint.context_sha256,'boot_sha256':digest(encoded(value))}))
        for _ in range(12 if args.role=='journal' else 4):endpoint.serve_one()
        if args.role=='rename':endpoint.serve_release()
        if endpoint.serve_finish() is not True or not endpoint.finish_completed:
            raise ValueError('publish_worker_finish_unconfirmed')
        if not authority.closed or authority.faulted or not client.closed or client.faulted:
            raise ValueError('publish_worker_native_cleanup_unconfirmed')
        held['phase']='WORKER_CLEANED'
        return 0
    except BaseException:
        held['phase']='FAULT'
        # No error-ACK, Source close, reconnect, resume, rollback or final Exit0.
        # Static retention preserves partial construction; Root owns Job cleanup.
        while True:time.sleep(1)


if __name__=='__main__':
    raise SystemExit(main())
