"""The advisor pin domain cannot admit trading sources or inherit Node exceptions."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[1]


@pytest.mark.skipif(os.name != 'nt', reason='Windows native lifetime pins')
@pytest.mark.parametrize('case', [
    'AllowedConfig', 'AllowedBroker', 'TradingConfig', 'Node', 'OtherSource',
    'HashMismatch', 'WriteBlocked', 'TradingRejectAdvisor', 'WrongDomain',
])
def test_advisor_domain(case):
    result = subprocess.run([
        shutil.which('powershell.exe'), '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-File',
        str(ROOT / 'tests/imc_advisor_domain_fixture.ps1'), '-Case', case,
    ], cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout.strip())
    assert proof['pass'] and proof['case'] == case
    assert proof['provider_calls'] == 0 and not proof['production_verified']


@pytest.mark.skipif(os.name != 'nt', reason='Windows native lifetime pins')
@pytest.mark.parametrize('case', [
    'Normal', 'MissingMember', 'WrongHash', 'SourceFault', 'ContextFault', 'WrongDirectory', 'AclMismatch', 'OldSelf',
])
def test_advisor_host(case):
    result = subprocess.run([
        shutil.which('powershell.exe'), '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-File',
        str(ROOT / 'tests/imc_advisor_host_fixture.ps1'), '-Case', case,
    ], cwd=ROOT, capture_output=True, text=True, timeout=50)
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout.strip())
    assert proof['pass'] and proof['case'] == case
    assert proof['provider_calls'] == 0 and not proof['production_verified']


@pytest.mark.skipif(os.name != 'nt', reason='Windows Owner InteractiveToken Task')
@pytest.mark.parametrize('case', ['Normal', 'PeerCommandMismatch', 'PeerExit', 'Client', 'Restart', 'StaleBinding', 'ForeignExecutable', 'PlanTaskMismatch'])
def test_advisor_source_closed_task(case):
    result = subprocess.run([
        shutil.which('powershell.exe'), '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-File',
        str(ROOT / 'tests/imc_advisor_task_fixture.ps1'), '-Case', case,
    ], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout.strip())
    assert proof['pass'] and proof['kind'] == 'OWN_ADVISOR_TASK' and proof['case'] == case
    assert proof['provider_calls'] == 0 and not proof['production_verified']


@pytest.mark.skipif(os.name != 'nt', reason='Windows source bootstrap')
@pytest.mark.parametrize('case', ['WrongAnchorHash', 'WrongSchema', 'WrongSourceHash', 'UnlistedSource', 'PreloadedType'])
def test_advisor_client_rejects_untrusted_anchor(case):
    result = subprocess.run([
        shutil.which('powershell.exe'), '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-File',
        str(ROOT / 'tests/imc_advisor_client_fixture.ps1'), '-Case', case,
    ], cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout.strip())
    assert proof['pass'] and proof['case'] == case
    assert proof['provider_calls'] == 0 and not proof['production_verified']
