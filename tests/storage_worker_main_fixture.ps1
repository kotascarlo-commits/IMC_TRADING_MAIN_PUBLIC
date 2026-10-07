param([string]$Workspace,[string]$Python,[ValidateSet('journal','rename')][string]$Role,
 [ValidateSet('Normal','Mismatch','Expiry','Partial','ReadyWrite')][string]$Case,[switch]$AbsentSHM)
$ErrorActionPreference='Stop';$repo=Split-Path -Parent $PSScriptRoot
. (Join-Path $repo 'TOOLS\IMC_STORAGE_CHANNEL.ps1')
. (Join-Path $repo 'TOOLS\IMC_STORAGE_GUARDS.ps1')
. (Join-Path $repo 'TOOLS\IMC_RUNTIME_JOB.ps1')
Add-Type -TypeDefinition @'
using System;using System.Runtime.InteropServices;
public static class MainFixtureThread {
 [StructLayout(LayoutKind.Sequential)] struct F {public uint Low,High;}
 [DllImport("kernel32.dll")] static extern uint GetCurrentThreadId();
 [DllImport("kernel32.dll",SetLastError=true)] static extern IntPtr OpenThread(uint a,bool i,uint id);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool GetThreadTimes(IntPtr h,out F b,out F e,out F k,out F u);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool CloseHandle(IntPtr h);
 public static long[] Read(){uint id=GetCurrentThreadId();IntPtr h=OpenThread(0x100800,false,id);F b,e,k,u;
  if(h==IntPtr.Zero||!GetThreadTimes(h,out b,out e,out k,out u))throw new Exception("fixture_thread_read");
  long birth=(long)(((ulong)b.High<<32)|b.Low);if(!CloseHandle(h))throw new Exception("fixture_thread_close");
  return new long[]{id,birth};}
}
'@
$nonce=[guid]::NewGuid().ToString('N');$copyNonce='a'*32
$runtime=Join-Path $Workspace 'RUNTIME';$publication=Join-Path $Workspace ('.local-tools\storage-publish-'+$nonce)
$journal=Join-Path $publication 'journal';$archive=Join-Path $publication 'original\imc-main.db'
$main=Join-Path $Workspace 'CORE\data\imc-main.db'
$work=Join-Path $Workspace ('.local-tools\offline-audit-'+$copyNonce+'\work\imc-main.db')
foreach($directory in @($runtime,$journal,(Split-Path $archive -Parent),(Split-Path $main -Parent),(Split-Path $work -Parent),(Join-Path $Workspace 'scripts'))){$null=[IO.Directory]::CreateDirectory($directory)}
[IO.File]::Copy((Join-Path $repo 'scripts\storage_publish_worker.py'),(Join-Path $Workspace 'scripts\storage_publish_worker.py'))
$names=[string[]]@('source_main','source_wal','source_shm','work_main')
$files=[string[]]@($main,($main+'-wal'),($main+'-shm'),$work)
foreach($i in 0..3){if($AbsentSHM -and $i -eq 2){continue};[IO.File]::WriteAllText($files[$i],('SYNTHETIC_MAIN_FIXTURE_'+$names[$i]))}
if($AbsentSHM){$names=[string[]]@($names[0],$names[1],$names[3]);$files=[string[]]@($files[0],$files[1],$files[3])}
$job=[IMCRuntimeJob]::new();$shutdownAttempted=$false;$mutexes=@();$owned=@();$pins=@()
$server=$null;$child=0
function Hash-Bytes([byte[]]$Bytes){$sha=[Security.Cryptography.SHA256]::Create();try{return [BitConverter]::ToString($sha.ComputeHash($Bytes)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}}
function Frame($Value,[string]$Mode='generic'){
    $path=Join-Path $runtime 'frame-input.json'
    [IO.File]::WriteAllText($path,($Value|ConvertTo-Json -Compress -Depth 15),[Text.UTF8Encoding]::new($false))
    $text=& $Python -I (Join-Path $PSScriptRoot 'storage_worker_main_frames.py') $path $Mode
    if($LASTEXITCODE -ne 0){throw 'fixture_frame_encoder'}
    return [Text.Encoding]::UTF8.GetBytes([string]$text)
}
function Durable([string]$Name,[string]$Text){
    $bytes=[Text.Encoding]::UTF8.GetBytes($Text)
    $stream=[IO.File]::Open((Join-Path $journal $Name),[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
    try{$stream.Write($bytes,0,$bytes.Length);$stream.Flush($true)}finally{$stream.Dispose()}
    return Hash-Bytes $bytes
}
try{
    foreach($index in 0..2){$m=[Threading.Mutex]::new($false,('Local\IMC_PUBLISH_AUTH_'+$nonce+'_'+$index));$mutexes+=$m;if(-not $m.WaitOne(0)){throw 'fixture_mutex_busy'};$owned+=$m}
    $thread=[MainFixtureThread]::Read()
    $mu=[IMCNativeMutexState]::Read($mutexes[0].SafeWaitHandle)
    if(-not $mu.Owned -or $mu.OwnerThread -ne $thread[0] -or $mu.OwnerProcess -ne $PID){throw 'fixture_mutex_thread'}
    $self=[Diagnostics.Process]::GetCurrentProcess();$birth=$self.StartTime.ToUniversalTime().ToString('o');$birthTicks=$self.StartTime.ToUniversalTime().ToFileTimeUtc();$self.Dispose()
    $marker=Join-Path $runtime 'maintenance-state.json'
    $active=@{schema_version=1;state='ACTIVE';nonce=$nonce;owner_pid=$PID;owner_birth=$birth;updated_at=$birth;reason='OFFLINE_AUDIT';proof=$null}
    [IO.File]::WriteAllText($marker,($active|ConvertTo-Json -Compress),[Text.UTF8Encoding]::new($false))
    $markerPin=[IMCStorageGuards]::new($Workspace,$nonce,45);$pins+=$markerPin
    $markerPin.Acquire([string[]]@('marker'),[string[]]@($marker),[string[]]@());$markerPin.TakeStrong([string[]]@('marker'),[string[]]@($marker))
    $markerID=$markerPin.MemberJSON('marker')|ConvertFrom-Json;$markerID|Add-Member sha256 $markerPin.Hash('marker')
    $source=[IMCStorageGuards]::new($Workspace,$nonce,45);$pins+=$source
    $source.Acquire($names,$files,[string[]]@((Split-Path $archive -Parent)))
    $writer=[IMCStorageGuards]::new($Workspace,$nonce,45);$pins+=$writer;$writer.AcquireScopes([string[]]@($journal))
    $members=@{source_shm=$null};foreach($name in $names){$id=$source.MemberJSON($name)|ConvertFrom-Json;$id|Add-Member sha256 $source.Hash($name);$members[$name]=$id}
    $paths=@{source_main=$main;source_wal=($main+'-wal');source_shm=($main+'-shm');work_main=$work;
        archive_main=$archive;archive_wal=($archive+'-wal');archive_shm=($archive+'-shm');installed_main=$main;retained_new_main=(Join-Path $publication 'retained-new.db')}
    $generation=@{copy_nonce=$copyNonce;publish_nonce=$nonce;root=$Workspace;paths=$paths;members=$members}
    $scope=if($Role -ceq 'journal'){$writer}else{$source}
    $directories=@{};foreach($path in $scope.DirectoryPaths){$directories[$path]=$scope.DirectoryJSON($path)|ConvertFrom-Json}
    $sentinels=@{};foreach($path in $scope.SentinelPaths){$sentinels[$path]=$scope.SentinelJSON($path)|ConvertFrom-Json}
    $frequency=[Diagnostics.Stopwatch]::Frequency;$seconds=if($Case -ceq 'Expiry'){4}else{35}
    $end=[Diagnostics.Stopwatch]::GetTimestamp()+$frequency*$seconds
    $server=[IMCStorageChannel]::CreateServerUntil($nonce,$Role,$end,$frequency)
    $child=$job.Start($Python,[string[]]@('-I',(Join-Path $PSScriptRoot 'storage_worker_main_child.py'),$repo,$Workspace,$Case,$Role,$nonce,
        '--parent-pid',[string]$PID,'--parent-birth',[string]$birthTicks,'--end-tick',[string]$end,'--frequency',[string]$frequency),$Workspace)
    $job.ObserveMember($child);$process=[Diagnostics.Process]::GetProcessById($child);$childBirth=$process.StartTime.ToUniversalTime().ToFileTimeUtc();$process.Dispose()
    $server.BindPeer($child,$childBirth);$server.Connect()
    $bootEnd=if($Case -ceq 'Mismatch'){$end+1}else{$end}
    $boot=@{schema_version=2;kind='WORKER_BOOT';role=$Role;generation=$generation;
        authority=@{thread_id=$thread[0];thread_birth=$thread[1];marker_identity=$markerID;active_snapshot=$active};
        namespace=@{directories=$directories;sentinels=$sentinels};deadline=@{end_tick=$bootEnd;frequency=$frequency};
        binding=@{publish_nonce=$nonce;role=$Role;parent_pid=$PID;parent_birth=$birthTicks;worker_pid=$child;worker_birth=$childBirth}}
    $bootBody=Frame $boot 'boot';$server.Write($bootBody)
    if($Case -cne 'Normal'){
        $path=Join-Path $runtime 'main-fault.json';$wait=[Diagnostics.Stopwatch]::StartNew()
        while(-not [IO.File]::Exists($path) -and $wait.Elapsed.TotalSeconds -lt 12){Start-Sleep -Milliseconds 10}
        if(-not [IO.File]::Exists($path)){throw 'fixture_main_fault_missing'}
        $fault=Get-Content -LiteralPath $path -Raw|ConvertFrom-Json
        if($fault.phase -cne 'FAULT' -or $fault.pid -ne $child -or -not $fault.local_mutex_adapter -or
            -not $fault.client_present -or $fault.client_closed -or $fault.authority_closed -or $fault.guards_closed -or -not $job.Running($child)){throw 'fixture_main_fault_not_retained'}
        $actualHash=(Get-FileHash -LiteralPath (Join-Path $repo 'scripts\storage_publish_worker.py') -Algorithm SHA256).Hash.ToLowerInvariant()
        if($fault.source_sha256 -cne $actualHash){throw 'fixture_main_source_mismatch'}
        if($Case -ceq 'Partial' -and (-not $fault.guards_present -or @($fault.held_members.PSObject.Properties|Where-Object Value -eq $true).Count -lt 1)){throw 'fixture_main_partial_handle_missing'}
        if($Case -ceq 'ReadyWrite' -and (-not $fault.endpoint_present -or -not $fault.authority_present)){throw 'fixture_main_ready_path_not_reached'}
        if($Case -ceq 'Expiry' -and (-not $fault.expired_boot_returned -or $fault.authority_present -or $fault.guards_present)){throw 'fixture_main_expired_boot_path_not_reached'}
        # Do not read the Worker channel after a fault: no synthetic error ACK.
        $shutdownAttempted=$true;$job.ShutdownWithin(10000)
    }else{
        $reply=[Text.Encoding]::UTF8.GetString($server.Read())|ConvertFrom-Json
        $bootValue=[Text.Encoding]::UTF8.GetString($bootBody)|ConvertFrom-Json
        $contextHash=Hash-Bytes (Frame $bootValue.binding)
        if($reply.kind -cne 'WORKER_READY' -or $reply.role -cne $Role -or $reply.boot_sha256 -cne (Hash-Bytes $bootBody) -or $reply.context_sha256 -cne $contextHash){throw 'fixture_main_ready_unbound'}
        if($Role -ceq 'journal'){
            $writer.AddStrongRead('generation',(Join-Path $journal 'generation.json'));$previous=$writer.Hash('generation')
            foreach($i in 0..11){
                $body=Frame @{index=$i;generation=$generation;context_sha256=$contextHash;previous_sha256=$previous;request_nonce=($i+1).ToString('x32')} 'journal'
                $server.Write($body);$ack=[Text.Encoding]::UTF8.GetString($server.Read())|ConvertFrom-Json
                if($ack.kind -cne 'JOURNAL_COMPLETED' -or $ack.ipc_sequence -ne ($i+1) -or $ack.journal_sequence -ne $i -or
                    $ack.context_sha256 -cne $contextHash -or $ack.request_nonce -cne ($i+1).ToString('x32') -or $ack.request_sha256 -cne (Hash-Bytes $body)){throw 'fixture_main_journal_ack'}
                $kind=if($i%2){'DONE'}else{'INTENT'};$writer.AddStrongRead([string]$i,(Join-Path $journal ($i.ToString('00')+'-'+$kind+'.json')))
                $previous=$writer.Hash([string]$i);if($previous -cne $ack.event_sha256){throw 'fixture_main_event_hash'}
            }
            $finish=Frame @{schema_version=2;kind='WORKER_FINISH';context_sha256=$contextHash;ipc_sequence=13;request_nonce=('c'*32);journal_sha256=$previous}
        }else{
            $keys=@('source_shm','source_wal','source_main','work_main');$targets=@('archive_shm','archive_wal','archive_main','installed_main')
            $steps=@('ARCHIVE_SHM','ARCHIVE_WAL','ARCHIVE_MAIN','INSTALL_MAIN');$expected=@{};foreach($name in $names){$expected[$name]=$paths[$name]}
            foreach($i in 0..3){
                $intentHash=Durable ($i.ToString()+'-intent.json') $steps[$i]
                $body=Frame @{schema_version=2;kind='MOVE_REQUEST';context_sha256=$contextHash;ipc_sequence=($i+1);intent_sequence=($i*2);request_nonce=($i+1).ToString('x32');intent_sha256=$intentHash;step=$steps[$i]}
                $server.Write($body);$ack=[Text.Encoding]::UTF8.GetString($server.Read())|ConvertFrom-Json
                if($ack.kind -cne 'MOVE_COMPLETED' -or $ack.ipc_sequence -ne ($i+1) -or $ack.context_sha256 -cne $contextHash -or $ack.request_sha256 -cne (Hash-Bytes $body) -or $ack.intent_sha256 -cne $intentHash){throw 'fixture_main_move_ack'}
                $expected[$keys[$i]]=$paths[$targets[$i]];$mapping=@();foreach($name in $names){$mapping+=$expected[$name]}
                $source.RebindWeak($names,[string[]]$mapping)
                if([IO.File]::Exists($paths[$keys[$i]]) -and $i -ne 3){throw 'fixture_main_source_remaining'}
                foreach($name in $names){if($source.Hash($name) -cne $members[$name].sha256){throw 'fixture_main_moved_bytes'}}
                if($AbsentSHM -and ([IO.File]::Exists($paths.source_shm) -or [IO.File]::Exists($paths.archive_shm))){throw 'fixture_main_absent_member_created'}
            }
            $doneHash=Durable '7-done.json' 'INSTALL_MAIN DONE7 SYNTHETIC'
            $release=Frame @{schema_version=2;kind='SOURCE_GUARD_RELEASE';context_sha256=$contextHash;ipc_sequence=5;journal_sequence=7;journal_step='INSTALL_MAIN';journal_kind='DONE';journal_sha256=$doneHash;request_nonce=('b'*32)}
            $server.Write($release);$ack=[Text.Encoding]::UTF8.GetString($server.Read())|ConvertFrom-Json
            $releaseHash=Hash-Bytes $release
            if($ack.kind -cne 'SOURCE_GUARDS_CLOSED' -or -not $ack.source_guard_cleanup -or $ack.job_cleanup -or $ack.restart_allowed -or $ack.request_sha256 -cne $releaseHash){throw 'fixture_main_release_ack'}
            $finish=Frame @{schema_version=2;kind='WORKER_FINISH';context_sha256=$contextHash;ipc_sequence=6;request_nonce=('c'*32);release_request_sha256=$releaseHash}
        }
        $server.WriteTerminal($finish);$wait=[Diagnostics.Stopwatch]::StartNew()
        while($job.Running($child) -and $wait.Elapsed.TotalSeconds -lt 5){Start-Sleep -Milliseconds 10}
        if($job.Running($child) -or $job.ExitCode($child) -ne 0){throw 'fixture_main_exit0_not_proved'}
        $shutdownAttempted=$true;$job.ShutdownWithin(10000)
    }
    if(-not $job.CleanupConfirmed){throw 'fixture_main_job_cleanup'}
    foreach($pin in $pins){$pin.AssertAll();$pin.Close()}
    if($Case -ceq 'Normal'){$server.Close()}
    Write-Output 'ACTUAL_WORKER_MAIN_LOCAL_MUTEX_PASS'
}finally{
    if(-not $shutdownAttempted){$shutdownAttempted=$true;$job.ShutdownWithin(10000)}
    if(-not $job.CleanupConfirmed){throw 'fixture_main_cleanup_unconfirmed'}
    foreach($m in $owned){$m.ReleaseMutex()};foreach($m in $mutexes){$m.Dispose()}
}
