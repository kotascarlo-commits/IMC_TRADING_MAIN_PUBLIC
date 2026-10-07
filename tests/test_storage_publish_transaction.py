"""Native temp-only move phase, with a genuinely validated tiny copy.

No production maintenance authority or parent handoff is asserted here.
"""
import json
import os
from pathlib import Path
import sqlite3
import sys
import subprocess
import time

import pytest
from test_execution import flow
from mykonos_meme_scout.store import SCHEMA

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import storage_copy_contract as contract
import storage_copy_worker as worker
import storage_publish_guard as native
import storage_publish_manifest as manifest
import storage_publish_transaction as transaction

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='native NTFS move fixtures')


@pytest.fixture(params=[False, True], ids=['no-wal', 'retained-wal'])
def held(tmp_path, flow, monkeypatch, request):
    root = tmp_path
    source = root / 'CORE/data/imc-main.db'
    source.parent.mkdir(parents=True)
    if request.param:
        # The isolated writer exits without SQLite shutdown, retaining a valid
        # WAL/SHM pair. Never target a production database with this fixture.
        code = """
import os,sqlite3,sys
sys.path.insert(0,sys.argv[2])
from mykonos_meme_scout.store import SCHEMA
c=sqlite3.connect(sys.argv[1]);c.executescript(SCHEMA)
c.execute("INSERT INTO snapshots VALUES(19,'column','a','s',1,'raw payload')")
c.commit();os._exit(0)
"""
        child = subprocess.run([sys.executable, '-I', '-c', code, str(source),
                                str(Path(__file__).parents[1] / 'CORE')], timeout=10)
        assert child.returncode == 0 and Path(str(source) + '-wal').exists()
    else:
        with sqlite3.connect(source) as c:
            c.executescript(SCHEMA)
            c.execute("INSERT INTO snapshots VALUES(19,'column','a','s',1,'raw payload')")
        c.close()
    scope = root / '.local-tools' / ('offline-audit-' + 'a' * 32)
    scope.mkdir(parents=True)
    gate = root / 'gate.json'
    gate.write_text(json.dumps({'schema_version': 1, 'OWNER_BROADCAST_GATE': 'OFF'}))
    report = {'schema_version': 1, 'nonce': 'a' * 32, 'pid': os.getpid(),
              'phase': 'RUNNING', 'complete': False, 'cleanup_confirmed': False,
              'budget_seconds': 7200.}
    monkeypatch.setattr(worker, 'ack', lambda *args: None)
    worker.frozen_copy(source, scope, flow.store.path, gate, report,
                       scope / 'report.json', scope / 'raw-ack', scope / 'end-ack',
                       time.monotonic() + 15)
    report.update(phase='FINAL', stage='FINAL', complete=True, cleanup_confirmed=True)
    result = contract.validate_final(report, source=source, scope=scope,
        nonce='a' * 32, worker_pid=os.getpid(), exit_code=0, deadline=time.monotonic() + 15)
    assert result['complete'] is True and result['eligible_for_publication'] is False
    publication = root / '.local-tools' / ('storage-publish-' + 'b' * 32)
    archive = publication / 'original' / source.name
    archive.parent.mkdir(parents=True)
    paths = {'source_main': str(source), 'source_wal': str(source) + '-wal',
             'source_shm': str(source) + '-shm', 'work_main': str(scope / 'work' / source.name),
             'archive_main': str(archive), 'archive_wal': str(archive) + '-wal',
             'archive_shm': str(archive) + '-shm', 'installed_main': str(source),
             'retained_new_main': str(publication / 'retained-new.db')}
    members = {k: Path(paths[k]) for k in ('source_main', 'source_wal', 'source_shm', 'work_main')
               if Path(paths[k]).exists()}
    # Guard hashes are checked against builder/independent-verifier evidence.
    values = {k: None for k in ('source_main', 'source_wal', 'source_shm', 'work_main')}
    journal_path = publication / 'journal'
    # Journal creation precedes guard pinning; its generated file also binds
    # the existing directory into RenameGuards' pinned inventory.
    identities = {}
    for key, path in members.items():
        with native.RenameGuards(root, {key: path}, deadline=time.monotonic() + 15) as probe:
            identities[key] = {**probe.members[key]['identity'], 'sha256': probe.digest(key)}
    assert identities['source_main']['sha256'] == report['source_sha256']['main']
    assert identities['work_main']['sha256'] == report['target_sha256']
    values.update(identities)
    value = {'copy_nonce': 'a' * 32, 'publish_nonce': 'b' * 32,
             'root': str(root), 'paths': paths, 'members': values}
    journal = manifest.Journal(journal_path, value, project_root=str(root))
    destinations = [Path(paths[k]) for k in ('archive_main', 'archive_wal', 'archive_shm', 'installed_main')]
    destinations.append(journal_path / 'generation.json')
    with native.RenameGuards(root, members, deadline=time.monotonic() + 30,
                            destinations=destinations, nonce='b' * 32) as guards:
        phase = transaction.MovePhase(journal, guards, project_root=str(root), authority_check=lambda: True)
        yield phase, paths, report, result


def test_four_native_moves_hold_every_original_and_never_release(held):
    phase, paths, _, _ = held
    original = phase.guards.digest('source_main')
    compact = phase.guards.digest('work_main')
    result = phase.run()
    assert result['phase'] == 'MOVES_HELD' and result['completed_steps'] == 4
    assert not result['native_handles_closed'] and not result['parent_strong_handoff']
    assert not result['restart_allowed']
    assert phase.guards.digest('source_main') == original
    assert phase.guards.digest('work_main') == compact
    assert phase.guards.members['source_main']['path'] == Path(paths['archive_main'])
    assert phase.guards.members['work_main']['path'] == Path(paths['installed_main'])
    assert not Path(paths['work_main']).exists()
    chain = manifest.Journal.load(phase.journal.directory, project_root=phase.value['root'])
    assert chain['completed_steps'] == 4 and not chain['complete']
    assert chain['pending_step'] is None
    assert result['last_journal_sha256'] == phase.journal.previous
    with pytest.raises(ValueError, match='consumed'):
        phase.run()


@pytest.mark.parametrize('step', [row[0] for row in transaction.MOVES])
@pytest.mark.parametrize('where', ['before_intent', 'after_intent', 'after_rename', 'after_done'])
def test_fault_between_every_step_never_retries_or_releases(held, monkeypatch, step, where):
    phase, paths, _, _ = held
    before = {k: phase.guards.digest(k) for k in ('source_main', 'work_main')}
    original_intent, original_done = phase.journal.intent, phase.journal.done
    def intent(actual, proof):
        if actual == step and where == 'before_intent': raise OSError('fixture')
        original_intent(actual, proof)
        if actual == step and where == 'after_intent': raise OSError('fixture')
    def done(actual, proof):
        if actual == step and where == 'after_rename': raise OSError('fixture')
        original_done(actual, proof)
        if actual == step and where == 'after_done': raise OSError('fixture')
    monkeypatch.setattr(phase.journal, 'intent', intent)
    monkeypatch.setattr(phase.journal, 'done', done)
    with pytest.raises(OSError, match='fixture'): phase.run()
    with pytest.raises(ValueError, match='consumed'): phase.run()
    chain = manifest.Journal.load(phase.journal.directory, project_root=phase.value['root'])
    assert not chain['complete'] and chain['completed_steps'] <= 4
    phase.guards.assert_bound()
    for key, digest in before.items():
        assert phase.guards.digest(key) == digest
    assert not (phase.journal.directory / '08-INTENT.json').exists()


@pytest.mark.parametrize('value', [False, 1, 'true', None])
def test_authority_loss_before_any_intent_has_no_file_effect(held, value):
    phase, paths, _, _ = held
    phase.authority_check = lambda: value
    with pytest.raises(ValueError, match='authority_lost'): phase.run()
    assert Path(paths['source_main']).exists() and Path(paths['work_main']).exists()
    assert not (phase.journal.directory / '00-INTENT.json').exists()


def test_hash_mismatch_before_any_intent(held):
    phase, _, _, _ = held
    phase._value['members']['work_main']['sha256'] = '0' * 64
    with pytest.raises(ValueError, match='hash_mismatch'): phase.run()
    assert not (phase.journal.directory / '00-INTENT.json').exists()


def test_external_value_mutation_cannot_change_generation(held):
    phase, _, _, _ = held
    value = phase.value
    value['members']['source_main']['file_id'] = 0
    value['paths']['installed_main'] = 'C:/outside.db'
    phase.run()
    assert phase.value['members']['source_main']['file_id'] != 0


@pytest.mark.parametrize('key', ['source_wal', 'source_shm', 'source_main'])
def test_archived_source_reappearance_blocks_next_move(held, monkeypatch, key):
    phase, paths, _, _ = held
    original_done = phase.journal.done
    mapping = {source: step for step, source, _ in transaction.MOVES}
    def done(step, proof):
        original_done(step, proof)
        if step == mapping[key]:
            Path(paths[key]).write_bytes(b'unexpected replacement')
    monkeypatch.setattr(phase.journal, 'done', done)
    with pytest.raises(ValueError, match='unexpected_file'): phase.run()
    assert Path(paths[key]).read_bytes() == b'unexpected replacement'
    assert Path(paths['work_main']).exists()
    assert not (phase.journal.directory / '06-INTENT.json').exists()


@pytest.mark.parametrize('where', ['after_intent', 'after_rename', 'after_done'])
@pytest.mark.parametrize('suffix', ['-wal', '-shm', '-journal'])
def test_work_sidecar_in_every_install_window_blocks_success(held, monkeypatch, where, suffix):
    phase, paths, _, _ = held
    unexpected = Path(paths['work_main'] + suffix)
    original_intent, original_done, original_move = phase.journal.intent, phase.journal.done, phase.guards.move
    def intent(step, proof):
        original_intent(step, proof)
        if step == 'INSTALL_MAIN' and where == 'after_intent': unexpected.write_bytes(b'unknown')
    def move(key, destination):
        original_move(key, destination)
        if key == 'work_main' and where == 'after_rename': unexpected.write_bytes(b'unknown')
    def done(step, proof):
        original_done(step, proof)
        if step == 'INSTALL_MAIN' and where == 'after_done': unexpected.write_bytes(b'unknown')
    monkeypatch.setattr(phase.journal, 'intent', intent)
    monkeypatch.setattr(phase.journal, 'done', done)
    monkeypatch.setattr(phase.guards, 'move', move)
    with pytest.raises(ValueError, match='unexpected_file'): phase.run()
    phase.guards.assert_bound()
    assert unexpected.read_bytes() == b'unknown'
    assert not (phase.journal.directory / '08-INTENT.json').exists()
    with pytest.raises(ValueError, match='consumed'): phase.run()


@pytest.mark.parametrize('where', ['after_intent', 'after_rename'])
def test_authority_loss_after_intent_or_rename_retains_guards(held, monkeypatch, where):
    phase, _, _, _ = held
    authorized = [True]
    phase.authority_check = lambda: authorized[0]
    original_intent, original_move = phase.journal.intent, phase.guards.move
    def intent(step, proof):
        original_intent(step, proof)
        if step == 'ARCHIVE_MAIN' and where == 'after_intent': authorized[0] = False
    def move(key, destination):
        original_move(key, destination)
        if key == 'source_main' and where == 'after_rename': authorized[0] = False
    monkeypatch.setattr(phase.journal, 'intent', intent)
    monkeypatch.setattr(phase.guards, 'move', move)
    with pytest.raises(ValueError, match='authority_lost'): phase.run()
    phase.guards.assert_bound()
    result = manifest.Journal.load(phase.journal.directory, project_root=phase.value['root'])
    assert result['pending_step'] == 'ARCHIVE_MAIN'
    assert result['completed_steps'] == 2 and not result['complete']
    assert not (phase.journal.directory / '05-DONE.json').exists()


@pytest.mark.parametrize('key,suffix', [('source_main', '-journal'), ('work_main', '-wal'), ('work_main', '-shm')])
def test_unexpected_sidecar_before_any_move_is_retained(held, key, suffix):
    phase, paths, _, _ = held
    unexpected = Path(paths[key] + suffix)
    unexpected.write_bytes(b'unknown')
    with pytest.raises(ValueError, match='unexpected_file'): phase.run()
    assert unexpected.read_bytes() == b'unknown'
    assert not (phase.journal.directory / '00-INTENT.json').exists()
