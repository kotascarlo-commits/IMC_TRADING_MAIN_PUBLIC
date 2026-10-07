"""Exact role inventories selected from already held native identities."""
import copy
from pathlib import Path, PureWindowsPath
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
from test_storage_publish_manifest import generation
from storage_publish_frames import namespace_for_role


def held(value):
    root = PureWindowsPath(value['root'])
    leaves = {PureWindowsPath(path).parent for path in value['paths'].values()}
    leaves |= {root / 'raw', root / 'RUNTIME', root / '.local-tools' /
               ('storage-publish-' + value['publish_nonce']) / 'journal'}
    directories = {str(parent) for leaf in leaves for parent in (leaf, *leaf.parents)}
    result = {'directories': {}, 'sentinels': {}}
    number = 100
    for kind, paths in [('directories', sorted(directories)),
                        ('sentinels', sorted(str(leaf / ('.imc-publish-guard-' + value['publish_nonce'] + '.sentinel')) for leaf in leaves))]:
        for path in paths:
            result[kind][path] = {'volume': 5, 'file_id': number}
            if kind == 'sentinels':
                result[kind][path]['bytes'] = 0
            number += 1
    return result


def select(value, native, role='journal', **changes):
    args = dict(project_root=value['root'], role=role)
    args.update(changes)
    return namespace_for_role(native, value, **args)


@pytest.mark.parametrize('role', ['journal', 'rename'])
def test_exact_subset_preserves_original_values_and_excludes_other_leaves(generation, role):
    native = held(generation)
    original = copy.deepcopy(native)
    result = select(generation, native, role)
    assert native == original
    assert not any('\\raw' in key or '\\RUNTIME' in key for values in result.values() for key in values)
    if role == 'journal':
        leaf = PureWindowsPath(generation['root']) / '.local-tools' / ('storage-publish-' + 'b' * 32) / 'journal'
        assert set(result['directories']) == {str(leaf), *(str(path) for path in leaf.parents)}
        assert set(result['sentinels']) == {str(leaf / ('.imc-publish-guard-' + 'b' * 32 + '.sentinel'))}
    else:
        root = PureWindowsPath(generation['root'])
        leaves = {root / 'CORE' / 'data', root / '.local-tools' / 'copy' / 'work',
                  root / '.local-tools' / 'publish' / 'original'}
        assert set(result['directories']) == {str(parent) for leaf in leaves for parent in (leaf, *leaf.parents)}
        assert set(result['sentinels']) == {str(leaf / ('.imc-publish-guard-' + 'b' * 32 + '.sentinel')) for leaf in leaves}
    for kind, values in result.items():
        for path, identity in values.items():
            assert identity == original[kind][path]
            identity['file_id'] = 99999
    assert native == original


def test_absent_sidecars_still_require_all_destination_namespace(generation):
    generation['members']['source_wal'] = generation['members']['source_shm'] = None
    native = held(generation)
    result = select(generation, native, 'rename')
    archive = str(PureWindowsPath(generation['paths']['archive_main']).parent)
    assert archive in result['directories']
    del native['directories'][archive]
    with pytest.raises(ValueError, match='namespace_missing'):
        select(generation, native, 'rename')


def test_retained_new_main_does_not_expand_rename_namespace(generation):
    generation['paths']['retained_new_main'] = generation['root'] + '\\separate-retained\\retained-new.db'
    result = select(generation, held(generation), 'rename')
    assert not any('separate-retained' in path for values in result.values() for path in values)


@pytest.mark.parametrize('key', ['archive_shm', 'archive_wal'])
def test_sidecar_destinations_cannot_escape_the_archive_main_leaf(generation, key):
    generation['members']['source_wal'] = generation['members']['source_shm'] = None
    generation['paths'][key] = generation['root'] + '\\other-archive\\imc-main.db-' + key.removeprefix('archive_')
    with pytest.raises(ValueError, match='journal_path_binding'):
        select(generation, held(generation), 'rename')


@pytest.mark.parametrize('reverse', [False, True])
def test_conflicting_directory_spelling_is_rejected_independent_of_insertion_order(generation, reverse):
    generation['paths']['source_wal'] = generation['paths']['source_wal'].replace('CORE', 'core')
    if reverse:
        generation['paths'] = dict(reversed(list(generation['paths'].items())))
    with pytest.raises(ValueError, match='namespace_path_alias'):
        select(generation, held(generation), 'rename')


@pytest.mark.parametrize('change', ['Volume', 'ExtraField'])
def test_wrong_volume_and_extra_identity_claims_are_rejected(generation, change):
    native = held(generation)
    path = next(iter(select(generation, native)['sentinels']))
    if change == 'Volume':
        native['sentinels'][path]['volume'] = 6
    else:
        native['sentinels'][path]['health'] = 1
    with pytest.raises(ValueError, match='namespace_identity'):
        select(generation, native)


@pytest.mark.parametrize('kind', ['directories', 'sentinels'])
def test_missing_key_never_uses_alias_or_invents_identity(generation, kind):
    native = held(generation)
    needed = select(generation, native)[kind]
    path = next(iter(needed))
    native[kind][path.lower()] = native[kind].pop(path)
    with pytest.raises(ValueError, match='namespace_missing'):
        select(generation, native)


@pytest.mark.parametrize('field,wrong', [
    ('volume', True), ('volume', -1), ('volume', 2**64), ('volume', 5.0),
    ('file_id', True), ('file_id', 0), ('file_id', 2**128), ('file_id', '1'),
    ('bytes', False), ('bytes', 1), ('bytes', 0.0),
])
def test_selected_identities_have_exact_native_types_and_bounds(generation, field, wrong):
    native = held(generation)
    path = next(iter(select(generation, native)['sentinels']))
    native['sentinels'][path][field] = wrong
    with pytest.raises(ValueError, match='namespace_identity'):
        select(generation, native)


def test_unsigned_native_extremes_are_preserved_without_float_conversion(generation):
    for identity in generation['members'].values():
        identity['volume'] = 2**64 - 1
    native = held(generation)
    for identities in native.values():
        for identity in identities.values():
            identity['volume'] = 2**64 - 1
    path = next(iter(select(generation, native)['sentinels']))
    native['sentinels'][path]['file_id'] = 2**128 - 1
    assert select(generation, native)['sentinels'][path]['file_id'] == 2**128 - 1


@pytest.mark.parametrize('suffix', ['\\.', '\\..', '\\bad.', '\\bad ', '\\CON', '\\CON .txt',
                                  '\\COM¹', '\\LPT².log', '\\bad:stream', '\\bad?'])
def test_ambiguous_windows_paths_are_rejected(generation, suffix):
    native = held(generation)
    generation['paths']['retained_new_main'] += suffix
    with pytest.raises(ValueError):
        select(generation, native)


@pytest.mark.parametrize('root', ['C:/IMC', 'c:\\IMC', 'C:\\IMC\\', 'C:\\IMC\\.'])
def test_project_root_requires_exact_canonical_spelling(generation, root):
    with pytest.raises(ValueError):
        select(generation, held(generation), project_root=root)


def test_same_native_identity_cannot_represent_directory_and_sentinel(generation):
    native = held(generation)
    result = select(generation, native)
    path = next(iter(result['sentinels']))
    native['sentinels'][path]['file_id'] = next(iter(result['directories'].values()))['file_id']
    with pytest.raises(ValueError, match='namespace_alias'):
        select(generation, native)


def test_unselected_untrusted_objects_are_neither_copied_nor_serialized(generation):
    native = held(generation)
    native['directories']['NOT_A_SELECTED_KEY'] = object()
    assert 'NOT_A_SELECTED_KEY' not in select(generation, native)['directories']
