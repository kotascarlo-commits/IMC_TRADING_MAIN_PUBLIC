param([string]$Workspace,[string]$Python,[ValidateSet('Normal','WrongThreadBirth','Unowned','MarkerHash','ProcessBirth','MarkerSwap','ThreadEnd','RelativeDeadline','CloseFalse','CloseException','ConcurrentBeforeClose','ConcurrentDuringClose','ConcurrentFinalClose','Writer','MoveNormal','MoveConcurrent','MoveReentrant','MoveReceiveError','MoveResponseLost','MoveUnknownNative','MoveNoSidecars','MoveConcurrentAck','MoveReleaseNormal','MoveReleaseWrongSeq','MoveReleaseFalse','MoveReleaseException','MoveReleaseAckLost','MoveReleaseEarly','MoveReleaseNoSidecars','MoveReleaseReplay','MoveReleaseExpiry','MoveReleaseConcurrentBefore','MoveReleaseConcurrentDuring','MoveReleaseReentrant','MoveReleaseFinalFault','MoveReleaseQpcFirst','MoveReleaseQpcFinal','MoveReleaseLocalFinal','MoveReleaseAckDeadline','MoveReleaseFinishNormal','MoveReleaseFinishNoSidecars','MoveReleaseFinishWrongHash','MoveReleaseFinishAuthorityFalse','MoveReleaseFinishAuthorityException','MoveReleaseFinishAuthorityDeadline','MoveReleaseFinishPipeFalse','MoveReleaseFinishPeerFalse','MoveReleaseFinishPipeException','MoveReleaseFinishFinalDeadline','MoveReleaseFinishConcurrentBefore','MoveReleaseFinishConcurrentDuring','MoveReleaseFinishConcurrentFinal','MoveReleaseFinishReentrant','MoveReleaseFinishWrongSeq','MoveReleaseFinishWrongContext','MoveReleaseFinishOldNonce','MoveReleaseFinishNoncanonical','MoveReleaseFinishReentrantFinal','MoveReleaseFinishAuthorityReentrantFinal','MoveReleaseFinishClientConcurrentFinal','JournalNormal','JournalWrongHash','JournalCloseFalse','JournalFinalReentrant','JournalWrongNonce','JournalFinalDeadline','JournalClientConcurrentFinal','JournalEarly','JournalAck12Lost')][string]$Case='Normal')
$ErrorActionPreference='Stop'
$Repo=Split-Path -Parent $PSScriptRoot
. (Join-Path $Repo 'TOOLS\IMC_STORAGE_CHANNEL.ps1')
. (Join-Path $Repo 'TOOLS\IMC_STORAGE_GUARDS.ps1')
. (Join-Path $Repo 'TOOLS\IMC_RUNTIME_JOB.ps1')
Add-Type -TypeDefinition @'
using System;using System.Runtime.InteropServices;
public static class AuthorityThreadFixture {
 [StructLayout(LayoutKind.Sequential)] struct F {public uint Low,High;}
 [DllImport("kernel32.dll")] static extern uint GetCurrentThreadId();
 [DllImport("kernel32.dll",SetLastError=true)] static extern IntPtr OpenThread(uint access,bool inherit,uint id);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool GetThreadTimes(IntPtr t,out F b,out F e,out F k,out F u);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool CloseHandle(IntPtr h);
 public static long[] Read() {
  uint id=GetCurrentThreadId();IntPtr h=OpenThread(0x100800,false,id);F b,e,k,u;
  if(h==IntPtr.Zero||!GetThreadTimes(h,out b,out e,out k,out u))throw new Exception("fixture_thread_native_failure");
  long birth=(long)(((ulong)b.High<<32)|b.Low);
  if(!CloseHandle(h))throw new Exception("fixture_thread_close_failure");return new long[]{id,birth};
 }
}
'@
Add-Type -TypeDefinition @'
using System;using System.Threading;using System.Reflection;
public static class AuthorityOwnerThreadFixture {
 static Thread thread;static ManualResetEvent ready,release;static Exception error;
 public static long[] Data;
 public static void Start(Mutex[] mutexes,Type probe) {
  ready=new ManualResetEvent(false);release=new ManualResetEvent(false);
  thread=new Thread(delegate(){try{foreach(Mutex m in mutexes)m.WaitOne();Data=(long[])probe.GetMethod("Read").Invoke(null,null);
   ready.Set();release.WaitOne();foreach(Mutex m in mutexes)m.ReleaseMutex();}catch(Exception e){error=e;ready.Set();}});
  thread.Start();if(!ready.WaitOne(5000)||error!=null)throw new Exception("fixture_owner_thread_start");
 }
 public static void Finish(){release.Set();if(!thread.Join(5000)||error!=null)throw new Exception("fixture_owner_thread_finish");ready.Dispose();release.Dispose();}
}
'@
$nonce=[guid]::NewGuid().ToString('N');$frequency=[Diagnostics.Stopwatch]::Frequency
$qpcExpiry=$Case -cin @('MoveReleaseQpcFirst','MoveReleaseQpcFinal')
$rootGuardSeconds=if($qpcExpiry){30}else{15}
$endTick=[Diagnostics.Stopwatch]::GetTimestamp()+$frequency*15
$role=if($Case -ceq 'Writer' -or $Case.StartsWith('Journal')){'journal'}elseif($Case.StartsWith('Move')){'rename'}else{'fixture'}
$server=[IMCStorageChannel]::CreateServerUntil($nonce,$role,$endTick,$frequency)
$mutexes=@();$owned=@();$job=[IMCRuntimeJob]::new();$guard=$null;$threadOwner=$false;$writerScope=$null;$sourceScope=$null
function Pin-JournalFixtureRecord([string]$Path,[string]$ExpectedHash='') {
    $key=[IO.Path]::GetFileName($Path)
    $writerScope.AddStrongRead($key,$Path)
    $hash=$writerScope.Hash($key)
    if($ExpectedHash -and $hash -cne $ExpectedHash){throw 'fixture_journal_independent_hash_mismatch'}
    return $hash
}
try {
    foreach($index in 0..2) {
        $m=[Threading.Mutex]::new($false,('Local\IMC_PUBLISH_AUTH_'+$nonce+'_'+$index));$mutexes+=$m
        if($Case -cne 'ThreadEnd' -and ($Case -cne 'Unowned' -or $index -ne 2)){if(-not $m.WaitOne(0)){throw 'fixture_mutex_busy'};$owned+=$m}
    }
    $thread=[AuthorityThreadFixture]::Read()
    if($Case -ceq 'ThreadEnd') {
        [AuthorityOwnerThreadFixture]::Start([Threading.Mutex[]]$mutexes,[AuthorityThreadFixture]);$threadOwner=$true
        $thread=[AuthorityOwnerThreadFixture]::Data
    }
    $proof=[IMCNativeMutexState]::Read($mutexes[0].SafeWaitHandle)
    if($proof.OwnerProcess -ne $PID -or $proof.OwnerThread -ne $thread[0] -or $proof.Abandoned -or $proof.Count -ne 0){throw 'fixture_owner_thread_changed'}
    if($Case -cne 'ThreadEnd' -and (-not $proof.Owned -or $proof.CallerThread -ne $thread[0])){throw 'fixture_caller_thread_changed'}
    $self=[Diagnostics.Process]::GetCurrentProcess();$birth=$self.StartTime.ToUniversalTime().ToString('o');$birthTicks=$self.StartTime.ToUniversalTime().ToFileTimeUtc();$self.Dispose()
    $runtime=Join-Path $Workspace 'RUNTIME';$null=New-Item -ItemType Directory -Path $runtime
    $marker=Join-Path $runtime 'maintenance-state.json'
    $active=@{schema_version=1;state='ACTIVE';nonce=$nonce;owner_pid=$PID;owner_birth=$birth;updated_at=$birth;reason='OFFLINE_AUDIT';proof=$null}
    [IO.File]::WriteAllText($marker,($active|ConvertTo-Json -Compress),[Text.UTF8Encoding]::new($false))
    $guard=[IMCStorageGuards]::new($Workspace,$nonce,$rootGuardSeconds)
    $guard.Acquire([string[]]@('marker'),[string[]]@($marker),[string[]]@())
    $guard.TakeStrong([string[]]@('marker'),[string[]]@($marker))
    $identity=$guard.MemberJSON('marker')|ConvertFrom-Json
    $identity|Add-Member -NotePropertyName sha256 -NotePropertyValue $guard.Hash('marker')
    if($Case -ceq 'WrongThreadBirth'){$thread[1]++}
    if($Case -ceq 'MarkerHash'){$identity.sha256='f'*64}
    if($Case -ceq 'ProcessBirth'){$active.owner_birth='2026-01-01T00:00:00.0000000Z'}
    if($Case -ceq 'MarkerSwap') {
        $guard.Close();[IO.File]::Move($marker,($marker+'.old'))
        [IO.File]::WriteAllText($marker,($active|ConvertTo-Json -Compress),[Text.UTF8Encoding]::new($false))
    }
    $writerData=$null
    $moveData=$null
    if($Case -ceq 'Writer' -or $Case.StartsWith('Journal')) {
        $writerDirectory=Join-Path $Workspace ('.local-tools\storage-publish-'+$nonce+'\journal')
        $null=New-Item -ItemType Directory -Path $writerDirectory -Force
        $writerScope=[IMCStorageGuards]::new($Workspace,$nonce,15)
        $writerScope.AcquireScopes([string[]]@($writerDirectory))
        $directories=@{};foreach($path in $writerScope.DirectoryPaths){$directories[$path]=$writerScope.DirectoryJSON($path)|ConvertFrom-Json}
        $sentinels=@{};foreach($path in $writerScope.SentinelPaths){$sentinels[$path]=$writerScope.SentinelJSON($path)|ConvertFrom-Json}
        $writerData=@{directory=$writerDirectory;directories=$directories;sentinels=$sentinels}
    }
    if($Case.StartsWith('Move')) {
        $copyNonce='a'*32
        $publication=Join-Path $Workspace ('.local-tools\storage-publish-'+$nonce)
        $main=Join-Path $Workspace 'CORE\data\imc-main.db'
        $work=Join-Path $Workspace ('.local-tools\offline-audit-'+$copyNonce+'\work\imc-main.db')
        $archive=Join-Path $publication 'original\imc-main.db'
        foreach($directory in @((Split-Path -Parent $main),(Split-Path -Parent $work),(Split-Path -Parent $archive),(Join-Path $publication 'journal'))){$null=New-Item -ItemType Directory -Path $directory -Force}
        # Synthetic native file bytes: this fixture is not a SQLite/copy audit.
        $names=@('source_main','source_wal','source_shm','work_main')
        $files=@($main,($main+'-wal'),($main+'-shm'),$work)
        if($Case -cin @('MoveNoSidecars','MoveReleaseNoSidecars','MoveReleaseFinishNoSidecars')){$names=@('source_main','work_main');$files=@($main,$work)}
        foreach($index in 0..($names.Count-1)){[IO.File]::WriteAllText($files[$index],('FIXTURE_'+$names[$index]),[Text.UTF8Encoding]::new($false))}
        $sourceScope=[IMCStorageGuards]::new($Workspace,$nonce,$rootGuardSeconds)
        $sourceScope.Acquire([string[]]$names,[string[]]$files,[string[]]@((Split-Path -Parent $archive)))
        $members=@{source_wal=$null;source_shm=$null};foreach($key in $names){$id=$sourceScope.MemberJSON($key)|ConvertFrom-Json;$id|Add-Member -NotePropertyName sha256 -NotePropertyValue $sourceScope.Hash($key);$members[$key]=$id}
        $paths=@{source_main=$main;source_wal=($main+'-wal');source_shm=($main+'-shm');work_main=$work;
            archive_main=$archive;archive_wal=($archive+'-wal');archive_shm=($archive+'-shm');installed_main=$main;retained_new_main=(Join-Path $publication 'retained-new.db')}
        $directories=@{};foreach($path in $sourceScope.DirectoryPaths){$directories[$path]=$sourceScope.DirectoryJSON($path)|ConvertFrom-Json}
        $sentinels=@{};foreach($path in $sourceScope.SentinelPaths){$sentinels[$path]=$sourceScope.SentinelJSON($path)|ConvertFrom-Json}
        $moveData=@{generation=@{copy_nonce=$copyNonce;publish_nonce=$nonce;root=$Workspace;paths=$paths;members=$members};
            directories=$directories;sentinels=$sentinels}
    }
    $child=$job.Start($Python,[string[]]@('-I',(Join-Path $PSScriptRoot 'storage_authority_child.py'),$nonce,
        [string]$PID,[string]$birthTicks,[string]$endTick,[string]$frequency,$Workspace,$Case),$Workspace)
    $job.ObserveMember($child)
    $process=[Diagnostics.Process]::GetProcessById($child);$childBirth=$process.StartTime.ToUniversalTime().ToFileTimeUtc();$process.Dispose()
    $server.BindPeer($child,$childBirth);$server.Connect()
    $frame=@{thread_id=$thread[0];thread_birth=$thread[1];marker_identity=$identity;active=$active}
    if($writerData){$frame.writer=$writerData}
    if($moveData){$frame.move=$moveData}
    $server.Write([Text.Encoding]::UTF8.GetBytes(($frame|ConvertTo-Json -Compress -Depth 8)))
    $reply=[Text.Encoding]::UTF8.GetString($server.Read())|ConvertFrom-Json
    if($Case -ceq 'Writer' -or $Case.StartsWith('Journal')) {
        if($reply.result -cne 'WRITER_READY'){throw 'fixture_writer_not_ready'}
        if($Case.StartsWith('Journal')) {
            $previousHash=Pin-JournalFixtureRecord (Join-Path $writerDirectory 'generation.json')
        }
        $journalIndexes=if($Case -ceq 'JournalEarly'){@()}else{@(0..11)}
        foreach($index in $journalIndexes) {
            if($Case.StartsWith('Journal')) {
                $preview=$server.Read()
                $request=[Text.Encoding]::UTF8.GetString($preview)|ConvertFrom-Json
                if($request.kind -cne 'JOURNAL_REQUEST' -or $request.ipc_sequence -ne ($index+1) -or
                    $request.journal_sequence -ne $index -or $request.context_sha256 -cne $reply.context_sha256 -or
                    $request.previous_sha256 -cne $previousHash){throw 'fixture_journal_bad_preview'}
                $server.Write($preview)
            }else {$server.Write([Text.Encoding]::UTF8.GetBytes([string]$index))}
            $completion=[Text.Encoding]::UTF8.GetString($server.Read())|ConvertFrom-Json
            if($completion.kind -cne 'JOURNAL_COMPLETED' -or $completion.journal_sequence -ne $index -or
                    $completion.ipc_sequence -ne ($index+1) -or $completion.context_sha256 -cne $reply.context_sha256){throw 'fixture_writer_bad_completion'}
            if($Case.StartsWith('Journal')) {
                $requestSha=[Security.Cryptography.SHA256]::Create()
                try{$requestHash=[BitConverter]::ToString($requestSha.ComputeHash($preview)).Replace('-','').ToLowerInvariant()}finally{$requestSha.Dispose()}
                if($completion.request_nonce -cne $request.request_nonce -or $completion.request_sha256 -cne $requestHash){throw 'fixture_journal_request_ack_unbound'}
            }
            $writerScope.AssertAll()
            if($Case.StartsWith('Journal')) {
                $kind=if($index%2){'DONE'}else{'INTENT'}
                $previousHash=Pin-JournalFixtureRecord (Join-Path $writerDirectory ($index.ToString('00')+'-'+$kind+'.json')) $completion.event_sha256
            }
        }
        if($Case.StartsWith('Journal')) {
            if($Case -cnotin @('JournalEarly','JournalAck12Lost')) {
            $finishBody=$server.Read()
            $finish=[Text.Encoding]::UTF8.GetString($finishBody)|ConvertFrom-Json
            if($finish.kind -cne 'WORKER_FINISH' -or $finish.ipc_sequence -ne 13 -or
                $finish.context_sha256 -cne $reply.context_sha256 -or $finish.journal_sha256 -cne $completion.event_sha256){throw 'fixture_journal_bad_finish_preview'}
            if($Case -cin @('JournalWrongHash','JournalWrongNonce')) {
                $finishHash=if($Case -ceq 'JournalWrongHash'){'f'*64}else{$finish.journal_sha256}
                $finishNonce=if($Case -ceq 'JournalWrongNonce'){'1'.PadLeft(32,'0')}else{'c'*32}
                $finishBody=[Text.Encoding]::UTF8.GetBytes(([ordered]@{context_sha256=$finish.context_sha256;ipc_sequence=13;
                    journal_sha256=$finishHash;kind='WORKER_FINISH';request_nonce=$finishNonce;schema_version=2}|ConvertTo-Json -Compress))
            }
            $server.WriteTerminal($finishBody)
            }
            $finishWait=[Diagnostics.Stopwatch]::StartNew()
            if($Case -ceq 'JournalNormal') {
                while($job.Running($child) -and $finishWait.Elapsed.TotalSeconds -lt 5){Start-Sleep -Milliseconds 5}
                if($job.Running($child) -or $job.ExitCode($child) -ne 0){throw 'fixture_journal_exit_not_zero'}
            }else {
                $diagnosticPath=Join-Path $Workspace 'RUNTIME\journal-fault.json'
                while(-not [IO.File]::Exists($diagnosticPath) -and $finishWait.Elapsed.TotalSeconds -lt 12){Start-Sleep -Milliseconds 5}
                if(-not [IO.File]::Exists($diagnosticPath)){throw 'fixture_journal_fault_missing'}
                $diagnostic=Get-Content -LiteralPath $diagnosticPath -Raw|ConvertFrom-Json
                if($diagnostic.result -cne 'JOURNAL_FINISH_FAULT' -or -not $diagnostic.endpoint_faulted -or
                    $diagnostic.finish_completed -ne $false -or -not $job.Running($child)){throw 'fixture_journal_fault_not_retained'}
                if($Case -cin @('JournalEarly','JournalAck12Lost') -and $diagnostic.scope_closed -ne $false){throw 'fixture_journal_fault_released_scope'}
            }
        }
    }
    if($Case.StartsWith('Move')) {
        if($reply.result -cne 'MOVE_READY'){throw 'fixture_mover_not_ready'}
        $keys=@('source_shm','source_wal','source_main','work_main')
        $targets=@('archive_shm','archive_wal','archive_main','installed_main')
        $steps=@('ARCHIVE_SHM','ARCHIVE_WAL','ARCHIVE_MAIN','INSTALL_MAIN')
        $expected=@{};foreach($key in $names){$expected[$key]=$paths[$key]}
        $moveIndexes=if($Case -ceq 'MoveReleaseEarly'){@()}else{@(0..3)}
        foreach($index in $moveIndexes) {
            # Root makes one checked fsync completion before granting the move.
            $intentPath=Join-Path $publication ('journal\'+$index+'-native-intent.json')
            $bytes=[Text.Encoding]::UTF8.GetBytes($steps[$index])
            $recordStream=[IO.File]::Open($intentPath,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
            try{$recordStream.Write($bytes,0,$bytes.Length);$recordStream.Flush($true)}finally{$recordStream.Dispose()}
            $sha=[Security.Cryptography.SHA256]::Create();try{$intentHash=[BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
            $request=[ordered]@{context_sha256=$reply.context_sha256;intent_sequence=($index*2);intent_sha256=$intentHash;
                ipc_sequence=($index+1);kind='MOVE_REQUEST';request_nonce=($index+1).ToString('x32');schema_version=2;step=$steps[$index]}
            $server.Write([Text.Encoding]::UTF8.GetBytes(($request|ConvertTo-Json -Compress)))
            $completion=[Text.Encoding]::UTF8.GetString($server.Read())|ConvertFrom-Json
            if($Case -cnotin @('MoveNormal','MoveNoSidecars') -and -not $Case.StartsWith('MoveRelease')) {
                $reply=$completion
                if($Case -cin @('MoveResponseLost','MoveUnknownNative','MoveConcurrentAck')) {
                    $expected.source_shm=$paths.archive_shm
                    $mapping=@();foreach($key in $names){$mapping+=$expected[$key]}
                    $sourceScope.RebindWeak([string[]]$names,[string[]]$mapping)
                }
                foreach($key in $names){if($sourceScope.Hash($key) -cne $members[$key].sha256){throw 'fixture_fault_mutated_source'}}
                break
            }
            if($completion.kind -cne 'MOVE_COMPLETED' -or $completion.ipc_sequence -ne ($index+1) -or $completion.step -cne $steps[$index]){throw 'fixture_bad_move_completion'}
            $expected[$keys[$index]]=$paths[$targets[$index]]
            $mapping=@();foreach($key in $names){$mapping+=$expected[$key]}
            $sourceScope.RebindWeak([string[]]$names,[string[]]$mapping)
            if(Test-Path -LiteralPath $paths[$keys[$index]]){throw 'fixture_source_reappeared'}
            foreach($suffix in @('-wal','-shm','-journal')){if(Test-Path -LiteralPath ($work+$suffix)){throw 'fixture_work_sidecar_present'}}
            if(Test-Path -LiteralPath ($main+'-journal')){throw 'fixture_canonical_journal_present'}
        }
        if($Case.StartsWith('MoveRelease')) {
            # The endpoint trusts Root's command, not an alleged file receipt.
            # Root finishes this fixture DONE7 fsync before issuing that command.
            $doneBytes=[Text.Encoding]::UTF8.GetBytes('INSTALL_MAIN DONE7 FIXTURE')
            $doneStream=[IO.File]::Open((Join-Path $publication 'journal\7-native-done.json'),[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
            try{$doneStream.Write($doneBytes,0,$doneBytes.Length);$doneStream.Flush($true)}finally{$doneStream.Dispose()}
            $doneSha=[Security.Cryptography.SHA256]::Create()
            try{$doneHash=[BitConverter]::ToString($doneSha.ComputeHash($doneBytes)).Replace('-','').ToLowerInvariant()}finally{$doneSha.Dispose()}
            $releaseRequest=[ordered]@{context_sha256=$reply.context_sha256;ipc_sequence=5;
                journal_kind='DONE';journal_sequence=7;journal_sha256=$doneHash;journal_step='INSTALL_MAIN';
                kind='SOURCE_GUARD_RELEASE';request_nonce=('b'*32);schema_version=2}
            if($Case -ceq 'MoveReleaseWrongSeq'){$releaseRequest.ipc_sequence=4}
            if($Case -cne 'MoveReleaseEarly'){$server.Write([Text.Encoding]::UTF8.GetBytes(($releaseRequest|ConvertTo-Json -Compress)))}
            if($qpcExpiry) {
                $expiryPath=Join-Path $Workspace 'RUNTIME\release-expiry.json'
                $expiryWait=[Diagnostics.Stopwatch]::StartNew()
                while(-not [IO.File]::Exists($expiryPath) -and $expiryWait.Elapsed.TotalSeconds -lt 18){Start-Sleep -Milliseconds 10}
                if(-not [IO.File]::Exists($expiryPath)){throw 'fixture_qpc_expiry_evidence_missing'}
                $releaseReply=Get-Content -LiteralPath $expiryPath -Raw | ConvertFrom-Json
                if($releaseReply.authority_healthy -ne $false){throw 'fixture_qpc_expiry_claimed_healthy'}
            } else {$releaseReply=[Text.Encoding]::UTF8.GetString($server.Read())|ConvertFrom-Json}
            if($Case -cin @('MoveReleaseReplay','MoveReleaseAckDeadline')) {
                if($releaseReply.kind -cne 'SOURCE_GUARDS_CLOSED'){throw 'fixture_replay_initial_release_missing'}
                $releaseReply=[Text.Encoding]::UTF8.GetString($server.Read())|ConvertFrom-Json
            }
            $sourceScope.AssertAll()
            if($Case -cin @('MoveReleaseNormal','MoveReleaseNoSidecars') -or $Case.StartsWith('MoveReleaseFinish')) {
                if($releaseReply.kind -cne 'SOURCE_GUARDS_CLOSED' -or $releaseReply.source_guard_cleanup -ne $true -or
                    $releaseReply.job_cleanup -ne $false -or $releaseReply.restart_allowed -ne $false){throw 'fixture_release_bad_ack'}
                if($Case.StartsWith('MoveReleaseFinish')) {
                    $finishRequest=[ordered]@{context_sha256=$reply.context_sha256;ipc_sequence=6;kind='WORKER_FINISH';
                        release_request_sha256=$releaseReply.request_sha256;request_nonce=('c'*32);schema_version=2}
                    if($Case -ceq 'MoveReleaseFinishWrongHash'){$finishRequest.release_request_sha256='f'*64}
                    if($Case -ceq 'MoveReleaseFinishWrongSeq'){$finishRequest.ipc_sequence=5}
                    if($Case -ceq 'MoveReleaseFinishWrongContext'){$finishRequest.context_sha256='f'*64}
                    if($Case -ceq 'MoveReleaseFinishOldNonce'){$finishRequest.request_nonce='b'*32}
                    $finishBody=$finishRequest|ConvertTo-Json -Compress
                    if($Case -ceq 'MoveReleaseFinishNoncanonical'){$finishBody=' '+$finishBody}
                    $server.WriteTerminal([Text.Encoding]::UTF8.GetBytes($finishBody))
                    if($Case -cnotin @('MoveReleaseFinishNormal','MoveReleaseFinishNoSidecars')) {
                        $finishEvidence=Join-Path $Workspace 'RUNTIME\finish-fault.json'
                        $finishWait=[Diagnostics.Stopwatch]::StartNew()
                        while(-not [IO.File]::Exists($finishEvidence) -and $finishWait.Elapsed.TotalSeconds -lt 12){Start-Sleep -Milliseconds 10}
                        if(-not [IO.File]::Exists($finishEvidence)){throw 'fixture_finish_fault_evidence_missing'}
                        $reply=Get-Content -LiteralPath $finishEvidence -Raw | ConvertFrom-Json
                        if($reply.result -cne 'FINISH_FAULT' -or -not $reply.endpoint_faulted -or
                            $reply.finish_completed -ne $false -or -not $reply.source_closed){throw 'fixture_finish_fault_bad_evidence'}
                    }
                }
            } else {
                if($releaseReply.result -cne 'SOURCE_FAULT' -or -not $releaseReply.endpoint_faulted -or
                    (-not $qpcExpiry -and -not $releaseReply.authority_healthy)){throw 'fixture_release_bad_fault'}
                $reply=$releaseReply
            }
        }
    }
    if($Case -ceq 'ThreadEnd') {
        if($reply.result -cne 'PASS'){throw 'fixture_initial_thread_not_alive'}
        [AuthorityOwnerThreadFixture]::Finish();$threadOwner=$false
        $server.Write([Text.Encoding]::UTF8.GetBytes('check again'))
        $reply=[Text.Encoding]::UTF8.GetString($server.Read())|ConvertFrom-Json
    }
    if($Case -cin @('CloseFalse','CloseException','ConcurrentBeforeClose','ConcurrentDuringClose','ConcurrentFinalClose')) {
        if($reply.result -cne 'PASS'){throw 'fixture_initial_close_authority_failed'}
        $server.Write([Text.Encoding]::UTF8.GetBytes('close now'))
        $reply=[Text.Encoding]::UTF8.GetString($server.Read())|ConvertFrom-Json
    }
    if($Case.StartsWith('Journal')) {
        $writerScope.AssertAll()
    }elseif($Case -cin @('MoveReleaseFinishNormal','MoveReleaseFinishNoSidecars')) {
        $finishExit=[Diagnostics.Stopwatch]::StartNew()
        while($job.Running($child) -and $finishExit.Elapsed.TotalSeconds -lt 5){Start-Sleep -Milliseconds 5}
        if($job.Running($child) -or $job.ExitCode($child) -ne 0){throw 'fixture_finish_native_exit_not_zero'}
    }elseif($Case.StartsWith('MoveReleaseFinish')) {
        if($reply.result -cne 'FINISH_FAULT' -or -not $job.Running($child)){throw 'fixture_finish_fault_worker_not_retained'}
        $sourceScope.AssertAll()
        foreach($key in $names){if($sourceScope.Hash($key) -cne $members[$key].sha256){throw 'fixture_finish_fault_mutated_source'}}
    }elseif($Case -cin @('Normal','Writer','MoveNormal','MoveNoSidecars','MoveReleaseNormal','MoveReleaseNoSidecars')) {
        if($Case -ceq 'Normal' -and $reply.result -cne 'PASS'){throw 'fixture_authority_failed'}
        $server.Write([Text.Encoding]::UTF8.GetBytes('close now'))
        $wait=[Diagnostics.Stopwatch]::StartNew();while($job.Running($child) -and $wait.Elapsed.TotalSeconds -lt 5){Start-Sleep -Milliseconds 5}
        if($job.Running($child) -or $job.ExitCode($child) -ne 0){throw 'fixture_authority_cleanup_failed'}
    }elseif($Case.StartsWith('MoveRelease')) {
        if($reply.result -cne 'SOURCE_FAULT' -or -not $job.Running($child)){throw 'fixture_source_release_fault_missing'}
        foreach($key in $names){if($sourceScope.Hash($key) -cne $members[$key].sha256){throw 'fixture_source_release_content_changed'}}
    }else {
        $codes=@{WrongThreadBirth='publish_authority_thread_birth';Unowned='publish_authority_mutex_owner';
            MarkerHash='publish_authority_marker_snapshot';ProcessBirth='publish_authority_snapshot';MarkerSwap='publish_authority_marker_identity';
            ThreadEnd='publish_authority_thread_exited';RelativeDeadline='publish_authority_binding'}
        $codes.CloseFalse='publish_authority_native_6';$codes.CloseException='fixture_authority_after_actual_close'
        $codes.ConcurrentBeforeClose='publish_authority_unconfirmed';$codes.ConcurrentDuringClose='publish_authority_unconfirmed'
        $codes.ConcurrentFinalClose='publish_authority_unconfirmed'
        $codes.MoveConcurrent='publish_endpoint_faulted'
        $codes.MoveReentrant='publish_endpoint_faulted';$codes.MoveReceiveError='fixture_move_receive_failed'
        $codes.MoveConcurrentAck='publish_endpoint_faulted'
        $codes.MoveResponseLost='fixture_move_response_lost';$codes.MoveUnknownNative='publish_native_rename_unconfirmed'
        if($reply.result -cne 'FAULT' -or $reply.code -cne $codes[$Case] -or -not $job.Running($child)){throw 'fixture_authority_wrong_rejection'}
    }
    # Workers close marker/scope handles before Exit0; root jobs cleaned before
    # parent scope/marker close. This is only the private fixture lifecycle.
    $job.ShutdownWithin(10000);if(-not $job.CleanupConfirmed){throw 'fixture_authority_job_cleanup_unconfirmed'}
    if($qpcExpiry) {
        # The expired channel remains Root-owned until this isolated process
        # exits. Native Job cleanup was checked; channel cleanup is not claimed.
        Write-Output 'EXPECTED_QPC_FAULT_CHANNEL_RETAINED'
    } else {$server.Close()}
    if($writerScope){$writerScope.AssertAll();$writerScope.Close()}
    if($sourceScope){$sourceScope.AssertAll();$sourceScope.Close()}
    if($guard -and -not $guard.Closed){$guard.Close()}
    Write-Output 'NATIVE_AUTHORITY_FIXTURE_PASS'
}finally {
    $job.ShutdownWithin(10000);if(-not $job.CleanupConfirmed){throw 'fixture_authority_job_cleanup_unconfirmed'}
    if($threadOwner){[AuthorityOwnerThreadFixture]::Finish()}
    foreach($m in $owned){$m.ReleaseMutex()}
    foreach($m in $mutexes){$m.Dispose()}
}
