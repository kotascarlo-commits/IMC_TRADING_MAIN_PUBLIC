$ErrorActionPreference='Stop'
. (Join-Path (Split-Path -Parent $PSScriptRoot) 'TOOLS\IMC_SHADOW_SESSION.ps1')
$start=[datetime]::UtcNow
$contract=[pscustomobject]@{mode='SIGNAL_ONLY';broadcast=$false;signing=$false;live_trading=$false;
    config_identity=('a'*64);owner_gate=[pscustomobject]@{valid=$true;value='OFF'}}
function Report([double]$Second) {
 return [pscustomobject]@{schema_version=1;latest_id=[long]$Second;observed_at=$start.AddSeconds($Second).ToString('o');source_age_seconds=0;
  watch_age_seconds=0;breadth_age_seconds=0;feed_complete=$true;breadth_error=$false;shadow_targets=0;shadow_observed=0;watch_errors=@();storage_bytes=1000;disk_free_bytes=10000000000}
}
function Block($Action,[string]$Code) {
 $blocked=$false;try{& $Action|Out-Null}catch{if($_.Exception.Message -ceq $Code){$blocked=$true}else{throw}}
 if(-not $blocked){throw ('Guard did not block: '+$Code)}
}
$storage=[pscustomobject]@{storage_bytes=1000;disk_free_bytes=10000000000}
$session=New-IMCShadowSession 180 $contract $start $storage
if(-not(Test-IMCShadowTick $session $contract 1 $start.AddSeconds(1) (Report 1) $storage)){throw 'early end'}
if(Test-IMCShadowTick $session $contract 180 $start.AddSeconds(180) $null $storage){throw 'missing deadline'}
if($session.stop_reason -cne 'DURATION_ELAPSED'){throw 'end reason'}
Block {Test-IMCShadowTick $session $contract 1 $start.AddSeconds(10) $null $storage} 'SHADOW_CLOCK_CHANGED'
$changed=$contract|ConvertTo-Json|ConvertFrom-Json;$changed.config_identity='b'*64
Block {Test-IMCShadowTick $session $changed 1 $start.AddSeconds(1) $null $storage} 'SHADOW_CONTRACT_CHANGED'
$changed.broadcast=$true
Block {Assert-IMCShadowContract $changed} 'SHADOW_CONTRACT_UNSAFE'
$changed=$contract|ConvertTo-Json|ConvertFrom-Json;$changed.owner_gate.valid=$false
Block {Assert-IMCShadowContract $changed} 'SHADOW_CONTRACT_UNSAFE'
$session=New-IMCShadowSession 600 $contract $start $storage
$r=Report 1;$r.disk_free_bytes=1
Block {Test-IMCShadowTick $session $contract 1 $start.AddSeconds(1) $r $r} 'SHADOW_STORAGE_LIMIT'
$session=New-IMCShadowSession 600 $contract $start $storage
$null=Test-IMCShadowTick $session $contract 1 $start.AddSeconds(1) (Report 1) $storage
$r=Report 2;$r.storage_bytes=2147485000
Block {Test-IMCShadowTick $session $contract 2 $start.AddSeconds(2) $r $r} 'SHADOW_STORAGE_LIMIT'
foreach($fault in @('source','watch','frozen','feed','pair','breadth','incomplete','missing','stalled')){
 $session=New-IMCShadowSession 600 $contract $start $storage
 foreach($second in @(90,120,150)){
  $r=Report $second
  switch($fault){source{$r.source_age_seconds=31};watch{$r.watch_age_seconds=31};
   frozen{$r.observed_at=$start.ToString('o')};feed{$r.feed_complete=$false};pair{$r.watch_errors=@('shadow_pair_binding_missing')};breadth{$r.breadth_error=$true};incomplete{$r.shadow_targets=1};missing{$r.source_age_seconds=$null};stalled{$r.latest_id=0}}
  if($second -eq 150){Block {Test-IMCShadowTick $session $contract $second $start.AddSeconds($second) $r $storage} 'SHADOW_DATA_UNHEALTHY'}
  else{$null=Test-IMCShadowTick $session $contract $second $start.AddSeconds($second) $r $storage}
 }
}
$validation=$contract|ConvertTo-Json|ConvertFrom-Json;$validation.mode='THREE_TRADE_VALIDATION'
Block {Assert-IMCShadowContract $validation} 'SHADOW_CONTRACT_UNSAFE'
Assert-IMCShadowContract $validation $true
$sixGiB=6442450944L
$restartStorage=[pscustomobject]@{storage_bytes=42050474056L+5368709120L;disk_free_bytes=10000000000L}
$extended=New-IMCShadowSession 43200 $validation $start $restartStorage $true $sixGiB 42050474056L
if($extended.storage_baseline -ne 42050474056L -or $extended.max_storage_growth_bytes -ne $sixGiB){throw 'restart reset original budget'}
if(-not(Test-IMCShadowTick $extended $validation 1 $start.AddSeconds(1) $null $restartStorage)){throw '6GiB budget failed'}
$restartStorage.storage_bytes=42050474056L+$sixGiB
$null=Test-IMCShadowTick $extended $validation 2 $start.AddSeconds(2) $null $restartStorage
$restartStorage.storage_bytes++
Block {Test-IMCShadowTick $extended $validation 3 $start.AddSeconds(3) $null $restartStorage} 'SHADOW_STORAGE_LIMIT'
Block {New-IMCShadowSession 600 $validation $start $restartStorage $true $sixGiB 42050474056L} 'SHADOW_STORAGE_LIMIT'
foreach($budget in @(-1L,0L,6442450945L,[long]::MaxValue)){
 Block {New-IMCShadowSession 600 $validation $start $storage $true $budget} 'SHADOW_BUDGET_INVALID'
}
foreach($baseline in @(-2L,1001L,[long]::MaxValue)){
 Block {New-IMCShadowSession 600 $validation $start $storage $true $sixGiB $baseline} 'SHADOW_BUDGET_INVALID'
}
Block {New-IMCShadowSession 600 $contract $start $storage $false $sixGiB} 'SHADOW_BUDGET_MODE_INVALID'
Block {New-IMCShadowSession 600 $contract $start $storage $false 2147483648L 0L} 'SHADOW_BUDGET_MODE_INVALID'
$zeroBaseline=New-IMCShadowSession 600 $validation $start $storage $true $sixGiB 0L
if($zeroBaseline.storage_baseline -ne 0){throw 'zero baseline replaced'}
$session=New-IMCShadowSession 600 $validation $start $storage $true
$r=Report 1;$r.feed_complete=$false;$r.disk_free_bytes=1
Block {Test-IMCShadowTick $session $validation 1 $start.AddSeconds(1) $r $r} 'SHADOW_STORAGE_LIMIT'
$health=[pscustomobject]@{version='2.0';verdict='HEALTHY';updated=$start.AddSeconds(100).ToString('o');configured_mode='DATA_ONLY_SHADOW';critical=@();
 databases=[pscustomobject]@{core=[pscustomobject]@{readable=$true;error=$null;snapshots_stale=$false;decisions_stale=$false};safe100_watcher=[pscustomobject]@{readable=$true;error=$null}};
 statuses=[pscustomobject]@{safe100_watcher=[pscustomobject]@{exists=$true;stale=$false;last_error=$null;saved_live_execution=$false}};
 process_snapshot=[pscustomobject]@{visibility='RECOGNIZED_ABSOLUTE_PATHS';error=$null;unknown_interpreter_pids=@();processes=@('Core','Watcher','Health','SignalSupervisor'|ForEach-Object{[pscustomobject]@{Role=$_}})}}
$birth=$start.AddSeconds(-1).ToFileTimeUtc().ToString()
$core=@($health.process_snapshot.processes|Where-Object Role -ceq 'Core')[0]
$core|Add-Member -NotePropertyName ProcessId -NotePropertyValue 42
$core|Add-Member -NotePropertyName CreationFiletime -NotePropertyValue $birth
$health|Add-Member -NotePropertyName scan_status -NotePropertyValue ([pscustomobject]@{schema_version=1;verified=$true;error=$null;scan_success=$true;
 pid=42;process_birth_filetime=$birth;cycle_id=1;cycle_seconds=1.0;evaluated=0;updated_at=$start.AddSeconds(100).ToString('o')})
Assert-IMCShadowHealth $health $start.AddSeconds(101) $start
$scan=$health.scan_status
$health.databases.core.decisions_stale=$true
Assert-IMCShadowHealth $health $start.AddSeconds(101) $start
$health.databases.core.snapshots_stale=$true
Block {Assert-IMCShadowHealth $health $start.AddSeconds(101) $start} 'SHADOW_HEALTH_UNVERIFIED'
$health.databases.core.snapshots_stale=$false
$scan.pid=43
Block {Assert-IMCShadowHealth $health $start.AddSeconds(101) $start} 'SHADOW_SCAN_UNVERIFIED'
$scan.pid=42;$scan.process_birth_filetime=$start.AddSeconds(-2).ToFileTimeUtc().ToString()
Block {Assert-IMCShadowHealth $health $start.AddSeconds(101) $start} 'SHADOW_SCAN_UNVERIFIED'
$scan.process_birth_filetime=$birth;$scan.updated_at=$start.AddSeconds(69).ToString('o')
Block {Assert-IMCShadowHealth $health $start.AddSeconds(101) $start} 'SHADOW_SCAN_UNVERIFIED'
$scan.updated_at=$start.AddSeconds(102).ToString('o')
Block {Assert-IMCShadowHealth $health $start.AddSeconds(101) $start} 'SHADOW_SCAN_UNVERIFIED'
$scan.updated_at=$start.AddSeconds(-.5).ToString('o')
Block {Assert-IMCShadowHealth $health $start.AddSeconds(101) $start} 'SHADOW_SCAN_UNVERIFIED'
$scan.updated_at=$start.AddSeconds(100).ToString('o')
$session=New-IMCShadowSession 600 $validation $start $storage $true
Assert-IMCShadowHealth $health $start.AddSeconds(101) $start $session
Assert-IMCShadowHealth $health $start.AddSeconds(102) $start $session
$scan.updated_at=$start.AddSeconds(101).ToString('o')
Block {Assert-IMCShadowHealth $health $start.AddSeconds(102) $start $session} 'SHADOW_SCAN_REPLAY'
$scan.cycle_id=2
Assert-IMCShadowHealth $health $start.AddSeconds(102) $start $session
$scan.cycle_id=1
Block {Assert-IMCShadowHealth $health $start.AddSeconds(103) $start $session} 'SHADOW_SCAN_REPLAY'
$scan.cycle_id=2
# A fresh health report cannot rescue a heartbeat aged 31 seconds at the guard.
$health.updated=$start.AddSeconds(132).ToString('o')
Block {Assert-IMCShadowHealth $health $start.AddSeconds(132) $start $session} 'SHADOW_SCAN_UNVERIFIED'
$health.updated=$start.AddSeconds(100).ToString('o');$scan.updated_at=$start.AddSeconds(100).ToString('o')
$health.scan_status=$null
Block {Assert-IMCShadowHealth $health $start.AddSeconds(101) $start} 'SHADOW_SCAN_UNVERIFIED'
$health.scan_status=$scan
$health.process_snapshot.unknown_interpreter_pids=@(42)
Block {Assert-IMCShadowHealth $health $start.AddSeconds(101) $start} 'SHADOW_HEALTH_UNVERIFIED'
$health.process_snapshot.unknown_interpreter_pids=@();$health.process_snapshot.processes+=[pscustomobject]@{Role='Bridge'}
Block {Assert-IMCShadowHealth $health $start.AddSeconds(101) $start} 'SHADOW_PROCESS_CONFLICT'
Block {Get-IMCShadowFingerprint @($PSCommandPath) 0} 'SHADOW_SOURCE_TIMEOUT'
$first=Get-IMCShadowFingerprint @($PSCommandPath)
if($first -cnotmatch '\A[A-F0-9-]+;\z'){throw 'missing source hash'}
'SHADOW_GUARD_CHECKS_PASS'
