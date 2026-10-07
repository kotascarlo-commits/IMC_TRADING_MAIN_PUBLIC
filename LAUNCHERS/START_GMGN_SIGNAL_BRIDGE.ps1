$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'IMC_PYTHON_COMMON.ps1')
$Python = Get-IMCPython
$Root='%USERPROFILE%\Desktop\IMC_TRADING_MAIN'
$Bridge=Join-Path $Root 'RUNTIME\EXECUTOR_GATEWAY\GMGN_BRIDGE\gmgn_signal_bridge.py'
$all=Get-CimInstance Win32_Process
if(-not ($all|Where-Object{$_.CommandLine -and $_.CommandLine -like '*gmgn_signal_bridge.py*'})){
 Start-Process $Python -WindowStyle Hidden -ArgumentList '-u',('"'+$Bridge+'"')
}
Start-Sleep -Seconds 1
Write-Host 'GMGN Signal Bridge: RUNNING'
Write-Host 'Execution: OFF'
