"""Pure publication wire encoding; native authority remains with Root/workers.

Only BOOT digests are calculated. Journal evidence must already exist and is
validated without inventing audit, cleanup or absence results. This module
opens no files, acquires no mutexes and starts no processes.
"""
import re
from pathlib import PureWindowsPath

from storage_publish_manifest import (STEPS, decode, encoded, generation,
                                     validate_evidence)
from storage_publish_worker import boot
from storage_publish_writer import context, digest


def _canonical_windows_path(text):
    if type(text) is not str or not re.match(r'^[A-Z]:\\', text):
        raise ValueError('publish_frame_namespace_path')
    path = PureWindowsPath(text)
    if str(path) != text or not path.is_absolute():
        raise ValueError('publish_frame_namespace_path')
    for part in path.parts[1:]:
        if (part in ('.', '..') or part.endswith((' ', '.'))
                or any(ord(char) < 32 or char in '<>:"|?*' for char in part)
                or re.fullmatch(r'(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])',
                                part.split('.')[0].rstrip(' ').upper())):
            raise ValueError('publish_frame_namespace_path')
    return path


def namespace_for_role(held_namespace, value, *, project_root, role):
    """Select original held keys only; no native ownership or health claim."""
    generation(value, project_root=project_root)
    root = _canonical_windows_path(project_root)
    if value['root'] != project_root or role not in ('journal', 'rename'):
        raise ValueError('publish_frame_namespace_role_root')
    if (type(held_namespace) is not dict
            or set(held_namespace) != {'directories', 'sentinels'}
            or any(type(held_namespace[key]) is not dict for key in held_namespace)):
        raise ValueError('publish_frame_namespace_shape')
    spellings = {}
    for text in value['paths'].values():
        path = _canonical_windows_path(text)
        if not text.startswith(project_root + '\\'):
            raise ValueError('publish_frame_namespace_path')
        for item in (path, *path.parents):
            original = str(item)
            folded = original.casefold()
            if folded in spellings and spellings[folded] != original:
                raise ValueError('publish_frame_namespace_path_alias')
            spellings[folded] = original
    if role == 'journal':
        leaves = {root / '.local-tools' / ('storage-publish-' + value['publish_nonce']) / 'journal'}
    else:
        keys = [key for key, member in value['members'].items() if member is not None]
        keys += ['archive_shm', 'archive_wal', 'archive_main', 'installed_main']
        leaves = {PureWindowsPath(value['paths'][key]).parent for key in keys}
    directories = set()
    for leaf in leaves:
        current = leaf
        while True:
            directories.add(str(current))
            if current.parent == current:
                break
            current = current.parent
    sentinels = {str(leaf / ('.imc-publish-guard-' + value['publish_nonce'] + '.sentinel'))
                 for leaf in leaves}
    result = {'directories': {}, 'sentinels': {}}
    seen = set()
    volume = value['members']['source_main']['volume']
    for kind, paths in (('directories', directories), ('sentinels', sentinels)):
        for path in sorted(paths):
            if path not in held_namespace[kind]:
                raise ValueError('publish_frame_namespace_missing')
            identity = held_namespace[kind][path]
            fields = {'volume', 'file_id'} | ({'bytes'} if kind == 'sentinels' else set())
            if (type(identity) is not dict or set(identity) != fields
                    or any(type(number) is not int for number in identity.values())
                    or not 0 <= identity['volume'] < 2**64
                    or not 0 < identity['file_id'] < 2**128
                    or identity['volume'] != volume
                    or kind == 'sentinels' and identity['bytes'] != 0):
                raise ValueError('publish_frame_namespace_identity')
            binding = (identity['volume'], identity['file_id'])
            if binding in seen:
                raise ValueError('publish_frame_namespace_alias')
            seen.add(binding)
            result[kind][path] = dict(identity)
    return result


def worker_boot(value, *, project_root, role, nonce, parent_pid, parent_birth,
                worker_pid, worker_birth, deadline_binding):
    """Encode against independent original native bindings, never wire claims."""
    value = decode(encoded(value))
    if type(value) is not dict or type(value.get('binding')) is not dict:
        raise ValueError('publish_frame_boot_shape')
    value['binding']['generation_sha256'] = digest(encoded(value.get('generation')))
    value['binding']['envelope_sha256'] = digest(encoded(
        {key: member for key, member in value.items() if key != 'binding'}))
    context({'role': role, 'publish_nonce': nonce, 'parent_pid': parent_pid,
             'parent_birth': parent_birth, 'worker_pid': worker_pid,
             'worker_birth': worker_birth,
             'generation_sha256': value['binding']['generation_sha256'],
             'envelope_sha256': value['binding']['envelope_sha256']}, role=role)
    if (type(deadline_binding) is not tuple or len(deadline_binding) != 2
            or any(type(number) is not int or not 0 < number < 2**63
                   for number in deadline_binding)):
        raise ValueError('publish_frame_deadline')
    body = encoded(value)
    boot(body, role=role, nonce=nonce, project_root=project_root,
         parent_pid=parent_pid, parent_birth=parent_birth, worker_pid=worker_pid,
         worker_birth=worker_birth, deadline_binding=deadline_binding)
    return body


def journal_request(value, binding, *, project_root, index, previous_sha256,
                    request_nonce, evidence):
    """Build one request; runtime owns current sequence, replay and freshness."""
    value = decode(encoded(value))
    binding = context(binding)
    generation(value, project_root=project_root)
    if (binding['generation_sha256'] != digest(encoded(value))
            or binding['publish_nonce'] != value['publish_nonce']):
        raise ValueError('publish_frame_generation')
    if type(index) is not int or not 0 <= index < 12:
        raise ValueError('publish_frame_index')
    if (type(previous_sha256) is not str
            or re.fullmatch('[a-f0-9]{64}', previous_sha256) is None
            or type(request_nonce) is not str
            or re.fullmatch('[a-f0-9]{32}', request_nonce) is None):
        raise ValueError('publish_frame_digest_nonce')
    evidence = decode(encoded(evidence))
    step, kind = STEPS[index // 2], 'DONE' if index % 2 else 'INTENT'
    validate_evidence(value, kind, step, evidence, schema_version=2)
    return encoded({'schema_version': 2, 'kind': 'JOURNAL_REQUEST',
                    'context_sha256': digest(encoded(binding)),
                    'ipc_sequence': index + 1, 'journal_sequence': index,
                    'request_nonce': request_nonce,
                    'previous_sha256': previous_sha256, 'step': step,
                    'event_kind': kind, 'evidence': evidence})
