$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'IMC_PYTHON_COMMON.ps1')
. (Join-Path $PSScriptRoot 'IMC_PROCESS_COMMON.ps1')
$Root=Split-Path -Parent $PSScriptRoot
. (Join-Path $Root 'TOOLS\IMC_MAINTENANCE.ps1')
$Python=Get-IMCPythonPath
$startFence=Enter-IMCMaintenanceFence -Python $Python -Purpose Start
try {
$snapshot=Get-IMCProcessSnapshot
if ($snapshot.UnknownInterpreterPids.Count) { throw 'Runtime process identity UNKNOWN.' }
$supervisors=@($snapshot.Processes | Where-Object { $_.Role -eq 'SignalSupervisor' })
if ($supervisors.Count -gt 1) { throw 'Multiple signal supervisors.' }
if (-not $supervisors.Count) {
    if ($snapshot.Processes.Count) { throw 'Existing IMC runtime conflicts; no process was stopped.' }
    $Python=Get-IMCPython
    $runner=Join-Path $PSScriptRoot 'RUN_IMC_SIGNAL_ONLY.ps1'
    # Release the START check before asynchronous RUN; RUN acquires its own
    # fence and rereads persistent state before any broker or worker starts.
    Exit-IMCMaintenanceFence $startFence;$startFence=$null
    $shell=Join-Path ([Environment]::GetFolderPath('System')) 'WindowsPowerShell\v1.0\powershell.exe'
    Start-Process $shell -WindowStyle Hidden -ArgumentList @('-NoProfile','-NonInteractive',
        '-ExecutionPolicy','Bypass','-File',('"'+$runner+'"')) | Out-Null
}
Write-Output 'IMC supervised runtime start requested; persisted execution gates remain authoritative.'
} finally {if($startFence){Exit-IMCMaintenanceFence $startFence}}
