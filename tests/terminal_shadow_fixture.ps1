param([string]$WorkDir)
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
. (Join-Path $root 'TOOLS/IMC_TERMINAL_REFRESH.ps1')
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $root 'TOOLS/IMC_Terminal.ps1'),[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Terminal parse error'}
foreach($statement in $ast.EndBlock.Statements){if($statement -is [Management.Automation.Language.FunctionDefinitionAst]){. ([scriptblock]::Create($statement.Extent.Text))}}
$root=$WorkDir;$null=New-Item -ItemType Directory -Path (Join-Path $root RUNTIME) -Force
$path=Join-Path $root 'RUNTIME/signal-runtime-status.json';$checks=0
function Check($Value,[string]$Name){if(-not $Value){throw $Name};$script:checks++}
function Save($Value){[IO.File]::WriteAllText($path,($Value|ConvertTo-Json -Depth 8),[Text.UTF8Encoding]::new($false))}
function Fresh {
    $now=[DateTimeOffset]::UtcNow;$start=$now.AddMilliseconds(-10)
    $contract=[pscustomobject]@{broadcast=$false;signing=$false;live_trading=$false;owner_gate=@{valid=$true;value='OFF'}}
    return [pscustomobject]@{schema_version=1;execution_mode='DATA_ONLY_SHADOW';state='RUNNING';code='OK';pid=$PID;
        process_birth_filetime=(Get-Process -Id $PID).StartTime.ToUniversalTime().ToFileTimeUtc().ToString();updated_at=$now.ToString('o');broadcast=$false;live_ready=$false;
        shadow_session=[pscustomobject]@{data_only=$true;qualified_trading_ready=$false;evidence_domain='DIAGNOSTIC_SHADOW_NOT_PERFORMANCE';
            started_at=$start.ToString('o');planned_end=$start.AddSeconds(300).ToString('o');duration_seconds=300;storage_baseline=100;max_storage_growth_bytes=1000;failures=0;scan_cycle_id=4;contract=($contract|ConvertTo-Json -Compress);
            latest_observation=[pscustomobject]@{schema_version=1;evidence_domain='DIAGNOSTIC_SHADOW_NOT_PERFORMANCE';qualified_trading_ready=$false;observed_at=$now.AddMilliseconds(-1).ToString('o');latest_id=12;source_age_seconds=1.0;storage_bytes=120;disk_free_bytes=6000000000L;shadow_targets=1;shadow_observed=1;sample_size=100;security_checked=2;holder_values_present=0;feed_complete=$true;watch_errors=@()}}}
}
$s=Fresh;Save $s
$read=Start-IMCStatusRead -Python 'DOES-NOT-EXIST.exe' -Entry 'DOES-NOT-EXIST.py' -Root $root
$view=(Receive-IMCStatusRead $read).Snapshot;Stop-IMCStatusRead $read
Check ($read.Native -and $view.status -eq 'RUNNING' -and $view.observation.latest_id -eq 12) 'native read launches no Python'
$lines=@(Get-RealityLines $view) -join "`n"
Check ($lines.Contains('STATISTIK-NACHTLAUF') -and $lines.Contains('N/A (keine Trade-Performance') -and -not $lines.Contains('Startguthaben:')) 'diagnostic metrics only'
foreach($name in @('birth','pid','status-stale','future','observation-stale','observation-other-run','observation-future','observation-schema-bool','counter-bool','counter-float','feed-type','gates','unknown-state','duration')){
    $s=Fresh
    switch($name){
        'birth' {$s.process_birth_filetime='134000000000000000'}
        'pid' {$s.pid=2147483647}
        'status-stale' {$s.updated_at=[DateTimeOffset]::UtcNow.AddSeconds(-40).ToString('o')}
        'future' {$s.updated_at=[DateTimeOffset]::UtcNow.AddMinutes(1).ToString('o')}
        'observation-stale' {$s.shadow_session.latest_observation.source_age_seconds=31}
        'observation-other-run' {$s.shadow_session.latest_observation.observed_at=[DateTimeOffset]::UtcNow.AddMinutes(-1).ToString('o')}
        'observation-future' {$s.shadow_session.latest_observation.observed_at=[DateTimeOffset]::UtcNow.AddMinutes(1).ToString('o')}
        'observation-schema-bool' {$s.shadow_session.latest_observation.schema_version=$true}
        'counter-bool' {$s.shadow_session.scan_cycle_id=$true}
        'counter-float' {$s.shadow_session.latest_observation.latest_id=1.2}
        'feed-type' {$s.shadow_session.latest_observation.feed_complete='true'}
        'gates' {$s.broadcast=$true}
        'unknown-state' {$s.state='COMPLETED'}
        'duration' {$s.shadow_session.duration_seconds=299}
    }
    Save $s;$view=Get-IMCShadowDisplay $root
    $observationError=$name -in @('observation-stale','observation-other-run','observation-future','observation-schema-bool','counter-float','feed-type')
    Check ($view.diagnostic -and $view.status -eq $(if($observationError){'RUNNING'}else{'UNKNOWN'}) -and $null -eq $view.observation) $name
    if($observationError){Check ($view.observation_state -eq $(if($name -eq 'observation-stale'){'STALE'}else{'UNKNOWN'}) -and $null -eq $view.storage_growth_bytes) ($name+' clears all derived stats')}
}
Save (Fresh);$view=Get-IMCShadowDisplay $root
Check ($view.observation_state -eq 'FRESH' -and $view.observation.latest_id -eq 12) 'source recovery restores stats'
$unix=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000
$view.heartbeat=$unix;$view.source_timestamp=$unix-31
$view=Update-IMCDiagnosticFreshness $view $unix
Check ($view.status -eq 'RUNNING' -and $view.observation_state -eq 'STALE' -and $null -eq $view.observation -and $null -eq $view.storage_growth_bytes) 'render clears stats across source age frontier'
Save (Fresh);$view=Get-IMCShadowDisplay $root;$view.heartbeat=$unix-31
Check ((Update-IMCDiagnosticFreshness $view $unix).status -eq 'UNKNOWN') 'render expires status heartbeat'
Save (Fresh);$view=Get-IMCShadowDisplay $root;$view.planned_end=$unix
Check ((Update-IMCDiagnosticFreshness $view $unix).status -eq 'UNKNOWN') 'render checks absolute deadline'
Save (Fresh);$view=Get-IMCShadowDisplay $root;$unix=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000;$view.health_verdict='HEALTHY';$view.health_messages=@('old warning');$view.health_updated_at=$unix-31
$view=Update-IMCDiagnosticFreshness $view $unix
Check ($view.health_verdict -eq 'UNKNOWN' -and $view.health_messages.Count -eq 0) 'health has independent freshness anchor'
Save (Fresh);$view=Get-IMCShadowDisplay $root;$unix=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000;$view.observation=$null;$view.observation_state='LOADING';$view.started_at=$unix-91
Check ((Update-IMCDiagnosticFreshness $view $unix).observation_state -eq 'UNKNOWN') 'missing data past warmup is unknown'
$s=Fresh;$s.state='STOPPED';$s.pid=2147483647;$s.updated_at=[DateTimeOffset]::UtcNow.AddHours(-1).ToString('o');$s.shadow_session.started_at=[DateTimeOffset]::UtcNow.AddHours(-2).ToString('o');$s.shadow_session.planned_end=[DateTimeOffset]::Parse($s.shadow_session.started_at).AddSeconds(300).ToString('o');$s.shadow_session.latest_observation.observed_at=$s.updated_at
Save $s;$view=Get-IMCShadowDisplay $root
Check ($view.status -eq 'STOPPED' -and $view.historical -and $view.remaining_seconds -eq 0) 'historical stopped does not claim native cleanup'
Check ((@(Get-RealityLines $view) -join "`n").Contains('GESPEICHERTER LAUF')) 'historic label'
try{Assert-IMCRealityControl;throw 'control was allowed'}catch{Check ($_.Exception.Message -like 'Diagnoseansicht*') 'diagnostic controls blocked'}
$Reality=$true;Assert-IMCRealityControl;$Reality=$false
Check $true 'explicit Reality after stopped allowed'
[IO.File]::WriteAllText($path,'{broken');$view=Get-IMCShadowDisplay $root
Check ($view.status -eq 'UNKNOWN') 'malformed JSON stays diagnostic'
[IO.File]::Move($path,$path+'.archived');$view=Get-IMCShadowDisplay $root
Check ($view.status -eq 'UNKNOWN' -and $view.diagnostic) 'missing selected source never falls back'
[IO.File]::WriteAllText($path,('x'*65537));Check ((Get-IMCShadowDisplay $root).status -eq 'UNKNOWN') 'bounded JSON'
$script:IMCDiagnosticSelected=$false;[IO.File]::WriteAllText($path,'{}');$view=Get-IMCShadowDisplay $root
Check ($view.diagnostic -and $view.status -eq 'UNKNOWN') 'missing mode never selects Reality'
for($i=0;$i -lt 30;$i++){
    $s=Fresh;$s.shadow_session.latest_observation.latest_id=$i
    $tmp=$path+'.'+$i+'.tmp';[IO.File]::WriteAllText($tmp,($s|ConvertTo-Json -Depth 8),[Text.UTF8Encoding]::new($false));[IO.File]::Replace($tmp,$path,[NullString]::Value)
    Check ((Get-IMCShadowDisplay $root).observation.latest_id -eq $i) 'atomic replacement reader'
}
[pscustomobject]@{ok=$true;checks=$checks}|ConvertTo-Json -Compress
