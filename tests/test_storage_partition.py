"""Native two-group preparation; no process handover, CAS or DB publication."""
import json
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).parents[1]


@pytest.mark.skipif(os.name != 'nt', reason='Windows native file guards')
@pytest.mark.parametrize('case', ['Normal', 'LeafCollision', 'IdentityWidth'])
def test_native_moving_and_evidence_partition(tmp_path, case):
    result = subprocess.run([
        'powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy',
        'Bypass', '-File', str(ROOT / 'tests/storage_partition_fixture.ps1'),
        '-Workspace', str(tmp_path), '-Case', case,
    ], cwd=ROOT, capture_output=True, text=True, timeout=40)
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout)
    assert proof['pass']
    if case == 'IdentityWidth':
        assert proof['identity_width_verified'] and proof['original_fds'] == 0
    else:
        assert proof['original_fds'] == 12
    assert not proof['publication_allowed'] and proof['provider_calls'] == 0
    if case == 'Normal':
        assert proof['moving_count'] == 3 and proof['evidence_count'] == 9
        assert proof['overlap_hashes'] == 12 and proof['strong_before_original_close']
        assert proof['appended'] == 2
