import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT=Path(__file__).parents[1]
pytestmark=pytest.mark.skipif(os.name!='nt',reason='native Windows parent/mutant/thread authority')


@pytest.mark.parametrize('case',['Normal','WrongThreadBirth','Unowned','MarkerHash','ProcessBirth','MarkerSwap','ThreadEnd','RelativeDeadline',
                               'CloseFalse','CloseException','ConcurrentBeforeClose','ConcurrentDuringClose','ConcurrentFinalClose','Writer','MoveNormal','MoveConcurrent',
                               'MoveReentrant','MoveReceiveError','MoveResponseLost','MoveUnknownNative','MoveNoSidecars','MoveConcurrentAck',
                               'MoveReleaseNormal','MoveReleaseWrongSeq','MoveReleaseFalse','MoveReleaseException','MoveReleaseAckLost',
                               'MoveReleaseEarly','MoveReleaseNoSidecars','MoveReleaseReplay','MoveReleaseExpiry',
                               'MoveReleaseConcurrentBefore','MoveReleaseConcurrentDuring','MoveReleaseReentrant','MoveReleaseFinalFault',
                               'MoveReleaseQpcFirst','MoveReleaseQpcFinal','MoveReleaseLocalFinal','MoveReleaseAckDeadline',
                               'MoveReleaseFinishNormal','MoveReleaseFinishNoSidecars','MoveReleaseFinishWrongHash',
                               'MoveReleaseFinishAuthorityFalse','MoveReleaseFinishAuthorityException','MoveReleaseFinishAuthorityDeadline',
                               'MoveReleaseFinishPipeFalse','MoveReleaseFinishPeerFalse','MoveReleaseFinishPipeException','MoveReleaseFinishFinalDeadline',
                               'MoveReleaseFinishConcurrentBefore','MoveReleaseFinishConcurrentDuring','MoveReleaseFinishConcurrentFinal',
                               'MoveReleaseFinishReentrant','MoveReleaseFinishWrongSeq','MoveReleaseFinishWrongContext',
                               'MoveReleaseFinishOldNonce','MoveReleaseFinishNoncanonical',
                               'MoveReleaseFinishReentrantFinal','MoveReleaseFinishAuthorityReentrantFinal',
                               'MoveReleaseFinishClientConcurrentFinal',
                               'JournalNormal','JournalWrongHash','JournalCloseFalse','JournalFinalReentrant',
                               'JournalWrongNonce','JournalFinalDeadline','JournalClientConcurrentFinal',
                               'JournalEarly','JournalAck12Lost'])
def test_readonly_native_parent_thread_mutex_and_active_marker(tmp_path,case):
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
        '-File',str(ROOT/'tests/storage_authority_fixture.ps1'),'-Workspace',str(tmp_path),
        '-Python',sys._base_executable,'-Case',case],capture_output=True,timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode==0,(result.stdout+result.stderr).decode('utf-8',errors='replace')
    assert b'NATIVE_AUTHORITY_FIXTURE_PASS' in result.stdout
    if case in ('MoveReleaseQpcFirst','MoveReleaseQpcFinal'):
        assert b'EXPECTED_QPC_FAULT_CHANNEL_RETAINED' in result.stdout
