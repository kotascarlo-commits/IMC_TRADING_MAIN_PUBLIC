param([string]$Workspace,[string]$Python,[string]$Case)
$ErrorActionPreference='Stop'
$Repo=Split-Path -Parent $PSScriptRoot
. (Join-Path $Repo 'TOOLS\IMC_STORAGE_GUARDS.ps1')
. (Join-Path $Repo 'TOOLS\IMC_RUNTIME_JOB.ps1')
$nonce=[guid]::NewGuid().ToString('N');$directory=Join-Path $Workspace 'journal'
$null=New-Item -ItemType Directory -Path $directory
$guard=[IMCStorageGuards]::new($Workspace,$nonce,15);$job=[IMCRuntimeJob]::new()
try {
 $guard.AcquireScopes([string[]]@($directory));$guard.AssertAll()
 $directories=@{};foreach($path in $guard.DirectoryPaths){$directories[$path]=$guard.DirectoryJSON($path)|ConvertFrom-Json}
 $sentinels=@{};foreach($path in $guard.SentinelPaths){$sentinels[$path]=$guard.SentinelJSON($path)|ConvertFrom-Json}
 $paths=@{};$source=Join-Path $Workspace 'CORE\data\imc-main.db';$archive=Join-Path $Workspace 'original\imc-main.db';$work=Join-Path $Workspace 'work\imc-main.db'
 $paths.source_main=$source;$paths.source_wal=$source+'-wal';$paths.source_shm=$source+'-shm';$paths.work_main=$work
 $paths.archive_main=$archive;$paths.archive_wal=$archive+'-wal';$paths.archive_shm=$archive+'-shm';$paths.installed_main=$source;$paths.retained_new_main=Join-Path $Workspace 'retained-new.db'
 $members=@{};$i=1;foreach($key in @('source_main','source_wal','source_shm','work_main')){$members[$key]=@{volume=5;file_id=$i;bytes=1;sha256=('a'*64)};$i++}
 $generation=@{copy_nonce=('b'*32);publish_nonce=$nonce;root=$Workspace;paths=$paths;members=$members}
 $frequency=[Diagnostics.Stopwatch]::Frequency;$endTick=[Diagnostics.Stopwatch]::GetTimestamp()+$frequency*12
 $data=@{nonce=$nonce;directories=$directories;sentinels=$sentinels;generation=$generation;end_tick=$endTick;frequency=$frequency}
 $bindingPath=Join-Path $Workspace 'inventory.json'
 [IO.File]::WriteAllText($bindingPath,($data|ConvertTo-Json -Compress -Depth 8),[Text.UTF8Encoding]::new($false))
 if($Case -ceq 'ExtraInventory'){[IO.File]::WriteAllText((Join-Path $directory 'unexpected'),'fixture')}
 if($Case -ceq 'ExistingHeader'){[IO.File]::WriteAllText((Join-Path $directory 'generation.json'),'fixture')}
 $child=$job.Start($Python,[string[]]@('-I',(Join-Path $PSScriptRoot 'storage_writer_inventory_child.py'),$bindingPath,$Workspace,$Case),$Workspace)
 $job.ObserveMember($child)
 $clock=[Diagnostics.Stopwatch]::StartNew();while($job.Running($child) -and $clock.Elapsed.TotalSeconds -lt 10){Start-Sleep -Milliseconds 10}
 if($job.Running($child) -or $job.ExitCode($child) -ne 0){throw 'fixture_inventory_child_failed'}
 $job.ShutdownWithin(10000);if(-not $job.CleanupConfirmed){throw 'fixture_inventory_job_cleanup'}
 $guard.AssertAll();$guard.Close()
 Write-Output 'NATIVE_INVENTORY_ROUNDTRIP_PASS'
}finally {if(-not $job.CleanupConfirmed){$job.ShutdownWithin(10000)}}
