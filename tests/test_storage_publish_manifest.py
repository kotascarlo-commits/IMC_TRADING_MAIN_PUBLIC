"""Journal primitives only; no database move or production maintenance."""
import copy
import json
from pathlib import Path, PureWindowsPath
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import storage_publish_manifest as manifest


@pytest.fixture
def generation():
    root = PureWindowsPath('C:/IMC')
    source = root / 'CORE/data/imc-main.db'
    archive = root / '.local-tools/publish/original/imc-main.db'
    work = root / '.local-tools/copy/work/imc-main.db'
    paths = {'source_main': str(source), 'source_wal': str(source) + '-wal',
             'source_shm': str(source) + '-shm', 'work_main': str(work),
             'archive_main': str(archive), 'archive_wal': str(archive) + '-wal',
             'archive_shm': str(archive) + '-shm', 'installed_main': str(source),
             'retained_new_main': str(archive.parent / 'retained-new.db')}
    return {'copy_nonce': 'a' * 32, 'publish_nonce': 'b' * 32, 'root': str(root),
            'paths': paths, 'members': {
        key: {'volume': 5, 'file_id': i, 'bytes': i * 4096, 'sha256': str(i) * 64}
        for i, key in enumerate(('source_main', 'source_wal', 'source_shm', 'work_main'), 1)}}


def test_root_traversal_rejected_before_journal_creation(tmp_path, generation):
    value = copy.deepcopy(generation)
    value['root'] = 'C:/IMC/../outside'
    value['paths'] = {k: v.replace('C:\\IMC', 'C:\\IMC\\..\\outside')
                      for k, v in value['paths'].items()}
    directory = tmp_path / 'journal'
    with pytest.raises(ValueError, match='journal_root'):
        manifest.Journal(directory, value, project_root='C:/IMC')
    assert not directory.exists()


def test_independent_project_root_required_on_create_and_load(tmp_path, generation):
    directory = tmp_path / 'journal'
    with pytest.raises(ValueError, match='project_root'):
        manifest.Journal(directory, generation, project_root='C:/OTHER')
    assert not directory.exists()
    manifest.Journal(directory, generation, project_root='C:/IMC')
    with pytest.raises(ValueError, match='project_root'):
        manifest.Journal.load(directory, project_root='C:/OTHER')


def proof(value, kind, step):
    mapping = {'ARCHIVE_SHM': ('source_shm', 'archive_shm'),
               'ARCHIVE_WAL': ('source_wal', 'archive_wal'),
               'ARCHIVE_MAIN': ('source_main', 'archive_main'),
               'INSTALL_MAIN': ('work_main', 'installed_main')}
    if step in mapping:
        source, target = mapping[step]
        result = {'source': value['paths'][source], 'destination': value['paths'][target],
                  'identity': copy.deepcopy(value['members'][source])}
        if kind == 'DONE':
            result.update(source_absent=True, destination_absent=value['members'][source] is None)
        return result
    result = {'installed_main': value['paths']['installed_main'],
              'identity': copy.deepcopy(value['members']['work_main'])}
    if kind == 'DONE':
        if step == 'VERIFY_INSTALLED':
            result.update(integrity=True, schema=True, canonical_sidecars_absent=True)
        else:
            result.update(native_cleanup=True, integrity=True, safety=True, absence=True)
    return result


def test_pending_intent_never_becomes_completion(tmp_path, generation):
    journal = manifest.Journal(tmp_path / 'journal', generation, project_root='C:/IMC')
    journal.intent('ARCHIVE_SHM', proof(generation, 'INTENT', 'ARCHIVE_SHM'))
    value = manifest.Journal.load(journal.directory, project_root='C:/IMC')
    assert value['pending_step'] == 'ARCHIVE_SHM'
    assert value['completed_steps'] == 0 and not value['complete']
    for action in (lambda: journal.intent('ARCHIVE_WAL', {}),
                   lambda: journal.done('ARCHIVE_MAIN', {})):
        with pytest.raises(ValueError, match='transition'):
            action()


def test_all_ordered_events_and_generation_copy(tmp_path, generation):
    journal = manifest.Journal(tmp_path / 'journal', generation, project_root='C:/IMC')
    generation['members']['source_main']['bytes'] = 0
    for step in manifest.STEPS:
        journal.intent(step, proof(journal.value, 'INTENT', step))
        assert manifest.Journal.load(journal.directory, project_root='C:/IMC')['pending_step'] == step
        journal.done(step, proof(journal.value, 'DONE', step))
    value = manifest.Journal.load(journal.directory, project_root='C:/IMC')
    assert value['complete'] and value['pending_step'] is None
    assert value['completed_steps'] == len(manifest.STEPS)
    assert value['generation']['members']['source_main']['bytes'] == 4096
    with pytest.raises(ValueError, match='transition'):
        journal.intent('RELEASE', {})


@pytest.mark.parametrize('change', ['extra', 'sequence_bool', 'previous', 'nonce', 'step', 'schema_bool'])
def test_corrupt_or_conflicting_event_fails(tmp_path, generation, change):
    journal = manifest.Journal(tmp_path / 'journal', generation, project_root='C:/IMC')
    journal.intent('ARCHIVE_SHM', proof(generation, 'INTENT', 'ARCHIVE_SHM'))
    path = journal.directory / '00-INTENT.json'
    value = json.loads(path.read_text())
    if change == 'extra':
        value['extra'] = True
    elif change == 'sequence_bool':
        value['sequence'] = False
    elif change == 'schema_bool':
        value['schema_version'] = True
    else:
        field = {'previous': 'previous_sha256', 'nonce': 'publish_nonce', 'step': 'step'}[change]
        value[field] = 'conflict'
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='chain'):
        manifest.Journal.load(journal.directory, project_root='C:/IMC')


@pytest.mark.parametrize('name', ['01-DONE.json', '02-INTENT.json', 'extra.json', '00-INTENT.json.tmp'])
def test_unknown_inventory_or_completion_without_intent_fails(tmp_path, generation, name):
    journal = manifest.Journal(tmp_path / 'journal', generation, project_root='C:/IMC')
    (journal.directory / name).write_text('{}')
    with pytest.raises(ValueError, match='inventory'):
        manifest.Journal.load(journal.directory, project_root='C:/IMC')


@pytest.mark.parametrize('body', [b'{"k":1,"k":2}', b'{"a":{"k":1,"k":2}}',
                                b'{"a":NaN}', b'{"a":1e999}', b'{"a":-Infinity}',
                                b'x' * (manifest.MAX_BYTES + 1)],
                         ids=['duplicate', 'nested_duplicate', 'nan', 'overflow', 'infinity', 'oversize'])
def test_strict_bounded_json(body):
    with pytest.raises(ValueError):
        manifest.decode(body)


@pytest.mark.parametrize('change', ['bool_id', 'negative_bytes', 'bad_hash', 'alias',
                                  'other_volume', 'missing_main', 'same_nonce', 'extra'])
def test_invalid_generation_fails_before_directory_creation(tmp_path, generation, change):
    value = copy.deepcopy(generation)
    members = value['members']
    if change == 'bool_id': members['work_main']['file_id'] = True
    elif change == 'negative_bytes': members['work_main']['bytes'] = -1
    elif change == 'bad_hash': members['work_main']['sha256'] = '0' * 63
    elif change == 'alias': members['work_main'] = copy.deepcopy(members['source_main'])
    elif change == 'other_volume': members['work_main']['volume'] = 6
    elif change == 'missing_main': members['source_main'] = None
    elif change == 'same_nonce': value['publish_nonce'] = value['copy_nonce']
    else: value['extra'] = True
    directory = tmp_path / 'journal'
    with pytest.raises(ValueError): manifest.Journal(directory, value, project_root='C:/IMC')
    assert not directory.exists()


def test_fsync_failure_keeps_uncertain_record_without_retry(tmp_path, generation, monkeypatch):
    journal = manifest.Journal(tmp_path / 'journal', generation, project_root='C:/IMC')
    calls = []
    def failed(fd):
        calls.append(fd)
        raise OSError('fixture')
    monkeypatch.setattr(manifest.os, 'fsync', failed)
    with pytest.raises(OSError): journal.intent('ARCHIVE_SHM', proof(generation, 'INTENT', 'ARCHIVE_SHM'))
    assert len(calls) == 1 and (journal.directory / '00-INTENT.json').exists()
    assert journal.pending is None
    with pytest.raises(FileExistsError): journal.intent('ARCHIVE_SHM', proof(generation, 'INTENT', 'ARCHIVE_SHM'))
    assert len(calls) == 1


def test_existing_directory_never_reused(tmp_path, generation):
    directory = tmp_path / 'journal'
    directory.mkdir()
    with pytest.raises(FileExistsError): manifest.Journal(directory, generation, project_root='C:/IMC')


def test_exact_native_guard_sentinel_is_only_structural_metadata(tmp_path, generation):
    journal = manifest.Journal(tmp_path / 'journal', generation, project_root='C:/IMC')
    sentinel = journal.directory / ('.imc-publish-guard-' + generation['publish_nonce'] + '.sentinel')
    sentinel.write_bytes(b'')
    result = manifest.Journal.load(journal.directory, project_root='C:/IMC')
    assert result['completed_steps'] == 0 and not result['complete']
    sentinel.write_bytes(b'not empty')
    with pytest.raises(ValueError, match='sentinel'):
        manifest.Journal.load(journal.directory, project_root='C:/IMC')


def test_wrong_generation_sentinel_is_unknown_inventory(tmp_path, generation):
    journal = manifest.Journal(tmp_path / 'journal', generation, project_root='C:/IMC')
    (journal.directory / ('.imc-publish-guard-' + 'c' * 32 + '.sentinel')).write_bytes(b'')
    with pytest.raises(ValueError, match='inventory'):
        manifest.Journal.load(journal.directory, project_root='C:/IMC')


def test_absent_original_sidecars_are_explicit(tmp_path, generation):
    generation['members'].update(source_wal=None, source_shm=None)
    journal = manifest.Journal(tmp_path / 'journal', generation, project_root='C:/IMC')
    assert manifest.Journal.load(journal.directory, project_root='C:/IMC')['generation']['members']['source_wal'] is None


@pytest.mark.parametrize('change', ['destination', 'source', 'bool_id', 'hash', 'extra'])
def test_move_intent_must_bind_exact_generation_before_write(tmp_path, generation, change):
    journal = manifest.Journal(tmp_path / 'journal', generation, project_root='C:/IMC')
    value = proof(generation, 'INTENT', 'ARCHIVE_SHM')
    if change in ('destination', 'source'):
        value[change] = str(PureWindowsPath('C:/IMC/other.db'))
    elif change == 'bool_id': value['identity']['file_id'] = True
    elif change == 'hash': value['identity']['sha256'] = '0' * 64
    else: value['extra'] = True
    with pytest.raises(ValueError): journal.intent('ARCHIVE_SHM', value)
    assert not (journal.directory / '00-INTENT.json').exists()


@pytest.mark.parametrize('change', ['source_present', 'destination_absent', 'bool_truth', 'missing_guard'])
def test_completion_requires_typed_absence_proof(tmp_path, generation, change):
    journal = manifest.Journal(tmp_path / 'journal', generation, project_root='C:/IMC')
    journal.intent('ARCHIVE_SHM', proof(generation, 'INTENT', 'ARCHIVE_SHM'))
    value = proof(generation, 'DONE', 'ARCHIVE_SHM')
    if change == 'source_present': value['source_absent'] = False
    elif change == 'destination_absent': value['destination_absent'] = True
    elif change == 'bool_truth': value['source_absent'] = 1
    else: value.pop('source_absent')
    with pytest.raises(ValueError): journal.done('ARCHIVE_SHM', value)
    assert manifest.Journal.load(journal.directory, project_root='C:/IMC')['pending_step'] == 'ARCHIVE_SHM'


@pytest.mark.parametrize('change', ['outside', 'source_alias', 'wrong_wal', 'different_install', 'traversal'])
def test_bound_paths_reject_generation_mix_and_escape(tmp_path, generation, change):
    value = copy.deepcopy(generation)
    paths = value['paths']
    if change == 'outside': paths['archive_main'] = 'D:/outside/imc-main.db'
    elif change == 'source_alias': paths['work_main'] = paths['source_main']
    elif change == 'wrong_wal': paths['source_wal'] = str(PureWindowsPath('C:/IMC/foreign-wal'))
    elif change == 'different_install': paths['installed_main'] = str(PureWindowsPath('C:/IMC/other.db'))
    else: paths['archive_main'] = 'C:/IMC/../outside/imc-main.db'
    with pytest.raises(ValueError): manifest.Journal(tmp_path / 'journal', value, project_root='C:/IMC')
    assert not (tmp_path / 'journal').exists()
