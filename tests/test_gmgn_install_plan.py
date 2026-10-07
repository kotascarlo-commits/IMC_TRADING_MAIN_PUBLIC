"""Installation plan binding is not dispatch or receipt-signing authority."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[1]


@pytest.mark.skipif(os.name != 'nt', reason='Windows native plan binding and DPAPI')
@pytest.mark.parametrize('case', [
    'Normal', 'WrongInstallId', 'WrongAuth', 'WrongRecovery', 'WrongSource',
    'Noncanonical', 'DuplicateField', 'ActorMismatch', 'SourceFault', 'AuthFault', 'PlanFault', 'ForeignPrimary',
    'Bom', 'WrongJsonType', 'ExtraField', 'PrimaryAds',
    'PrimaryNormal', 'PrimaryMissingSdk', 'PrimaryMissingContract', 'PrimaryExtraPin',
    'PrimaryWrongHash', 'PrimaryWrongId', 'PrimaryDuplicate', 'PrimaryBom',
    'PrimarySourceFault', 'PrimaryParentFault', 'PrimaryBadType', 'PrimaryExtraField',
    'WarmBuilder', 'WarmOtherPlan', 'WarmChangedPayload', 'WarmChangedLoader',
    'WarmWhitespace', 'WarmNonUtf16', 'WarmFile',
    'EffectAbortFirst', 'EffectWins', 'EffectUnderPlan', 'EffectFailure',
    'CheckpointEntry', 'CheckpointSourceFault', 'CheckpointPlanFault', 'CheckpointHashFinal',
])
def test_read_install_plan(case):
    result = subprocess.run([
        shutil.which('powershell.exe'), '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-File',
        str(ROOT / 'tests/gmgn_install_plan_fixture.ps1'), '-Case', case,
    ], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout.strip())
    assert proof['pass'] and proof['case'] == case
    assert proof['provider_calls'] == 0 and not proof['dispatch_granted']
    assert not proof['production_verified']
