param([switch]$PreflightOnly,[switch]$PrepareOnly,[switch]$ValidationOnly,[switch]$InitializeValidationOnly)
$ErrorActionPreference='Stop'
$Root=Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'IMC_PYTHON_COMMON.ps1')
. (Join-Path $PSScriptRoot 'IMC_PROCESS_COMMON.ps1')
. (Join-Path $Root 'TOOLS\IMC_RUNTIME_JOB.ps1')
. (Join-Path $Root 'TOOLS\IMC_MAINTENANCE.ps1')
$Python=Get-IMCPython
$purpose=if($InitializeValidationOnly){'Maintenance'}else{'Start'}
$fence=Enter-IMCMaintenanceFence -Python $Python -Purpose $purpose
$mutex=[Threading.Mutex]::new($false,'Global\IMC_TRADING_MAIN_RUNTIME_V1')
$owned=$false;$job=$null
$entry=Join-Path $Root 'RUNTIME\jupiter_forward_runtime.py'
$status=Join-Path $Root 'RUNTIME\jupiter-supervisor-status.json'
$stop=Join-Path $Root ('RUNTIME\jupiter-forward-'+[guid]::NewGuid().ToString('N')+'.stop')
$script:started=[DateTimeOffset]::UtcNow.ToString('o')
$script:processStartTicks=[Diagnostics.Process]::GetCurrentProcess().StartTime.ToUniversalTime().Ticks
$script:outcome='STARTING';$script:outcomeCode='WAITING_FOR_FRESH_SOURCE'
$script:cleanupCode=$null
function Publish-JupiterSupervisor($state,$code) {
    $document=@{schema_version=1;state=$state;code=$code;pid=$PID;
      process_start_ticks=[string]$script:processStartTicks;
      outcome=$script:outcome;outcome_code=$script:outcomeCode;cleanup_code=$script:cleanupCode;
      started_at=$script:started;updated_at=[DateTimeOffset]::UtcNow.ToString('o');
      stop_file=$stop;preflight_only=[bool]$PreflightOnly;prepare_only=[bool]$PrepareOnly;broadcast_enabled=$false;signing=$false} | ConvertTo-Json -Depth 4
    $temporary=$status+'.'+[guid]::NewGuid().ToString('N')+'.tmp'
    [IO.File]::WriteAllText($temporary,$document,[Text.UTF8Encoding]::new($false))
    if(Test-Path -LiteralPath $status){[IO.File]::Replace($temporary,$status,[Management.Automation.Language.NullString]::Value)}
    else{[IO.File]::Move($temporary,$status)}
}
try {
    try{$owned=$mutex.WaitOne(0)}catch [Threading.AbandonedMutexException]{$owned=$true}
    if(-not $owned){throw 'IMC_RUNTIME_ALREADY_OWNED'}
    $snapshot=Get-IMCProcessSnapshot
    $foreign=@($snapshot.Processes | Where-Object {$_.ProcessId -ne $PID})
    if($snapshot.UnknownInterpreterPids.Count -or $foreign.Count){throw 'IMC_RUNTIME_CONFLICT_NO_PROCESS_STOPPED'}
    if($InitializeValidationOnly){
        if($PreflightOnly -or $PrepareOnly -or $ValidationOnly){throw 'JUPITER_AMBIGUOUS_MODE'}
        $existing=Read-IMCMaintenanceState $Python
        if($existing.state -ceq 'ACTIVE'){Recover-IMCMaintenanceActive $fence $Python}
        else{Set-IMCMaintenanceActive $fence $Python ([guid]::NewGuid().ToString('N'))}
    }
    $script:started=[DateTimeOffset]::UtcNow.ToString('o')
    $job=[IMCRuntimeJob]::new()
    if($PrepareOnly -or $ValidationOnly -or $InitializeValidationOnly){
        if($PreflightOnly){throw 'JUPITER_AMBIGUOUS_MODE'}
        if($PrepareOnly -and $ValidationOnly){throw 'JUPITER_AMBIGUOUS_MODE'}
        Publish-JupiterSupervisor 'PREPARING' 'UNSIGNED_SIMULATION_ONLY'
        $mode=if($ValidationOnly){'validate'}elseif($InitializeValidationOnly){'initialize-validation'}else{'prepare'}
        $arguments=[string[]]@('-u',$entry,'--mode',$mode)
        if($InitializeValidationOnly){$arguments+=@('--maintenance-nonce',$fence.ActiveNonce)}
        $check=$job.Start($Python,$arguments,$Root)
        $deadline=[DateTime]::UtcNow.AddSeconds(65)
        while($job.Running($check) -and [DateTime]::UtcNow -lt $deadline){Start-Sleep -Milliseconds 200}
        if($job.Running($check)){throw 'JUPITER_PREPARATION_TIMEOUT'}
        $result=$job.ExitCode($check);$job.Release($check)
        if($result -ne 0){throw 'JUPITER_PREPARATION_BLOCKED'}
        $script:outcome='VERIFIED';$script:outcomeCode=if($ValidationOnly){'ISOLATED_VALIDATION_PASS'}elseif($InitializeValidationOnly){'VALIDATION_PREPARED_GATES_OFF'}else{'UNSIGNED_SIMULATION_ONLY'}
        Publish-JupiterSupervisor 'VERIFIED' $script:outcomeCode;return
    }
    $feed=$job.Start($Python,[string[]]@('-u',$entry,'--mode','feed','--stop-file',$stop),$Root)
    Publish-JupiterSupervisor 'STARTING' 'WAITING_FOR_FRESH_SOURCE'
    $deadline=[DateTime]::UtcNow.AddSeconds(45)
    $fresh=$false
    while([DateTime]::UtcNow -lt $deadline){
        if(-not $job.Running($feed)){throw 'JUPITER_FEED_EXITED'}
        $feedStatus=Join-Path $Root 'RUNTIME\jupiter-feed-status.json'
        if(Test-Path -LiteralPath $feedStatus){
            try{
                $value=Get-Content -LiteralPath $feedStatus -Raw -Encoding UTF8|ConvertFrom-Json
                $age=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000-$value.updated_at
                if($value.status -ceq 'PASS' -and $age -ge 0 -and $age -lt 10 -and
                    $value.updated_at -ge ([DateTimeOffset]::Parse($script:started).ToUnixTimeMilliseconds()/1000)){$fresh=$true;break}
            }catch{}
        }
        Start-Sleep -Milliseconds 200
    }
    if(-not $fresh){throw 'JUPITER_SOURCE_NOT_FRESH'}
    # This child also belongs to the native Job; even DNS/HTTP hangs cannot orphan it.
    $check=$job.Start($Python,[string[]]@('-u',$entry,'--mode','preflight'),$Root)
    $deadline=[DateTime]::UtcNow.AddSeconds(45)
    while($job.Running($check) -and [DateTime]::UtcNow -lt $deadline){Start-Sleep -Milliseconds 200}
    if($job.Running($check)){throw 'JUPITER_PREFLIGHT_TIMEOUT'}
    $result=$job.ExitCode($check);$job.Release($check)
    if($result -ne 0){throw 'JUPITER_PREFLIGHT_BLOCKED'}
    if($PreflightOnly){$script:outcome='VERIFIED';$script:outcomeCode='QUOTE_PAPER_ONLY';Publish-JupiterSupervisor 'VERIFIED' 'QUOTE_PAPER_ONLY';return}
    $runner=$job.Start($Python,[string[]]@('-u',$entry,'--mode','run','--stop-file',$stop),$Root)
    # Keep the maintenance fence for the whole owned feed/forward session;
    # release it only after native cleanup, never while a DB writer survives.
    Publish-JupiterSupervisor 'RUNNING' 'JUPITER_QUOTE_FORWARD_NO_BROADCAST'
    $script:outcome='RUNNING';$script:outcomeCode='JUPITER_QUOTE_FORWARD_NO_BROADCAST'
    while(-not(Test-Path -LiteralPath $stop)){
        if(-not $job.Running($feed)){throw 'JUPITER_FEED_EXITED'}
        if(-not $job.Running($runner)){
            $result=$job.ExitCode($runner)
            if($result -ne 0){throw 'JUPITER_FORWARD_EXITED_ERROR'}
            $script:outcome='FINISHED';$script:outcomeCode='FORWARD_RUNNER_FINISHED'
            break
        }
        Publish-JupiterSupervisor 'RUNNING' 'JUPITER_QUOTE_FORWARD_NO_BROADCAST'
        Start-Sleep -Seconds 1
    }
    if(Test-Path -LiteralPath $stop){$script:outcome='STOPPED';$script:outcomeCode='STOP_REQUESTED'}
}catch{
    $script:outcome='BLOCKED';$script:outcomeCode=[string]$_.Exception.Message
    Publish-JupiterSupervisor 'BLOCKED' ([string]$_.Exception.Message)
    throw
}finally{
    $cleanup=$false
    if($job){$job.ShutdownWithin(10000);$cleanup=$job.CleanupConfirmed}else{$cleanup=$true}
    if(-not $cleanup){throw 'JUPITER_NATIVE_CLEANUP_UNCONFIRMED'}
    if($InitializeValidationOnly -and $fence.ActiveNonce -and $script:outcome -ceq 'VERIFIED'){
        Complete-IMCMaintenance $fence $Python $true $true $cleanup
    }
    if($owned){$mutex.ReleaseMutex()};$mutex.Dispose()
    if($fence){
        if($fence.ActiveNonce){
            # Preserve the original BLOCKED cause and durable ACTIVE marker.
            # The ending process releases its native handle; later START stays blocked.
            $script:cleanupCode='MAINTENANCE_ACTIVE_RETAINED'
            if($fence.SnapshotGuard){
                try{Close-IMCMaintenanceSnapshot $fence}
                catch{$script:cleanupCode='MAINTENANCE_SNAPSHOT_CLOSE_UNCONFIRMED'}
            }
            if($fence.Owned){$fence.Mutex.ReleaseMutex();$fence.Owned=$false}
            $fence.Mutex.Dispose()
        }else{Exit-IMCMaintenanceFence $fence}
    }
    if($cleanup){
        if($script:outcome -ceq 'BLOCKED'){Publish-JupiterSupervisor 'BLOCKED' $script:outcomeCode}
        else{Publish-JupiterSupervisor 'STOPPED' 'NATIVE_CLEANUP_CONFIRMED'}
    }
}
