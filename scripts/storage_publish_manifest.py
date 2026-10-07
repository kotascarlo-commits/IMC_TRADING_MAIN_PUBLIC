"""Durable publication journal; this module never moves a database or starts IMC.

An owning native maintenance controller supplies the verified generation and
keeps ACTIVE through every rename. Each step has a separately fsynced intent
and completion. A completion cannot erase an unresolved earlier intent.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path, PureWindowsPath
import re
import stat


STEPS = ('ARCHIVE_SHM', 'ARCHIVE_WAL', 'ARCHIVE_MAIN', 'INSTALL_MAIN',
         'VERIFY_INSTALLED', 'RELEASE')
MAX_BYTES = 131072
PATHS = ('source_main', 'source_wal', 'source_shm', 'work_main', 'archive_main',
         'archive_wal', 'archive_shm', 'installed_main', 'retained_new_main')
CONTRACT = 'SINGLE_WRITER_SOURCE_RELEASE_V2'
_UNCERTAIN_STREAMS = []


def decode(body):
    if type(body) is not bytes or len(body) > MAX_BYTES:
        raise ValueError('publish_journal_size')
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError('publish_journal_duplicate_field')
            value[key] = item
        return value
    def reject(_):
        raise ValueError('publish_journal_nonfinite')
    def floating(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError('publish_journal_nonfinite')
        return number
    return json.loads(body.decode('utf-8'), object_pairs_hook=pairs,
                      parse_constant=reject, parse_float=floating)


def encoded(value):
    body = json.dumps(value, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')
    if len(body) > MAX_BYTES:
        raise ValueError('publish_journal_size')
    return body


def fresh_record(path, value):
    """No overwrite/retry. fsync failure leaves the uncertain file in place."""
    body = encoded(value)
    stream = Path(path).open('xb')
    try:
        written = stream.write(body)
        if written != len(body):
            raise OSError('publish_journal_short_write')
        stream.flush()
        os.fsync(stream.fileno())
    finally:
        try:
            stream.close()
        except BaseException:
            _UNCERTAIN_STREAMS.append(stream)
            raise RuntimeError('publish_journal_close_unconfirmed') from None
    return hashlib.sha256(body).hexdigest()


def identity(value):
    if (type(value) is not dict or set(value) != {'volume', 'file_id', 'bytes', 'sha256'}
            or any(type(value[k]) is not int or value[k] < 0
                   for k in ('volume', 'file_id', 'bytes'))
            or value['file_id'] == 0 or type(value['sha256']) is not str
            or re.fullmatch('[a-f0-9]{64}', value['sha256']) is None):
        raise ValueError('publish_journal_identity')


def generation(value, *, project_root):
    if (type(value) is not dict or set(value) != {'copy_nonce', 'publish_nonce', 'members', 'root', 'paths'}
            or any(type(value[k]) is not str or re.fullmatch('[a-f0-9]{32}', value[k]) is None
                   for k in ('copy_nonce', 'publish_nonce'))
            or value['copy_nonce'] == value['publish_nonce']
            or type(value['members']) is not dict
            or set(value['members']) != {'source_main', 'source_wal', 'source_shm', 'work_main'}):
        raise ValueError('publish_journal_generation')
    for key, member in value['members'].items():
        if member is None:
            if key not in ('source_wal', 'source_shm'):
                raise ValueError('publish_journal_missing_main')
        else:
            identity(member)
    ids = [(m['volume'], m['file_id']) for m in value['members'].values() if m is not None]
    if len(set(ids)) != len(ids) or len({v for v, _ in ids}) != 1:
        raise ValueError('publish_journal_file_alias_or_volume')
    if type(value['root']) is not str or type(value['paths']) is not dict or set(value['paths']) != set(PATHS):
        raise ValueError('publish_journal_paths')
    root = PureWindowsPath(value['root'])
    if (not root.is_absolute() or re.fullmatch('[A-Za-z]:', root.drive) is None
            or chr(0) in value['root'] or any(p == '..' or ':' in p for p in root.parts[1:])):
        raise ValueError('publish_journal_root')
    if type(project_root) is not str:
        raise ValueError('publish_journal_project_root')
    expected = PureWindowsPath(project_root)
    if (not expected.is_absolute() or re.fullmatch('[A-Za-z]:', expected.drive) is None
            or chr(0) in project_root or any(p == '..' or ':' in p for p in expected.parts[1:])
            or root != expected):
        raise ValueError('publish_journal_project_root')
    paths = {}
    for key, text in value['paths'].items():
        if type(text) is not str or not text or chr(0) in text:
            raise ValueError('publish_journal_path')
        path = PureWindowsPath(text)
        try:
            relative = path.relative_to(root)
        except ValueError:
            raise ValueError('publish_journal_path_outside_root') from None
        if not path.is_absolute() or not relative.parts or any(p == '..' or ':' in p for p in relative.parts):
            raise ValueError('publish_journal_path')
        paths[key] = path
    if (paths['installed_main'] != paths['source_main']
            or paths['source_wal'] != PureWindowsPath(str(paths['source_main']) + '-wal')
            or paths['source_shm'] != PureWindowsPath(str(paths['source_main']) + '-shm')
            or paths['archive_wal'] != PureWindowsPath(str(paths['archive_main']) + '-wal')
            or paths['archive_shm'] != PureWindowsPath(str(paths['archive_main']) + '-shm')
            or len(set(paths.values())) != len(PATHS) - 1
            or paths['source_main'].name != paths['work_main'].name
            or paths['source_main'].name != paths['archive_main'].name):
        raise ValueError('publish_journal_path_binding')


def validate_evidence(value, kind, step, proof, *, schema_version=1):
    """Typed, generation-bound assertions; the native caller supplies facts."""
    if type(proof) is not dict:
        raise ValueError('publish_journal_evidence')
    paths, members = value['paths'], value['members']
    moves = {'ARCHIVE_SHM': ('source_shm', 'archive_shm'),
             'ARCHIVE_WAL': ('source_wal', 'archive_wal'),
             'ARCHIVE_MAIN': ('source_main', 'archive_main'),
             'INSTALL_MAIN': ('work_main', 'installed_main')}
    if step in moves:
        source, destination = moves[step]
        expected = members[source]
        keys = {'source', 'destination', 'identity'}
        if kind == 'DONE':
            keys |= {'source_absent', 'destination_absent'}
        if (set(proof) != keys or type(proof['source']) is not str
                or type(proof['destination']) is not str or proof['source'] != paths[source]
                or proof['destination'] != paths[destination]):
            raise ValueError('publish_journal_evidence_path')
        actual = proof['identity']
        if actual is not None:
            identity(actual)
        if actual != expected:
            raise ValueError('publish_journal_evidence_identity')
        if kind == 'DONE' and (proof['source_absent'] is not True
                or type(proof['destination_absent']) is not bool
                or proof['destination_absent'] is not (expected is None)):
            raise ValueError('publish_journal_evidence_absence')
        return
    keys = {'installed_main', 'identity'}
    if kind == 'DONE':
        keys |= ({'integrity', 'schema', 'canonical_sidecars_absent'} if step == 'VERIFY_INSTALLED'
                 else {('source_guard_cleanup' if schema_version == 2 else 'native_cleanup'),
                       'integrity', 'safety', 'absence'})
    if step not in ('VERIFY_INSTALLED', 'RELEASE') or set(proof) != keys:
        raise ValueError('publish_journal_evidence_shape')
    if type(proof['installed_main']) is not str or proof['installed_main'] != paths['installed_main']:
        raise ValueError('publish_journal_evidence_path')
    identity(proof['identity'])
    if proof['identity'] != members['work_main']:
        raise ValueError('publish_journal_evidence_identity')
    if any(proof[k] is not True for k in keys - {'installed_main', 'identity'}):
        raise ValueError('publish_journal_evidence_guard')


class Journal:
    """Ordered, immutable events, usable only with a fresh bounded directory.

    Call ``intent`` before a move and ``done`` only after native path identity
    and canonical sidecar absence checks. ``load`` reports pending intent; it
    never guesses that a rename succeeded or grants restart permission.
    """
    def __init__(self, directory, value, *, project_root, schema_version=1, prepared_sentinel=None):
        if type(schema_version) is not int or schema_version not in (1, 2):
            raise ValueError('publish_journal_schema')
        generation(value, project_root=project_root)
        self.directory = Path(directory)
        if prepared_sentinel is None:
            self.directory.mkdir(exist_ok=False)
        else:
            if schema_version != 2:
                raise ValueError('publish_journal_prepared_schema')
            identity(prepared_sentinel)
            if (prepared_sentinel['bytes'] != 0
                    or prepared_sentinel['sha256'] != hashlib.sha256(b'').hexdigest()):
                raise ValueError('publish_journal_prepared_identity')
            name = '.imc-publish-guard-' + value['publish_nonce'] + '.sentinel'
            if sorted(path.name for path in self.directory.iterdir()) != [name]:
                raise ValueError('publish_journal_prepared_inventory')
            info = (self.directory / name).lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_size != 0 or info.st_nlink != 1
                    or getattr(info, 'st_file_attributes', 0) & 0x400
                    or info.st_dev != prepared_sentinel['volume'] or info.st_ino != prepared_sentinel['file_id']):
                raise ValueError('publish_journal_prepared_identity')
            # The host holds native namespace/sentinel pins around this check.
            # Stat equality alone is not authority or a durability receipt.
        self._value = decode(encoded(value))
        self.schema_version = schema_version
        header = self._value if schema_version == 1 else {
            'schema_version': 2, 'contract': CONTRACT, 'generation': self._value}
        self.previous = fresh_record(self.directory / 'generation.json', header)
        self.next_step = 0
        self.pending = None

    @property
    def value(self):
        return decode(encoded(self._value))

    def _event(self, kind, step, evidence):
        # Validation precedes creating an intent file or any caller mutation.
        validate_evidence(self._value, kind, step, evidence, schema_version=self.schema_version)
        event = {'schema_version': self.schema_version, 'publish_nonce': self._value['publish_nonce'],
                 'sequence': self.next_step * 2 + (kind == 'DONE'),
                 'kind': kind, 'step': step, 'previous_sha256': self.previous,
                 'evidence': evidence}
        number = event['sequence']
        digest = fresh_record(self.directory / f'{number:02d}-{kind}.json', event)
        self.previous = digest

    def intent(self, step, evidence):
        if self.pending is not None or self.next_step >= len(STEPS) or step != STEPS[self.next_step]:
            raise ValueError('publish_journal_transition')
        self._event('INTENT', step, evidence)
        self.pending = step

    def done(self, step, evidence):
        if self.pending != step:
            raise ValueError('publish_journal_transition')
        self._event('DONE', step, evidence)
        self.pending = None
        self.next_step += 1

    @staticmethod
    def load(directory, *, project_root):
        """Validate the event chain only; ``complete`` is no restart permit.

        Recovery still needs fresh native file/path/hash proofs and its own
        durable fsync evidence. A readable record cannot prove that the old
        writer's fsync succeeded before its process disappeared.
        """
        directory = Path(directory)
        def read(path):
            with path.open('rb') as stream:
                body = stream.read(MAX_BYTES + 1)
            return decode(body), hashlib.sha256(body).hexdigest()
        header, previous = read(directory / 'generation.json')
        schema_version = 1
        if type(header) is dict and 'schema_version' in header:
            if (set(header) != {'schema_version', 'contract', 'generation'}
                    or type(header['schema_version']) is not int or header['schema_version'] != 2
                    or header['contract'] != CONTRACT):
                raise ValueError('publish_journal_header')
            schema_version = 2
            value = header['generation']
        else:
            value = header
        generation(value, project_root=project_root)
        files = sorted(p.name for p in directory.iterdir())
        # Native directory pinning leaves one generation-bound, zero-byte
        # no-delete sentinel. It is not an event or an authority/restart proof.
        sentinel_name = '.imc-publish-guard-' + value['publish_nonce'] + '.sentinel'
        if sentinel_name in files:
            info = (directory / sentinel_name).lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_size != 0 or info.st_nlink != 1
                    or getattr(info, 'st_file_attributes', 0) & 0x400):
                raise ValueError('publish_journal_sentinel')
            files.remove(sentinel_name)
        expected = ['generation.json']
        pending = None
        count = 0
        for step in STEPS:
            for kind in ('INTENT', 'DONE'):
                name = f'{count:02d}-{kind}.json'
                path = directory / name
                if not path.exists():
                    if sorted(expected) != files:
                        raise ValueError('publish_journal_inventory')
                    return {'generation': value, 'completed_steps': count // 2,
                            'pending_step': pending, 'complete': False}
                event, digest = read(path)
                if (type(event) is not dict
                        or set(event) != {'schema_version', 'publish_nonce', 'sequence', 'kind',
                                         'step', 'previous_sha256', 'evidence'}
                        or type(event['schema_version']) is not int or event['schema_version'] != schema_version
                        or type(event['sequence']) is not int or event['sequence'] != count
                        or event['publish_nonce'] != value['publish_nonce'] or event['kind'] != kind
                        or event['step'] != step or event['previous_sha256'] != previous
                        or type(event['evidence']) is not dict):
                    raise ValueError('publish_journal_chain')
                previous = digest
                validate_evidence(value, kind, step, event['evidence'], schema_version=schema_version)
                expected.append(name)
                count += 1
                pending = step if kind == 'INTENT' else None
        if sorted(expected) != files:
            raise ValueError('publish_journal_inventory')
        return {'generation': value, 'completed_steps': len(STEPS),
                'pending_step': None, 'complete': True}
