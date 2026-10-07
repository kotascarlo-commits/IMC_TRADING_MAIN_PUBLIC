"""Actual main(), exact source copy; LocalMu namespace is the sole adapter.

Fault injection and a separate diagnostic file observe, never exit/close the
worker's permanent fault loop. No production mutex or database is touched.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading

repo, root, case, role, nonce, *arguments = sys.argv[1:]
repo, root = Path(repo), Path(root)
sys.path.insert(0, str(repo / 'scripts'))
source = repo / 'scripts/storage_publish_worker.py'
copy = root / 'scripts/storage_publish_worker.py'
assert copy.read_bytes() == source.read_bytes()
spec = importlib.util.spec_from_file_location('fixture_actual_worker', copy)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)
assert Path(worker.__file__).resolve() == copy.resolve()
for name in ('storage_publish_authority','storage_publish_channel','storage_publish_guard',
             'storage_publish_journal_endpoint','storage_publish_move_endpoint'):
    assert Path(sys.modules[name].__file__).resolve() == (repo/'scripts'/f'{name}.py').resolve()
original = worker.ParentAuthority
def local_mutex_factory(*args, **kwargs):
    assert 'private_test_nonce' not in kwargs
    return original(*args, **kwargs, private_test_nonce=nonce)
worker.ParentAuthority = local_mutex_factory

selected = []
expired_boot_returned = []
if case == 'Partial':
    cls = worker.ParentReadGuards if role == 'journal' else worker.RenameGuards
    native_open, native_info = cls._open, cls._info
    def partial_open(self, path):
        handle = native_open(self, path)
        selected.append(handle)
        return handle
    def partial_info(self, handle):
        if handle in selected:
            raise ValueError('fixture_main_partial_inspection')
        return native_info(self, handle)
    cls._open, cls._info = partial_open, partial_info
if case == 'ReadyWrite':
    native_write = worker.Client.write
    def fail_ready(self, body):
        if json.loads(body)['kind'] == 'WORKER_READY':
            raise RuntimeError('fixture_main_ready_pre_write')
        return native_write(self, body)
    worker.Client.write = fail_ready
if case == 'Expiry':
    native_read = worker.Client.read
    def expired_boot(self):
        body = native_read(self)
        wait = threading.Event()
        while True:
            try:self._shared_deadline.check()
            except TimeoutError:
                expired_boot_returned.append(True)
                return body
            wait.wait(.005)
    worker.Client.read = expired_boot

def observe():
    wait = threading.Event()
    while True:
        if worker._RETAINED_BOOT and worker._RETAINED_BOOT[-1]['phase'] == 'FAULT':
            held = worker._RETAINED_BOOT[-1]
            client, authority, guards = (held[key] for key in ('client','authority','guards'))
            states = {} if guards is None else {
                str(key): member['handle'] is not None for key,member in guards.members.items()}
            report = {'phase':'FAULT','pid':os.getpid(),'case':case,'role':role,
                'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
                'local_mutex_adapter':True,'client_present':client is not None,
                'client_closed':False if client is None else client.closed,
                'authority_present':authority is not None,
                'authority_closed':False if authority is None else authority.closed,
                'guards_present':guards is not None,'held_members':states,
                'guards_closed':False if guards is None else guards.release_closed,
                'endpoint_present':held['endpoint'] is not None,
                'expired_boot_returned':bool(expired_boot_returned)}
            path = root/'RUNTIME/main-fault.json'
            temporary = path.with_suffix('.tmp')
            with temporary.open('x',encoding='utf-8') as stream:
                json.dump(report,stream,sort_keys=True);stream.flush();os.fsync(stream.fileno())
            os.replace(temporary,path)
            return
        wait.wait(.005)
threading.Thread(target=observe,daemon=True).start()
sys.argv = [str(copy), '--nonce',nonce,'--role',role,'--root',str(root),*arguments]
raise SystemExit(worker.main())
