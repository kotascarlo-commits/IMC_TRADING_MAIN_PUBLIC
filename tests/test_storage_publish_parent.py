"""Native guard handoff only, in temp fixtures; no production authority."""
import copy
import ctypes
import os
from pathlib import Path
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
from storage_publish_parent import ParentReadGuards
from storage_publish_guard import RenameGuards
from windows_read_guard import ReadFiles

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='actual Windows native handoff')


@pytest.fixture
def parent(tmp_path):
    source, work = tmp_path / 'original.db', tmp_path / 'work.db'
    source.write_bytes(b'original generation')
    work.write_bytes(b'compact generation')
    archive = tmp_path / 'archive'
    archive.mkdir()
    destinations = [archive / source.name, source]
    old = ReadFiles([source, work], deadline=time.monotonic() + 20)
    old.__enter__()
    with ParentReadGuards(tmp_path, {'source': source, 'work': work},
                          deadline=time.monotonic() + 20, destinations=destinations,
                          nonce='a' * 32) as owned:
        try:
            yield owned, old, source, work, destinations
        finally:
            old.close()


def attached(parent, bindings=None):
    owned, _, source, work, destinations = parent
    return RenameGuards(owned.root, {'source': source, 'work': work},
        deadline=owned.deadline, destinations=destinations, nonce=owned.nonce,
        attached_sentinels=owned.sentinel_bindings if bindings is None else bindings)


def test_full_file_directory_sentinel_handoff_preserves_all_identities(parent):
    owned, old, source, _, destinations = parent
    initial = {key: copy.deepcopy(member['identity']) for key, member in owned.members.items()}
    # Strong old guards prohibit DELETE handles, despite parent weak overlap.
    with pytest.raises(OSError):
        with attached(parent): pass
    owned.assert_bound()
    old.close()
    with attached(parent) as child:
        child.move('source', destinations[0])
        child.move('work', source)
        with pytest.raises(OSError):
            owned.acquire_strong({'source': destinations[0], 'work': source})
        # Directory rename/deletion stays excluded with both processes' pins.
        with pytest.raises(OSError): os.rename(destinations[0].parent, owned.root / 'foreign')
    strong = owned.acquire_strong({'source': destinations[0], 'work': source})
    try:
        assert {key: member['identity'] for key, member in owned.members.items()} == initial
        owned.close_files_after_strong(strong)
        owned._assert_directories()
        assert source.read_bytes() == b'compact generation'
        assert destinations[0].read_bytes() == b'original generation'
        assert all(member['handle'] is None for member in owned.members.values())
    finally:
        strong.close()


@pytest.mark.parametrize('change', ['id', 'volume', 'bytes', 'missing', 'extra', 'path'])
def test_sentinel_attach_requires_exact_explicit_native_generation(parent, change):
    owned, old, _, _, _ = parent
    bindings = owned.sentinel_bindings
    key = next(iter(bindings))
    if change in ('id', 'volume', 'bytes'):
        field = {'id': 'file_id', 'volume': 'volume', 'bytes': 'bytes'}[change]
        bindings[key][field] += 1
    elif change == 'missing': bindings.pop(key)
    elif change == 'extra': bindings[str(owned.root / 'unknown.sentinel')] = bindings[key].copy()
    else: bindings[str(owned.root / 'wrong.sentinel')] = bindings.pop(key)
    old.close()
    with pytest.raises(ValueError, match='sentinel'):
        with attached(parent, bindings): pass
    owned.assert_bound()


def test_parent_cannot_rename_and_missing_strong_handoff_keeps_weak_handles(parent):
    owned, old, source, _, destinations = parent
    with pytest.raises(ValueError, match='no_move_authority'): owned.move('source', destinations[0])
    with pytest.raises(ValueError, match='strong_missing'): owned.close_files_after_strong(None)
    owned.assert_bound()
    assert source.exists()


def test_existing_sentinel_is_not_an_implicit_attach_fallback(parent):
    owned, old, source, work, destinations = parent
    old.close()
    with pytest.raises(OSError):
        with RenameGuards(owned.root, {'source': source, 'work': work},
                          deadline=owned.deadline, destinations=destinations, nonce=owned.nonce): pass
    owned.assert_bound()


def test_wrong_rebind_mapping_never_changes_expected_paths(parent):
    owned, _, source, work, _ = parent
    with pytest.raises(ValueError, match='handoff_identity'):
        owned.rebind({'source': work, 'work': source})
    assert owned.members['source']['path'] == source
    assert owned.members['work']['path'] == work
    owned.assert_bound()


def test_cached_strong_evidence_cannot_hide_swapped_native_handle(parent):
    owned, old, source, work, _ = parent
    old.close()
    strong = owned.acquire_strong({'source': source, 'work': work})
    extra = owned.root / 'foreign.db'
    extra.write_bytes(b'foreign')
    with ReadFiles([extra], deadline=owned.deadline) as foreign:
        actual = strong.handles[0]
        strong.handles[0] = foreign.handles[0]
        try:
            with pytest.raises(ValueError, match='strong_native_proof'):
                owned.close_files_after_strong(strong)
            owned.assert_bound()
            assert all(member['handle'] is not None for member in owned.members.values())
        finally:
            strong.handles[0] = actual  # Test double never closed this owned handle.
            strong.close()


def test_partial_strong_acquisition_failure_keeps_all_parent_pins(parent):
    owned, old, source, work, _ = parent
    old.close()
    with RenameGuards(owned.root, {'work': work}, deadline=owned.deadline, nonce='b' * 32):
        with pytest.raises(OSError):
            owned.acquire_strong({'source': source, 'work': work})
        owned.assert_bound()
        assert all(member['handle'] is not None for member in owned.members.values())
