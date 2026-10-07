"""Four journalled native moves, with no CLI or maintenance authority.

This primitive is NOT a production publisher. The owning controller must bind
the real validated copy proof, native parent/worker lifetimes, mutex ownership,
ACTIVE generation and overlapping file/directory guards before calling it.
It must retain those guards on every failure. Neither verification, guard
handoff, RELEASE, rollback nor restart is performed here.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path

from storage_publish_guard import RenameGuards
from storage_publish_manifest import Journal, generation


MOVES = (('ARCHIVE_SHM', 'source_shm', 'archive_shm'),
         ('ARCHIVE_WAL', 'source_wal', 'archive_wal'),
         ('ARCHIVE_MAIN', 'source_main', 'archive_main'),
         ('INSTALL_MAIN', 'work_main', 'installed_main'))


class MoveChecks:
    """Single-use move phase over already-held guards; never closes them.

    ``authority_check`` is the owning host's fresh authority check, not a JSON
    grant. This callable alone does not prove that an actual native parent owns
    production mutexes. A production host still needs its separately reviewed
    parent/worker protocol. Any exception permanently consumes this instance.
    """
    def __init__(self, value, guards, *, project_root, authority_check, journal_directory,
                 require_journal_pin=True):
        if type(guards) is not RenameGuards:
            raise ValueError('publish_move_objects')
        if not callable(authority_check):
            raise ValueError('publish_move_authority_check')
        value = copy.deepcopy(value)
        generation(value, project_root=project_root)
        root = Path(project_root).resolve(strict=True)
        if guards.root != root:
            raise ValueError('publish_move_fresh_generation')
        if guards.nonce != value['publish_nonce']:
            raise ValueError('publish_move_guard_generation')
        expected_journal = root / '.local-tools' / ('storage-publish-' + value['publish_nonce']) / 'journal'
        if Path(journal_directory).absolute() != expected_journal:
            raise ValueError('publish_move_journal_binding')
        if require_journal_pin and expected_journal not in guards.directories:
            raise ValueError('publish_move_journal_directory_unpinned')
        if Path(value['paths']['source_main']) != root / 'CORE' / 'data' / 'imc-main.db':
            raise ValueError('publish_move_source_binding')
        if (Path(value['paths']['work_main']) != root / '.local-tools' /
                ('offline-audit-' + value['copy_nonce']) / 'work' / 'imc-main.db'
                or Path(value['paths']['archive_main']) != expected_journal.parent / 'original' / 'imc-main.db'
                or Path(value['paths']['retained_new_main']) != expected_journal.parent / 'retained-new.db'):
            raise ValueError('publish_move_layout_binding')
        expected = {k for k, member in value['members'].items() if member is not None}
        if set(guards.members) != expected:
            raise ValueError('publish_move_member_inventory')
        for key in expected:
            if guards.members[key]['path'] != Path(value['paths'][key]):
                raise ValueError('publish_move_member_path')
        destinations = {Path(value['paths'][dest]) for _, _, dest in MOVES}
        if not destinations.issubset(set(guards.destinations)):
            raise ValueError('publish_move_destination_inventory')
        self.guards = guards
        self._value = value
        self.authority_check = authority_check
        self.consumed = False
        self._moved_sources = []
        self._installed = False

    @property
    def value(self):
        return copy.deepcopy(self._value)

    def _check(self):
        self.guards.check()
        # No coercion of a returned truthy string or numeric value.
        if self.authority_check() is not True:
            raise ValueError('publish_move_authority_lost')
        self.guards.assert_bound()
        for key in self._moved_sources:
            if key == 'source_main' and self._installed:
                continue  # Canonical Main now holds the guarded WORK identity.
            self._absent(self._value['paths'][key])
        self._sidecars()

    def _absent(self, path):
        if os.path.lexists(path):
            raise ValueError('publish_move_unexpected_file')

    def _sidecars(self):
        paths = self.value['paths']
        for key in ('source_main', 'work_main'):
            self._absent(paths[key] + '-journal')
        for suffix in ('-wal', '-shm'):
            self._absent(paths['work_main'] + suffix)
        for key in ('source_wal', 'source_shm'):
            if self.value['members'][key] is None:
                self._absent(paths[key])

    def _proof(self, key, destination, *, done=False):
        result = {'source': self.value['paths'][key],
                  'destination': self.value['paths'][destination],
                  'identity': copy.deepcopy(self.value['members'][key])}
        if done:
            self._absent(result['source'])
            missing = result['identity'] is None
            if missing:
                self._absent(result['destination'])
            result.update(source_absent=True, destination_absent=missing)
        return result

    def preflight(self):
        self._check()
        self._sidecars()
        for _, _, destination in MOVES[:3]:
            self._absent(self.value['paths'][destination])
        # All native identities AND held-file bytes before the first intent.
        for key, expected in self.value['members'].items():
            if expected is None:
                continue
            observed = self.guards.members[key]['identity']
            if observed != {k: expected[k] for k in ('volume', 'file_id', 'bytes')}:
                raise ValueError('publish_move_identity_mismatch')
            if self.guards.digest(key) != expected['sha256']:
                raise ValueError('publish_move_hash_mismatch')


class MovePhase(MoveChecks):
    """Legacy single-use primitive, retaining its own journal contract."""
    def __init__(self, journal, guards, *, project_root, authority_check):
        if type(journal) is not Journal or type(guards) is not RenameGuards:
            raise ValueError('publish_move_objects')
        if journal.next_step != 0 or journal.pending is not None:
            raise ValueError('publish_move_fresh_generation')
        super().__init__(journal.value,guards,project_root=project_root,
                         authority_check=authority_check,journal_directory=journal.directory)
        self.journal=journal

    def run(self):
        if self.consumed:
            raise ValueError('publish_move_consumed')
        self.consumed = True
        self.preflight()
        for step, key, destination in MOVES:
            self._check()
            self._sidecars()
            self._absent(self.value['paths'][destination])
            if self.value['members'][key] is None:
                self._absent(self.value['paths'][key])
            self.journal.intent(step, self._proof(key, destination))
            # A lost parent after fsync must not permit the native rename.
            self._check()
            if self.value['members'][key] is not None:
                self.guards.move(key, self.value['paths'][destination])
            if step == 'INSTALL_MAIN':
                self._installed = True
            self._moved_sources.append(key)
            self._check()
            self.journal.done(step, self._proof(key, destination, done=True))
        for key in ('source_wal', 'source_shm'):
            self._absent(self.value['paths'][key])
        self._absent(self.value['paths']['installed_main'] + '-journal')
        self._check()
        return {'phase': 'MOVES_HELD', 'publish_nonce': self.value['publish_nonce'],
                'last_journal_sha256': self.journal.previous,
                'completed_steps': 4, 'native_handles_closed': False,
                'parent_strong_handoff': False, 'restart_allowed': False}
