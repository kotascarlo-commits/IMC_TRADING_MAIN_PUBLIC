$ErrorActionPreference='Stop'
. (Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'LAUNCHERS\IMC_PYTHON_COMMON.ps1')
$Python = Get-IMCPython
$d=Split-Path -Parent $MyInvocation.MyCommand.Path
$root=Split-Path -Parent $d
$twin=Get-Content (Join-Path $root 'CURRENT_EXECUTION_TWIN.json') -Raw | ConvertFrom-Json
if($twin.current_execution_lane -ne 'IMC_EXECUTION_BUILD_PARITY_v4.8.0'){ throw 'Falscher Execution-Twin.' }
if($twin.legacy_executor_hardlocked -ne $true){ throw 'Legacy-Executor muss gesperrt sein.' }
& node (Join-Path $d 'wallet_verify.js') | Out-Null
Get-CimInstance Win32_Process | Where-Object {
 $_.CommandLine -and $_.CommandLine -like '*real_market_execution_v480_build_parity.py*'
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Process -WindowStyle Hidden -FilePath $Python -ArgumentList '-u',('"'+(Join-Path $root 'real_market_execution_v480_build_parity.py')+'"')
Start-Sleep -Seconds 2
$p=Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine -like '*real_market_execution_v480_build_parity.py*' }
if(-not $p){ throw 'v4.8 Simulation konnte nicht gestartet werden.' }
[pscustomobject]@{
 State='V480_SIM_RUNNING'
 Authority='0%'
 Broadcast='OFF'
 Lane=$twin.current_execution_lane
 SignedPath=$twin.signed_no_broadcast_verified
} | Format-List
