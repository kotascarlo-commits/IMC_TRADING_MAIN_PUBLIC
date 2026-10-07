param([ValidateSet('Preflight','Prepare','Validate','PrepareValidation','Start','Stop','Status')][string]$Action='Status')
$ErrorActionPreference='Stop'
$Root=Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'IMC_PYTHON_COMMON.ps1')
. (Join-Path $PSScriptRoot 'IMC_PROCESS_COMMON.ps1')
$Python=Get-IMCPython
$status=Join-Path $Root 'RUNTIME\jupiter-supervisor-status.json'
switch($Action){
 'Preflight' { & (Join-Path $PSScriptRoot 'RUN_JUPITER_FORWARD.ps1') -PreflightOnly; if($LASTEXITCODE){exit $LASTEXITCODE} }
 'Prepare' { & (Join-Path $PSScriptRoot 'RUN_JUPITER_FORWARD.ps1') -PrepareOnly; if($LASTEXITCODE){exit $LASTEXITCODE} }
 'Validate' { & (Join-Path $PSScriptRoot 'RUN_JUPITER_FORWARD.ps1') -ValidationOnly; if($LASTEXITCODE){exit $LASTEXITCODE} }
 'PrepareValidation' { & (Join-Path $PSScriptRoot 'RUN_JUPITER_FORWARD.ps1') -InitializeValidationOnly; if($LASTEXITCODE){exit $LASTEXITCODE} }
 'Start' {
    $runner=Join-Path $PSScriptRoot 'RUN_JUPITER_FORWARD.ps1'
    $shell=Join-Path ([Environment]::GetFolderPath('System')) 'WindowsPowerShell\v1.0\powershell.exe'
    Start-Process $shell -WindowStyle Hidden -ArgumentList @('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',('"'+$runner+'"'))|Out-Null
    Write-Output 'Jupiter live-market quote-forward start requested; no signing or broadcast.'
 }
 'Stop' {
    if(-not(Test-Path -LiteralPath $status)){Write-Output 'No Jupiter supervisor status.';return}
    $value=Get-Content -LiteralPath $status -Raw -Encoding UTF8|ConvertFrom-Json
    $snapshot=Get-IMCProcessSnapshot
    if($snapshot.UnknownInterpreterPids.Count){throw 'JUPITER_PROCESS_IDENTITY_UNKNOWN'}
    $bound=@($snapshot.Processes|Where-Object {$_.Role -in @('JupiterForwardSupervisor','JupiterForward')})
    if($value.state -ceq 'STOPPED'){
        if($bound.Count){throw 'JUPITER_STOP_STATUS_IS_STALE'}
        Write-Output 'No Jupiter forward runtime process remains.';return
    }
    $row=@($bound|Where-Object {$_.ProcessId -eq $value.pid -and $_.Role -ceq 'JupiterForwardSupervisor'})
    if($row.Count -ne 1){throw 'JUPITER_SUPERVISOR_IDENTITY_UNKNOWN'}
    $handle=Get-Process -Id $value.pid -ErrorAction Stop
    $null=$handle.Handle
    if($handle.StartTime.ToUniversalTime().Ticks -ne [long]$value.process_start_ticks){$handle.Dispose();throw 'JUPITER_SUPERVISOR_IDENTITY_CHANGED'}
    $target=[IO.Path]::GetFullPath([string]$value.stop_file)
    $directory=[IO.Path]::GetFullPath((Join-Path $Root 'RUNTIME')).TrimEnd('\')+'\'
    if(-not $target.StartsWith($directory,[StringComparison]::OrdinalIgnoreCase) -or
       [IO.Path]::GetFileName($target) -cnotmatch '^jupiter-forward-[a-f0-9]{32}\.stop$'){throw 'JUPITER_STOP_PATH_INVALID'}
    [IO.File]::WriteAllText($target,'STOP_REQUESTED')
    $deadline=[DateTime]::UtcNow.AddSeconds(20)
    do {
        Start-Sleep -Milliseconds 200
        $current=Get-Content -LiteralPath $status -Raw -Encoding UTF8|ConvertFrom-Json
        if($current.pid -ne $value.pid -or $current.started_at -cne $value.started_at -or $current.stop_file -cne $value.stop_file){$handle.Dispose();throw 'JUPITER_SUPERVISOR_SESSION_CHANGED'}
        if($current.state -ceq 'STOPPED' -and $current.code -ceq 'NATIVE_CLEANUP_CONFIRMED' -and $handle.WaitForExit(1000)){
            $handle.Dispose()
            $remaining=Get-IMCProcessSnapshot
            if($remaining.UnknownInterpreterPids.Count -or @($remaining.Processes|Where-Object {$_.Role -in @('JupiterForwardSupervisor','JupiterForward')}).Count){throw 'JUPITER_NATIVE_CLEANUP_UNCONFIRMED'}
            Write-Output 'Jupiter forward native cleanup confirmed.';return
        }
    }while([DateTime]::UtcNow -lt $deadline)
    $handle.Dispose();throw 'JUPITER_STOP_NOT_CONFIRMED'
 }
 'Status' {
    if(Test-Path -LiteralPath $status){Get-Content -LiteralPath $status -Encoding UTF8}
    $ready=Join-Path $Root 'RUNTIME\jupiter-forward-readiness.json'
    if(Test-Path -LiteralPath $ready){Write-Output 'Historical readiness observation; Start requires a new preflight.';Get-Content -LiteralPath $ready -Encoding UTF8}
    if(Test-Path -LiteralPath (Join-Path $Root 'RUNTIME\jupiter-forward.db')){
        & $Python (Join-Path $Root 'RUNTIME\jupiter_forward_runtime.py') --mode status
    }
 }
}
