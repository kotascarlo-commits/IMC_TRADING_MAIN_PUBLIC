"""Same absolute QPC bound across native guard groups; isolated files only."""
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).parents[1]


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows QPC/file handles')
@pytest.mark.parametrize('case', [
    'Binding', 'Expired', 'Frequency', 'Overflow', 'Zero', 'Regression',
    'Deadline', 'OneTick', 'Partial', 'CloseAfterElapsed',
])
def test_absolute_guard_deadline_and_retention(tmp_path, case):
    (tmp_path / 'source.db').write_bytes(b'untouched')
    (tmp_path / 'journal').mkdir()
    result = subprocess.run([
        'powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy',
        'Bypass', '-File', str(ROOT / 'tests/storage_absolute_guards_fixture.ps1'),
        '-Workspace', str(tmp_path), '-Case', case,
    ], cwd=ROOT, capture_output=True, text=True, timeout=40)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'ABSOLUTE_GUARD_PASS' in result.stdout
    assert (tmp_path / 'source.db').read_bytes() == b'untouched'
