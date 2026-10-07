param([Parameter(Mandatory)][string]$Duration,[Parameter(Mandatory)][ValidatePattern('\A[0-9a-f]{32}\z')][string]$RequestId)
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'IMC_PYTHON_COMMON.ps1')
. (Join-Path $PSScriptRoot 'IMC_PROCESS_COMMON.ps1')
. (Join-Path $root 'TOOLS\IMC_RUNTIME_JOB.ps1')
. (Join-Path $root 'TOOLS\IMC_MAINTENANCE.ps1')
$python=Get-IMCPython
$entry=Join-Path $root 'RUNTIME\reality_runtime.py'
$mutex=[Threading.Mutex]::new($false,'Global\IMC_TRADING_MAIN_RUNTIME_V1')
$owned=$false;$job=$null;$fence=$null;$runId=$null;$recoveryOnly=$false
$result=Join-Path $root ('RUNTIME\reality-start-'+$RequestId+'.json')
function Write-RealityStartResult($Value) {
    # Publish one complete JSON document; a polling console must never read a
    # partially written notification. The ledger remains the source of truth.
    $temporary=$result+'.'+[guid]::NewGuid().ToString('N')+'.tmp'
    [IO.File]::WriteAllText($temporary,($Value|ConvertTo-Json),[Text.UTF8Encoding]::new($false))
    if([IO.File]::Exists($result)){[IO.File]::Replace($temporary,$result,$null)}
    else{[IO.File]::Move($temporary,$result)}
}
try {
    $fence=Enter-IMCMaintenanceFence -Python $python -Purpose Start
    try {$owned=$mutex.WaitOne(0)} catch [Threading.AbandonedMutexException] {$owned=$true}
    if(-not $owned){throw 'IMC_RUNTIME_ALREADY_OWNED'}
    $snapshot=Get-IMCProcessSnapshot
    $foreign=@($snapshot.Processes | Where-Object {$_.ProcessId -ne $PID})
    if($snapshot.UnknownInterpreterPids.Count -or $foreign.Count){throw 'IMC_RUNTIME_CONFLICT_NO_PROCESS_STOPPED'}
    $old=(& $python $entry status | ConvertFrom-Json)
    if($LASTEXITCODE -ne 0){throw 'REALITY_STATUS_UNREADABLE'}
    if($old.status -in @('STARTING','RUNNING','FINISHING','BLOCKED')) {
        # Owning the global mutex proves there is no cooperating old runtime.
        # Recovery never replays an unfinished BUY/SELL claim.
        & $python $entry recover --run-id $old.run_id | Out-Null
        if($LASTEXITCODE -ne 0){throw 'REALITY_RECOVERY_FAILED'}
        if(@($old.claims | Where-Object state -eq 'CLAIMED').Count){throw 'REALITY_UNKNOWN_CLAIM_NO_RESEND'}
        if($old.open_position){
            if($old.open_position.execution_state -cne 'POSITION_OPEN'){throw 'REALITY_UNKNOWN_POSITION_NO_RESEND'}
            $runId=$old.run_id;$recoveryOnly=$true
        }else{
            & $python $entry run --run-id $old.run_id | Out-Null
            if($LASTEXITCODE -ne 0){throw 'REALITY_RECOVERY_FINISH_FAILED'}
        }
    }
    if(-not $recoveryOnly){
        $ids=@(& $python $entry create --duration $Duration --request-id $RequestId)
        if($LASTEXITCODE -ne 0 -or $ids.Count -ne 1 -or $ids[0] -cnotmatch '\A[0-9a-f]{32}\z'){throw 'REALITY_CREATE_FAILED'}
        $runId=$ids[0]
    }
    $job=[IMCRuntimeJob]::new()
    $child=$job.Start($python,[string[]]@('-u',$entry,'run','--run-id',$runId),$root)
    Write-RealityStartResult @{status='STARTED';run_id=$runId;recovery_only=$recoveryOnly}
    while($job.Running($child)){Start-Sleep -Milliseconds 250}
    $exitCode=$job.ExitCode($child)
    $job.Release($child)
    if($exitCode -ne 0){
        & $python $entry recover --run-id $runId | Out-Null
        throw 'REALITY_RUNTIME_FAILED_RECOVERY_PERSISTED'
    }
} catch {
    Write-RealityStartResult @{status='BLOCKED';reason=$_.Exception.Message;run_id=$runId}
    throw
} finally {
    $cleanup=$true
    if($job){$job.ShutdownWithin(10000);$cleanup=$job.CleanupConfirmed}
    if(-not $cleanup){throw 'REALITY_NATIVE_CLEANUP_UNCONFIRMED'}
    if($runId){& $python $entry cleanup --run-id $runId | Out-Null;if($LASTEXITCODE -ne 0){throw 'REALITY_CLEANUP_PERSIST_FAILED'}}
    if($owned){$mutex.ReleaseMutex()};$mutex.Dispose()
    if($fence){Exit-IMCMaintenanceFence $fence}
}
