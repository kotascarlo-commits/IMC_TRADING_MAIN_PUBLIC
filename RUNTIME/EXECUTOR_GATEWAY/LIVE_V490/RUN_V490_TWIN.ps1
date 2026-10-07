$ErrorActionPreference='Stop'
$Base=Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
. (Join-Path $Base 'LAUNCHERS\IMC_PYTHON_COMMON.ps1')
$Python = Get-IMCPython
$Runtime=Join-Path $Base 'RUNTIME'
$env:IMC_EXEC_DB=Join-Path $Runtime 'real-forward-v490-live-twin.db'
$env:IMC_EXEC_STATUS=Join-Path $PSScriptRoot 'v490_twin_status.json'
$env:IMC_EXEC_STATE=Join-Path $PSScriptRoot 'v490_twin_state.json'
$env:IMC_BASELINE_TARGET='999999'
$env:IMC_CONTINUOUS='1'
& $Python -u (Join-Path $Runtime 'real_market_execution_v480_build_parity.py') '--lane=v490-3trade'
