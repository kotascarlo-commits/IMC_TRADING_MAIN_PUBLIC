"""Native, source-pinned three-message handover with no IPC token transfer."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[1]
ERRORS = {
    'CompletionWarmClaimsBefore': ('', ''),
    'CompletionWarmClaimsAfter': ('', ''),
    'CompletionWarmClaimsAndNative': ('', ''),
    'CompletionWarmClaimsAfterNative': ('', ''),
    'CompletionWarmSelfNull': ('', ''),
    'CompletionWarmSelfShort': ('', ''),
    'CompletionWarmScalarCopy': ('', ''),
    'CompletionFileDigest': ('GMGN_PIPE_DISCONNECTED', 'GMGN_HANDOFF_READY_SOURCE'),
    'CompletionDirectoryDigest': ('GMGN_PIPE_DISCONNECTED', 'GMGN_HANDOFF_READY_SOURCE'),
    'CompletionAuthAndDigest': ('GMGN_PIPE_DISCONNECTED', 'GMGN_AUTH_FAULT'),
    'Normal': ('', ''),
    'Replay': ('GMGN_PIN_ACK_USED', ''),
    'GenerationMismatch': ('GMGN_PIPE_DISCONNECTED', 'GMGN_PIN_ACK_MESSAGE'),
    'DigestMismatch': ('GMGN_PIPE_DISCONNECTED', 'GMGN_PIN_ACK_MESSAGE'),
    'DirectoryMismatch': ('GMGN_PIPE_DISCONNECTED', 'GMGN_PIN_ACK_MESSAGE'),
    'WrongSequence': ('GMGN_PIN_ACK_MESSAGE', ''),
    'ExtraField': ('GMGN_PIN_ACK_MESSAGE', ''),
    'MissingField': ('GMGN_PIN_ACK_MESSAGE', ''),
    'DuplicateField': ('GMGN_PIN_ACK_MESSAGE', ''),
    'WrongDirection': ('GMGN_PIN_ACK_MESSAGE', ''),
    'SourceFault': ('GMGN_PIPE_DISCONNECTED', 'GMGN_SOURCE_LOCKED'),
    'AbortBeforeCommit': ('GMGN_PIPE_DISCONNECTED', ''),
    'AbortAfterPinAck': ('', 'GMGN_PIPE_DISCONNECTED'),
    'ManipulatedCommit': ('', 'GMGN_PIN_ACK_MESSAGE'),
    'ReplayNewSession': ('', 'GMGN_PIN_ACK_MESSAGE'),
    'Admission': ('', ''),
    'AdmissionReplay': ('', 'GMGN_PIN_ACK_ADMISSION_STATE'),
    'AdmissionSourceFault': ('', 'GMGN_SOURCE_LOCKED'),
    'ManifestMismatch': ('', 'GMGN_PIN_ACK_MANIFEST_GENERATION'),
    'RootAdmission': ('GMGN_PIN_ACK_ADMISSION_STATE', ''),
    'CompletionNormal': ('', ''),
    'CompletionWrongMac': ('GMGN_HANDOFF_READY_MESSAGE', ''),
    'CompletionReplay': ('GMGN_HANDOFF_READY_STATE', ''),
    'CompletionSourceFault': ('GMGN_PIPE_DISCONNECTED', 'GMGN_SOURCE_LOCKED'),
    'CompletionBeforeAdmission': ('GMGN_PIPE_DISCONNECTED', 'GMGN_HANDOFF_READY_STATE'),
    'CompletionDuplicateRoot': ('GMGN_PIN_ACK_COMPLETION_CLAIMED', ''),
    'CompletionDuplicateChild': ('GMGN_PIPE_DISCONNECTED', 'GMGN_PIN_ACK_COMPLETION_CLAIMED'),
    'TransportDuplicateRoot': ('GMGN_PIPE_HANDSHAKE_CLAIMED', ''),
    'TransportDuplicateChild': ('', 'GMGN_PIPE_HANDSHAKE_CLAIMED'),
    'CompletionWarmNormal': ('', ''),
    'CompletionWarmReplay': ('', 'GMGN_WARM_ORIGIN_BINDING'),
    'CompletionWarmSource': ('', 'GMGN_WARM_ORIGIN_BINDING'),
    'CompletionWarmAuth': ('', 'GMGN_WARM_ORIGIN_BINDING'),
    'CompletionWarmAdmission': ('', 'GMGN_WARM_ORIGIN_BINDING'),
    'CompletionWarmBeforeReady': ('GMGN_PIPE_DISCONNECTED', 'GMGN_WARM_ORIGIN_BINDING'),
    'CompletionWarmFault': ('', 'GMGN_SOURCE_LOCKED'),
    'CompletionWarmRoot': ('GMGN_WARM_ORIGIN_BINDING', ''),
}


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows pipes/files/scheduler')
@pytest.mark.parametrize('case', ERRORS)
def test_native_pin_ack_contract(case):
    result = subprocess.run([
        shutil.which('powershell.exe'), '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-File',
        str(ROOT / 'tests/gmgn_pin_ack_fixture.ps1'), '-Case', case,
    ], cwd=ROOT, capture_output=True, text=True, timeout=50)
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout.strip())
    assert proof['pass'] and proof['case'] == case
    assert (proof['code'], proof['child_code']) == ERRORS[case]
    assert proof['child_native_exit'] == 0 and proof['provider_calls'] == 0
    assert not proof['ipc_token_transferred'] and not proof['production_verified']
    if case in ('AbortAfterPinAck', 'ManipulatedCommit'):
        assert proof['step_two_verified']
    assert proof['fixture_sha256'] == hashlib.sha256(
        (ROOT / 'tests/gmgn_pin_ack_fixture.ps1').read_bytes()).hexdigest()
    fixture = (ROOT / 'tests/gmgn_pin_ack_fixture.ps1').read_bytes().decode('utf-8')
    newline = '\r\n' if "$workerText=@'\r\n" in fixture else '\n'
    worker = fixture.split("$workerText=@'" + newline, 1)[1].split(newline + "'@", 1)[0]
    assert proof['worker_sha256'] == hashlib.sha256(worker.encode('utf-8')).hexdigest()
    assert set(proof['source_hashes']) == {
        'GmgnRepairNamespace', 'GmgnImmutableSources', 'GmgnReadDirectoryIdentity',
        'GmgnRepairProcessBinding', 'GmgnScheduledActionIdentity', 'GmgnSupervisorPipe',
        'GmgnSourcePinBinding', 'GmgnPinTransferHandshake',
        'GmgnSupervisorSourceAdmission',
        'GmgnReadLocalAuth', 'GmgnHandoffCompletion',
    }
    for name, digest in proof['source_hashes'].items():
        assert digest == hashlib.sha256((ROOT / 'TOOLS' / (name + '.cs')).read_bytes()).hexdigest()


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows completion claims')
@pytest.mark.parametrize('case,index', [
    *[('CompletionWarmClaimsBefore', i) for i in [-1, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]],
    *[('CompletionWarmClaimsAfter', i) for i in range(6)],
    ('CompletionWarmClaimsAndNative', -1),
])
def test_original_completion_claim_mutation(case, index):
    result = subprocess.run([
        shutil.which('powershell.exe'), '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-File',
        str(ROOT / 'tests/gmgn_pin_ack_fixture.ps1'), '-Case', case,
        '-ClaimIndex', str(index),
    ], cwd=ROOT, capture_output=True, text=True, timeout=50)
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout.strip())
    assert proof['pass'] and proof['code'] == proof['child_code'] == ''
    assert proof['child_native_exit'] == proof['provider_calls'] == 0
    assert not proof['production_verified'] and not proof['ipc_token_transferred']


@pytest.mark.skipif(os.name != 'nt', reason='Native process lifetime after handover')
def test_child_source_admission_survives_original_root_exit():
    result = subprocess.run([
        shutil.which('powershell.exe'), '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-File',
        str(ROOT / 'tests/gmgn_admission_root_exit_fixture.ps1'),
    ], cwd=ROOT, capture_output=True, text=True, timeout=40)
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout.strip())
    assert proof['pass'] and proof['root_native_exit'] == proof['child_native_exit'] == 0
    assert proof['task_deleted'] and proof['provider_calls'] == 0
    assert proof['warm_origin_independent']
    assert not proof['production_verified']
