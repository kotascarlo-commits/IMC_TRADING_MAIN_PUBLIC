# Pure validation for the existing owned supervisor. No sender or process actions.
if(-not ('IMCShadowSourceProbe' -as [type])) {
Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Security.Cryptography;
using System.Threading.Tasks;
public static class IMCShadowSourceProbe {
    public static Task<string> Start(string[] paths) {
        return Task.Run(() => {
            string result="";
            foreach(string path in paths) {
                if(!File.Exists(path)) {result+="ABSENT;";continue;}
                using(var input=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.ReadWrite|FileShare.Delete)) {
                    if(input.Length>33554432) throw new IOException("SHADOW_SOURCE_SIZE");
                    using(var hash=SHA256.Create()) {result+=BitConverter.ToString(hash.ComputeHash(input))+";";}
                }
            }
            return result;
        });
    }
}
'@
}
function Get-IMCShadowFingerprint([string[]]$Paths,[int]$TimeoutMilliseconds=5000) {
    $task=[IMCShadowSourceProbe]::Start($Paths)
    try {
        if($TimeoutMilliseconds -le 0 -or -not $task.Wait($TimeoutMilliseconds)){throw 'SHADOW_SOURCE_TIMEOUT'}
        return $task.Result
    }catch{if($_.Exception.Message -ceq 'SHADOW_SOURCE_TIMEOUT'){throw};throw 'SHADOW_SOURCE_UNVERIFIED'}
}
function Get-IMCShadowStorage([string]$Root) {
    $bytes=0L
    foreach($name in @('CORE\data\imc-main.db','RUNTIME\watch-breadth-v425-shadow.db',
        'RUNTIME\EXECUTOR_GATEWAY\GMGN_BRIDGE\execution-ledger.db')) {
        foreach($suffix in @('','-wal','-shm')) {
            $file=Get-Item -LiteralPath (Join-Path $Root ($name+$suffix)) -ErrorAction SilentlyContinue
            if($file){$bytes+=$file.Length}
        }
    }
    $drive=[IO.DriveInfo]::new([IO.Path]::GetPathRoot($Root))
    return [pscustomobject]@{storage_bytes=$bytes;disk_free_bytes=$drive.AvailableFreeSpace}
}
function Test-IMCShadowNumber($Value) {
    return ($Value -is [ValueType] -and $Value -isnot [bool] -and
        -not [double]::IsNaN([double]$Value) -and -not [double]::IsInfinity([double]$Value) -and $Value -ge 0)
}
function Test-IMCScanNoCandidates($Scan) {
    $o=$Scan.observation
    if($Scan.evaluated -isnot [int] -and $Scan.evaluated -isnot [long]){return $false}
    if($Scan.evaluated -ne 0 -or $Scan.scan_success -isnot [bool] -or -not $Scan.scan_success -or
        $o.market_state -cne 'NO_CANDIDATES' -or $null -eq $o.errors -or @($o.errors).Count){return $false}
    foreach($name in @('gecko','dex')){
        $p=$o.providers.$name
        $fresh=($p.network_success -is [bool] -and $p.network_success -and
            $p.state -cin @('FRESH','DISCOVERY_CACHE_MARKET_REFRESH'))
        $empty=($p.state -ceq 'CACHED_EMPTY' -and $p.empty_verified -is [bool] -and $p.empty_verified -and
            (Test-IMCShadowNumber $p.empty_age_seconds) -and $p.empty_age_seconds -le 10)
        if(-not $fresh -and -not $empty){return $false}
    }
    $refresh=$o.providers.refresh
    return (($refresh.network_success -is [bool] -and $refresh.network_success) -or $refresh.state -ceq 'NOT_REQUESTED')
}
function New-IMCShadowSession([int]$Seconds, $Contract, [datetime]$Now, $Storage, [bool]$DataOnly=$false,
    [long]$MaxGrowthBytes=2147483648, [long]$BaselineBytes=-1) {
    if ($Seconds -lt 1 -or $Seconds -gt 43200) { throw 'SHADOW_DURATION_INVALID' }
    if($MaxGrowthBytes -lt 1 -or $MaxGrowthBytes -gt 6442450944 -or $BaselineBytes -lt -1){throw 'SHADOW_BUDGET_INVALID'}
    if(-not $DataOnly -and ($MaxGrowthBytes -ne 2147483648 -or $BaselineBytes -ne -1)){throw 'SHADOW_BUDGET_MODE_INVALID'}
    Assert-IMCShadowContract $Contract $DataOnly
    if(-not(Test-IMCShadowNumber $Storage.storage_bytes) -or
        -not(Test-IMCShadowNumber $Storage.disk_free_bytes) -or $Storage.disk_free_bytes -lt 5368709120){throw 'SHADOW_STORAGE_LIMIT'}
    $baseline=if($BaselineBytes -eq -1){[long]$Storage.storage_bytes}else{$BaselineBytes}
    if($baseline -gt $Storage.storage_bytes){throw 'SHADOW_BUDGET_INVALID'}
    if([long]$Storage.storage_bytes-$baseline -gt $MaxGrowthBytes){throw 'SHADOW_STORAGE_LIMIT'}
    return [pscustomobject]@{duration_seconds=$Seconds; started_at=$Now.ToString('o');
        planned_end=$Now.AddSeconds($Seconds).ToString('o');
        contract=($Contract|ConvertTo-Json -Compress -Depth 6); storage_baseline=$baseline;
        max_storage_growth_bytes=$MaxGrowthBytes;
        data_only=$DataOnly; source_execution_mode=$Contract.mode;
        automatic_learning_suppressed=$DataOnly;
        failures=0; last_source_id=0L; latest_observation=$null; stop_reason=$null;
        scan_identity=$null; scan_cycle_id=0L; scan_updated=$null;
        evidence_domain='DIAGNOSTIC_SHADOW_NOT_PERFORMANCE'; qualified_trading_ready=$false}
}
function Assert-IMCShadowContract($Contract,[bool]$DataOnly=$false) {
    $modes=if($DataOnly){@('SIGNAL_ONLY','THREE_TRADE_VALIDATION')}else{@('SIGNAL_ONLY')}
    if ($Contract.mode -cnotin $modes -or $Contract.broadcast -isnot [bool] -or $Contract.broadcast -or
        $Contract.signing -isnot [bool] -or $Contract.signing -or
        $Contract.live_trading -isnot [bool] -or $Contract.live_trading -or
        $Contract.owner_gate.valid -isnot [bool] -or -not $Contract.owner_gate.valid -or
        $Contract.owner_gate.value -cne 'OFF' -or
        $Contract.config_identity -cnotmatch '\A[a-f0-9]{64}\z') { throw 'SHADOW_CONTRACT_UNSAFE' }
}
function Test-IMCShadowTick($Session,$Contract,[double]$Elapsed,[datetime]$Now,$Report,$Storage) {
    Assert-IMCShadowContract $Contract $Session.data_only
    if (($Contract|ConvertTo-Json -Compress -Depth 6) -cne $Session.contract) { throw 'SHADOW_CONTRACT_CHANGED' }
    $wall=($Now-[datetime]::Parse($Session.started_at).ToUniversalTime()).TotalSeconds
    if ([double]::IsNaN($Elapsed) -or [double]::IsInfinity($Elapsed) -or $Elapsed -lt 0 -or
        [math]::Abs($wall-$Elapsed) -gt 3) { throw 'SHADOW_CLOCK_CHANGED' }
    if ($Elapsed -ge $Session.duration_seconds -or $Now -ge [datetime]::Parse($Session.planned_end).ToUniversalTime()) {
        $Session.stop_reason='DURATION_ELAPSED'; return $false
    }
    if(-not(Test-IMCShadowNumber $Storage.storage_bytes) -or
        -not(Test-IMCShadowNumber $Storage.disk_free_bytes) -or
        [long]$Storage.storage_bytes-$Session.storage_baseline -gt $Session.max_storage_growth_bytes -or
        $Storage.disk_free_bytes -lt 5368709120){throw 'SHADOW_STORAGE_LIMIT'}
    if ($null -ne $Report) {
        $Session.latest_observation=$Report
        $empty=($Report.no_candidates -is [bool] -and $Report.no_candidates -and
            $Report.shadow_targets -eq 0 -and $Report.shadow_observed -eq 0)
        $healthy=($Report.schema_version -eq 1 -and -not $Report.error -and
            [datetime]::Parse($Report.observed_at).ToUniversalTime() -ge [datetime]::Parse($Session.started_at).ToUniversalTime() -and
            ($Now-[datetime]::Parse($Report.observed_at).ToUniversalTime()).TotalSeconds -ge 0 -and
            ($Now-[datetime]::Parse($Report.observed_at).ToUniversalTime()).TotalSeconds -le 30 -and
            (Test-IMCShadowNumber $Report.source_age_seconds) -and ($Report.source_age_seconds -le 30 -or $empty) -and
            (Test-IMCShadowNumber $Report.watch_age_seconds) -and $Report.watch_age_seconds -le 30 -and
            (Test-IMCShadowNumber $Report.breadth_age_seconds) -and $Report.breadth_age_seconds -le 75 -and
            $Report.feed_complete -is [bool] -and $Report.feed_complete -and
            $Report.breadth_error -is [bool] -and -not $Report.breadth_error -and
            (Test-IMCShadowNumber $Report.shadow_targets) -and
            (Test-IMCShadowNumber $Report.shadow_observed) -and
            $Report.shadow_targets -eq $Report.shadow_observed -and
            (Test-IMCShadowNumber $Report.latest_id) -and
            ($Report.latest_id -gt $Session.last_source_id -or ($empty -and $Report.latest_id -eq $Session.last_source_id)) -and
            @($Report.watch_errors).Count -eq 0)
        if ($healthy) { $Session.failures=0; $Session.last_source_id=[long]$Report.latest_id } elseif ($Elapsed -ge 90) { $Session.failures++ }
        if ($Session.failures -ge 3) { throw 'SHADOW_DATA_UNHEALTHY' }
    }
    return $true
}
function Assert-IMCShadowHealth($Health,[datetime]$Now,[datetime]$Start,$Session=$null) {
    try {$age=($Now-[datetime]::Parse($Health.updated).ToUniversalTime()).TotalSeconds}
    catch {throw 'SHADOW_HEALTH_UNVERIFIED'}
    if($Health.configured_mode -cne 'DATA_ONLY_SHADOW' -or $age -lt 0 -or $age -gt 30 -or
        [datetime]::Parse($Health.updated).ToUniversalTime() -lt $Start -or
        @($Health.critical).Count -or $Health.process_snapshot.error -or
        @($Health.process_snapshot.unknown_interpreter_pids).Count){throw 'SHADOW_HEALTH_UNVERIFIED'}
    if($Health.version -cne '2.0' -or $Health.verdict -cnotin @('HEALTHY','DEGRADED') -or
        $Health.process_snapshot.visibility -cne 'RECOGNIZED_ABSOLUTE_PATHS'){throw 'SHADOW_HEALTH_UNVERIFIED'}
    foreach($name in @('core','safe100_watcher')) {
        $database=$Health.databases.$name
        if($database.readable -isnot [bool] -or -not $database.readable -or $database.error){throw 'SHADOW_HEALTH_UNVERIFIED'}
    }
    foreach($name in @('snapshots')){
        $stale=$Health.databases.core.($name+'_stale')
        if($stale -isnot [bool] -or ($stale -and -not(Test-IMCScanNoCandidates $Health.scan_status))){throw 'SHADOW_HEALTH_UNVERIFIED'}
    }
    $status=$Health.statuses.safe100_watcher
    if($status.exists -isnot [bool] -or -not $status.exists -or
        $status.stale -isnot [bool] -or $status.stale -or $status.last_error -or
        $status.saved_live_execution -eq $true){throw 'SHADOW_HEALTH_UNVERIFIED'}
    $roles=@($Health.process_snapshot.processes|ForEach-Object{$_.Role})
    foreach($role in @('Core','Watcher','Health','SignalSupervisor')){
        if(@($roles|Where-Object{$_ -ceq $role}).Count -ne 1){throw 'SHADOW_PROCESS_CONFLICT'}
    }
    if(@($roles|Where-Object{$_ -cnotin @('Core','Watcher','Health','SignalSupervisor','MLAutoOnce')}).Count){throw 'SHADOW_PROCESS_CONFLICT'}
    $scan=$Health.scan_status
    $core=@($Health.process_snapshot.processes|Where-Object Role -ceq 'Core')[0]
    foreach($name in @('schema_version','pid','cycle_id','evaluated')){
        if($scan.$name -isnot [int] -and $scan.$name -isnot [long]){throw 'SHADOW_SCAN_UNVERIFIED'}
    }
    $birth=0L;$actual=0L
    if($scan.verified -isnot [bool] -or -not $scan.verified -or $scan.error -or
        $scan.schema_version -ne 1 -or $scan.scan_success -isnot [bool] -or -not $scan.scan_success -or
        -not(Test-IMCShadowNumber $scan.pid) -or $scan.pid -ne $core.ProcessId -or
        $scan.process_birth_filetime -isnot [string] -or $scan.process_birth_filetime -cnotmatch '\A[1-9][0-9]{17}\z' -or
        $core.CreationFiletime -isnot [string] -or $core.CreationFiletime -cnotmatch '\A[1-9][0-9]{17}\z' -or
        -not [long]::TryParse($scan.process_birth_filetime,[ref]$birth) -or
        -not [long]::TryParse($core.CreationFiletime,[ref]$actual) -or [math]::Abs($birth-$actual) -ge 10 -or
        -not(Test-IMCShadowNumber $scan.cycle_id) -or $scan.cycle_id -lt 1 -or $scan.cycle_id -ne [long]$scan.cycle_id -or
        -not(Test-IMCShadowNumber $scan.cycle_seconds) -or -not(Test-IMCShadowNumber $scan.evaluated)) {throw 'SHADOW_SCAN_UNVERIFIED'}
    try{$updated=[datetimeoffset]::Parse($scan.updated_at).UtcDateTime;$scanAge=($Now-$updated).TotalSeconds}
    catch{throw 'SHADOW_SCAN_UNVERIFIED'}
    if($scanAge -lt 0 -or $scanAge -gt 30 -or $updated -lt $Start -or
        $updated -lt [datetime]::FromFileTimeUtc($birth)){throw 'SHADOW_SCAN_UNVERIFIED'}
    if($Session){
        $identity=([string]$scan.pid)+':'+$scan.process_birth_filetime
        if($Session.scan_identity -and ($Session.scan_identity -cne $identity -or
            $scan.cycle_id -lt $Session.scan_cycle_id -or $updated -lt [datetime]::Parse($Session.scan_updated).ToUniversalTime() -or
            ($scan.updated_at -cne $Session.scan_updated -and $scan.cycle_id -le $Session.scan_cycle_id) -or
            ($scan.updated_at -ceq $Session.scan_updated -and $scan.cycle_id -ne $Session.scan_cycle_id))){throw 'SHADOW_SCAN_REPLAY'}
        $Session.scan_identity=$identity;$Session.scan_cycle_id=[long]$scan.cycle_id;$Session.scan_updated=$scan.updated_at
    }
}
