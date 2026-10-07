"""BOOT validation is pure; these tests acquire no native authority."""
import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
from test_storage_publish_manifest import generation
from storage_publish_manifest import encoded
from storage_publish_worker import boot
from storage_publish_writer import digest


def envelope(generation, role='journal'):
    value = {'schema_version': 2, 'kind': 'WORKER_BOOT', 'role': role,
             'generation': copy.deepcopy(generation),
             'authority': {'thread_id': 11, 'thread_birth': 111,
                           'marker_identity': {}, 'active_snapshot': {}},
             'namespace': {'directories': {}, 'sentinels': {}},
             'deadline': {'end_tick': 5000, 'frequency': 1000}}
    value['binding'] = {'publish_nonce': generation['publish_nonce'],
                        'generation_sha256': digest(encoded(generation)),
                        'envelope_sha256': digest(encoded(value)), 'role': role,
                        'parent_pid': 10, 'parent_birth': 100,
                        'worker_pid': 20, 'worker_birth': 200}
    return value


def validate(body, generation, role='journal', **changes):
    arguments = dict(role=role, nonce=generation['publish_nonce'], project_root='C:/IMC',
                     parent_pid=10, parent_birth=100, worker_pid=20, worker_birth=200,
                     deadline_binding=(5000, 1000))
    arguments.update(changes)
    return boot(body, **arguments)


@pytest.mark.parametrize('role', ['journal', 'rename'])
def test_canonical_boot_binds_role_and_native_identities(generation, role):
    value = envelope(generation, role)
    assert validate(encoded(value), generation, role) == value


@pytest.mark.parametrize('field,value', [('parent_pid', 21), ('parent_birth', 101),
                                     ('worker_pid', 21), ('worker_birth', 201),
                                     ('nonce', 'c' * 32), ('role', 'rename')])
def test_boot_cannot_override_native_admission(generation, field, value):
    with pytest.raises(ValueError):
        validate(encoded(envelope(generation)), generation, **{field: value})


@pytest.mark.parametrize('target', ['generation', 'envelope'])
def test_boot_rejects_unbound_digest(generation, target):
    value = envelope(generation)
    value['binding'][target + '_sha256'] = 'f' * 64
    with pytest.raises(ValueError, match='boot_digest'):
        validate(encoded(value), generation)


@pytest.mark.parametrize('target', ['root', 'binding', 'authority', 'namespace'])
def test_boot_rejects_additional_authority_or_fallback(generation, target):
    value = envelope(generation)
    destination = value if target == 'root' else value[target]
    destination['private_test_nonce' if target == 'binding' else 'relative_deadline'] = 1
    if target in ('authority', 'namespace'):
        value['binding']['envelope_sha256'] = digest(encoded({k: v for k, v in value.items() if k != 'binding'}))
    with pytest.raises(ValueError):
        validate(encoded(value), generation)


def test_boot_rejects_noncanonical_wire_bytes(generation):
    value = envelope(generation)
    with pytest.raises(ValueError, match='boot_schema'):
        validate(json.dumps(value, indent=2).encode(), generation)


@pytest.mark.parametrize('binding', [(5001, 1000), (5000, 1001)])
def test_unchanged_boot_rejects_changed_cli_deadline(generation, binding):
    with pytest.raises(ValueError, match='boot_deadline'):
        validate(encoded(envelope(generation)), generation, deadline_binding=binding)


@pytest.mark.parametrize('field', ['schema_version', 'parent_pid', 'worker_birth'])
def test_boot_rejects_boolean_numeric_authority(generation, field):
    value = envelope(generation)
    (value if field == 'schema_version' else value['binding'])[field] = True
    with pytest.raises(ValueError):
        validate(encoded(value), generation)
