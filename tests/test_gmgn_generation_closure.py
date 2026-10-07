"""Compile the actual closed supervisor generation without starting it."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[1]
APPROVED = ROOT / '.local-tools/gmgn-approved-public-generation-23/package.json'


@pytest.mark.skipif(os.name != 'nt' or not APPROVED.exists(),
                    reason='Local owner-approved public SDK inventory required')
def test_actual_public_generation_closes_native_bootstrap_chain():
    result = subprocess.run([
        shutil.which('powershell.exe'), '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-File',
        str(ROOT / 'tests/gmgn_generation_closure_fixture.ps1'),
    ], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout.strip())
    assert proof['pass'] and proof['manifest_native_pinned']
    assert proof['native_members'] == proof['manifest_members'] + 1
    assert proof['provider_calls'] == 0 and not proof['retirement_attempted']
    assert not proof['production_verified']


@pytest.mark.skipif(os.name != 'nt' or not APPROVED.exists(),
                    reason='Local owner-approved public SDK inventory required')
def test_production_cannot_fall_back_to_legacy_object_context():
    result = subprocess.run([
        shutil.which('powershell.exe'), '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-File',
        str(ROOT / 'tests/gmgn_generation_closure_fixture.ps1'), '-Case', 'ProductionWarmFallback',
    ], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout.strip())
    assert proof['pass'] and proof['code'] == 'GMGN_SUPERVISOR_ADMISSION_REQUIRED'
    assert proof['provider_calls'] == 0 and not proof['production_verified']
