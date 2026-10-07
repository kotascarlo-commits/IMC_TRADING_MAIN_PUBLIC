"""Pure wire contracts: no native authority, database or runtime changes."""
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
from test_storage_publish_manifest import generation, proof
from test_storage_publish_worker import envelope
from storage_publish_frames import journal_request, worker_boot
from storage_publish_manifest import decode, encoded, STEPS


def encode_boot(value, **changes):
    expected = dict(project_root='C:/IMC', role=value['role'], nonce='b' * 32,
                    parent_pid=10, parent_birth=100, worker_pid=20,
                    worker_birth=200, deadline_binding=(5000, 1000))
    expected.update(changes)
    return worker_boot(value, **expected)


@pytest.mark.parametrize('role', ['journal', 'rename'])
def test_boot_only_calculates_hashes_without_mutating_input(generation, role):
    value = envelope(generation, role)
    value['binding']['generation_sha256'] = '0' * 64
    value['binding']['envelope_sha256'] = '0' * 64
    original = copy.deepcopy(value)
    result = decode(encode_boot(value))
    assert value == original
    result['binding']['generation_sha256'] = '0' * 64
    result['binding']['envelope_sha256'] = '0' * 64
    assert result == original


@pytest.mark.parametrize('field,wrong', [
    ('parent_pid', 11), ('worker_birth', 201), ('nonce', 'c' * 32),
    ('role', 'rename'), ('deadline_binding', (5001, 1000)),
    ('parent_pid', 10.0), ('parent_birth', True),
    ('deadline_binding', (5000.0, 1000)), ('deadline_binding', [5000, 1000]),
])
def test_boot_rejects_mismatched_or_untyped_independent_bindings(generation, field, wrong):
    with pytest.raises(ValueError):
        encode_boot(envelope(generation), **{field: wrong})


def test_boot_does_not_repair_wrong_schema_or_extra_claims(generation):
    value = envelope(generation)
    value['schema_version'] = True
    with pytest.raises(ValueError):
        encode_boot(value)
    value['schema_version'] = 2
    value['authority']['restart_allowed'] = True
    with pytest.raises(ValueError):
        encode_boot(value)


def encode_journal(generation, *, index=0, evidence=None, **changes):
    binding = envelope(generation)['binding']
    evidence_index = index if type(index) is int and 0 <= index < 12 else 0
    kind, step = ('DONE' if evidence_index % 2 else 'INTENT'), STEPS[evidence_index // 2]
    if evidence is None:
        evidence = proof(generation, kind, step)
        if index == 11:
            evidence['source_guard_cleanup'] = evidence.pop('native_cleanup')
    args = dict(project_root='C:/IMC', index=index, previous_sha256='d' * 64,
                request_nonce='e' * 32, evidence=evidence)
    args.update(changes)
    return journal_request(generation, binding, **args)


@pytest.mark.parametrize('index', range(12))
def test_all_journal_requests_preserve_explicit_evidence(generation, index):
    original = copy.deepcopy(generation)
    body = encode_journal(generation, index=index)
    value = decode(body)
    assert body == encoded(value) and generation == original
    assert value['ipc_sequence'] == index + 1 and value['journal_sequence'] == index
    expected = proof(generation, value['event_kind'], value['step'])
    if index == 11:
        expected['source_guard_cleanup'] = expected.pop('native_cleanup')
    assert value['evidence'] == expected


@pytest.mark.parametrize('index,field', [
    (1, 'source_absent'), (9, 'integrity'), (9, 'schema'),
    (9, 'canonical_sidecars_absent'), (11, 'source_guard_cleanup'),
    (11, 'integrity'), (11, 'safety'), (11, 'absence'),
])
@pytest.mark.parametrize('missing', [True, False])
def test_success_is_never_invented(generation, index, field, missing):
    value = proof(generation, 'DONE', STEPS[index // 2])
    if index == 11:
        value['source_guard_cleanup'] = value.pop('native_cleanup')
    if missing:
        del value[field]
    else:
        value[field] = False
    with pytest.raises(ValueError):
        encode_journal(generation, index=index, evidence=value)


@pytest.mark.parametrize('field,wrong', [
    ('index', True), ('index', -1), ('index', 12), ('index', 0.0),
    ('previous_sha256', 'D' * 64), ('previous_sha256', None),
    ('request_nonce', 'e' * 31), ('request_nonce', 1),
])
def test_journal_rejects_untyped_sequences_and_hashes(generation, field, wrong):
    with pytest.raises(ValueError):
        encode_journal(generation, **{field: wrong})


@pytest.mark.parametrize('field', ['generation_sha256', 'publish_nonce', 'role'])
def test_journal_generation_and_context_are_bound(generation, field):
    binding = envelope(generation)['binding']
    binding[field] = {'generation_sha256': 'f' * 64, 'publish_nonce': 'c' * 32,
                      'role': 'rename'}[field]
    with pytest.raises(ValueError):
        journal_request(generation, binding, project_root='C:/IMC', index=0,
                        previous_sha256='d' * 64, request_nonce='e' * 32,
                        evidence=proof(generation, 'INTENT', 'ARCHIVE_SHM'))


def test_encoded_nested_evidence_has_no_caller_alias(generation):
    value = proof(generation, 'INTENT', 'ARCHIVE_SHM')
    original = copy.deepcopy(value)
    body = encode_journal(generation, evidence=value)
    assert value == original
    value['identity']['file_id'] = 999
    generation['members']['source_shm']['file_id'] = 888
    assert decode(body)['evidence'] == original
