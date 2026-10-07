param(
    [Parameter(Mandatory)][string]$OutputDirectory,
    [Parameter(Mandatory)][ValidatePattern('\A[1-9A-HJ-NP-Za-km-z]{32,44}\z')][string]$QuoteWallet,
    [ValidateRange(30,43200)][int]$DurationSeconds=3600,
    [ValidateRange(0,180)][int]$DrainSeconds=180
)
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$Root=Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'IMC_PROCESS_COMMON.ps1')
. (Join-Path $PSScriptRoot 'IMC_PYTHON_COMMON.ps1')
. (Join-Path $Root 'TOOLS\IMC_RUNTIME_JOB.ps1')
$output=[IO.Path]::GetFullPath($OutputDirectory)
$prefix=Join-Path $Root 'RUNTIME\test-runs\'
if($OutputDirectory -ine $output -or -not $output.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase)){
    throw 'PAPER_OUTPUT_OUTSIDE_AUDIT_ROOT'
}
$snapshot=Get-IMCProcessSnapshot
if($snapshot.Processes.Count -gt 1 -or @($snapshot.Processes|Where-Object ProcessId -ne $PID).Count -or
    $snapshot.InterpreterRedirectors.Count -or $snapshot.UnknownInterpreterPids.Count){throw 'PAPER_RUNTIME_CONFLICT'}
$null=[IO.Directory]::CreateDirectory($output)
$status=Join-Path $output 'paper-status.json'
$database=Join-Path $output 'paper.db'
if(Test-Path -LiteralPath $database){throw 'PAPER_FRESH_COHORT_REQUIRED'}
$python=Get-IMCPythonPath
$env:PYTHONPATH=Join-Path $Root 'CORE'
$env:IMC_SHADOW_DATA_ONLY='1'
$clock=[Diagnostics.Stopwatch]::StartNew()
$job=[IMCRuntimeJob]::new();$worker=$null;$birth=$null;$reason=$null;$cleanup=$false
try{
    $workerPid=$job.Start($python,[string[]]@('-u','-m','mykonos_meme_scout.paper_forward',
        '--database',$database,'--status-path',$status,'--virtual-eur','1000','--quote-wallet',$QuoteWallet,
        '--collect','--duration-seconds',[string]$DurationSeconds,'--drain-seconds',[string]$DrainSeconds),$Root)
    $worker=Get-Process -Id $workerPid -ErrorAction Stop;$null=$worker.Handle
    $birth=$worker.StartTime.ToUniversalTime().ToFileTimeUtc().ToString()
    $baseline=0L
    foreach($name in @('CORE\data\imc-main.db','CORE\data\imc-main.db-wal','CORE\data\imc-main.db-shm')){
        $file=Get-Item -LiteralPath (Join-Path $Root $name) -ErrorAction SilentlyContinue
        if($file){$baseline+=$file.Length}
    }
    while($job.Running($workerPid)){
        if($clock.Elapsed.TotalSeconds -gt 30+$DurationSeconds+$DrainSeconds+10){throw 'PAPER_NATIVE_DEADLINE'}
        $drive=[IO.DriveInfo]::new([IO.Path]::GetPathRoot($Root));$storage=0L
        foreach($name in @('CORE\data\imc-main.db','CORE\data\imc-main.db-wal','CORE\data\imc-main.db-shm')){
            $file=Get-Item -LiteralPath (Join-Path $Root $name) -ErrorAction SilentlyContinue
            if($file){$storage+=$file.Length}
        }
        if($drive.AvailableFreeSpace -lt 5368709120 -or $storage-$baseline -gt 6442450944){throw 'PAPER_STORAGE_LIMIT'}
        Start-Sleep -Milliseconds 500
    }
    $worker.WaitForExit(1000)|Out-Null
    if(-not $worker.HasExited){throw 'PAPER_WORKER_EXIT_UNVERIFIED'}
    $reason='WORKER_EXIT_'+$worker.ExitCode
}catch{
    $reason=if($_.Exception.Message -cmatch '\APAPER_[A-Z0-9_]+\z'){$_.Exception.Message}else{'PAPER_LAUNCHER_ERROR'}
    throw
}finally{
    try{$job.Dispose();$cleanup=$job.CleanupConfirmed}finally{
        $exited=($worker -and $worker.WaitForExit(5000) -and $worker.HasExited)
        $proof=[ordered]@{observed_at=[DateTimeOffset]::UtcNow.ToString('o');supervisor_pid=$PID;
            worker_pid=$(if($worker){$worker.Id}else{0});worker_birth_filetime=$birth;
            worker_exit_confirmed=[bool]$exited;job_cleanup_confirmed=[bool]$cleanup;
            reason=$reason;native_elapsed_seconds=$clock.Elapsed.TotalSeconds;source='HELD_NATIVE_HANDLE_AND_WINDOWS_JOB';
            broadcast_enabled=$false;signing=$false;live_trading_enabled=$false}
        [IO.File]::WriteAllText((Join-Path $output 'native-stop-proof.json'),($proof|ConvertTo-Json),[Text.UTF8Encoding]::new($false))
        if($exited -and $cleanup -and (Test-Path -LiteralPath $status)){
            $doc=Get-Content -LiteralPath $status -Raw -Encoding UTF8|ConvertFrom-Json
            $doc.runtime_state='STOPPED_NATIVE_CONFIRMED'
            $doc|Add-Member -NotePropertyName native_stop_confirmed_at -NotePropertyValue $proof.observed_at -Force
            [IO.File]::WriteAllText($status,($doc|ConvertTo-Json -Depth 12),[Text.UTF8Encoding]::new($false))
        }
        if($worker){$worker.Dispose()}
    }
}
if(-not $cleanup){throw 'PAPER_JOB_CLEANUP_UNVERIFIED'}
if($reason -ne 'WORKER_EXIT_0'){throw $reason}
