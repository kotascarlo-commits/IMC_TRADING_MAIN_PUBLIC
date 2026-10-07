"""Native writer construction over an already pinned, prepared journal scope.

No CLI or production grant. Root separately proves Job membership and role;
this factory owns no Source handles and cannot publish or restart IMC.
"""
import ctypes
import hashlib
import os
from pathlib import Path

from storage_publish_authority import ParentAuthority
from storage_publish_channel import _FileTime
from storage_publish_deadline import Deadline
from storage_publish_parent import ParentReadGuards
from storage_publish_writer import Writer, context


def build(directory, value, binding, *, project_root, deadline, authority, scopes):
    if (type(deadline) is not Deadline or type(authority) is not ParentAuthority
            or type(scopes) is not ParentReadGuards
            or authority._deadline is not deadline or authority._client._shared_deadline is not deadline
            or scopes._shared_deadline is not deadline
            or scopes.attached_directories is None or scopes.attached_sentinels is None):
        raise ValueError('publish_writer_native_scope')
    binding=context(binding)
    root=Path(project_root).resolve(strict=True)
    directory=Path(directory).absolute()
    expected=root/'.local-tools'/('storage-publish-'+binding['publish_nonce'])/'journal'
    sentinel=directory/('.imc-publish-guard-'+binding['publish_nonce']+'.sentinel')
    ancestors=set();cursor=expected
    while True:
        ancestors.add(cursor)
        if cursor==Path(root.anchor):break
        cursor=cursor.parent
    if (set(scopes.directories)!=ancestors or set(scopes.sentinels)!={sentinel}
            or set(scopes.attached_directories)!={str(path) for path in ancestors}
            or set(scopes.attached_sentinels)!={str(sentinel)}):
        raise ValueError('publish_writer_native_inventory')
    if (directory!=expected or scopes.root!=root or authority._marker!=root/'RUNTIME/maintenance-state.json'
            or scopes.nonce!=binding['publish_nonce'] or set(scopes.members)!= {'sentinel'} or scopes.destinations
            or authority._client._nonce!=binding['publish_nonce'] or authority._client._role!='journal'
            or scopes.members['sentinel']['path']!=sentinel
            or binding['parent_pid']!=authority._pid or binding['parent_birth']!=authority._client._birth
            or binding['worker_pid']!=os.getpid() or authority._snapshot['nonce']!=binding['publish_nonce']):
        raise ValueError('publish_writer_native_binding')
    api=authority._client.api
    api.GetCurrentProcess.argtypes=[];api.GetCurrentProcess.restype=ctypes.c_void_p
    birth,exit,kernel,user=(_FileTime() for _ in range(4))
    if not api.GetProcessTimes(api.GetCurrentProcess(),ctypes.byref(birth),ctypes.byref(exit),
                               ctypes.byref(kernel),ctypes.byref(user)):
        raise ValueError('publish_writer_native_birth')
    if ((birth.high<<32)|birth.low) != binding['worker_birth']:
        raise ValueError('publish_writer_native_birth')
    def check():
        deadline.check();authority.check();scopes.assert_bound()
        if scopes.close_fault:raise ValueError('publish_writer_scope_faulted')
        deadline.check()
        return True
    check()
    identity=dict(scopes.members['sentinel']['identity'])
    if identity['bytes']!=0 or scopes.digest('sentinel')!=hashlib.sha256(b'').hexdigest():
        raise ValueError('publish_writer_native_sentinel')
    identity['sha256']=hashlib.sha256(b'').hexdigest()
    return Writer(directory,value,binding,project_root=str(root),deadline=deadline,
                  authority_check=check,prepared_sentinel=identity)
