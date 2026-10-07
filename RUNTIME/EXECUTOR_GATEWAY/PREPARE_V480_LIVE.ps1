$ErrorActionPreference='Stop'
. (Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'LAUNCHERS\IMC_PYTHON_COMMON.ps1')
$Python = Get-IMCPython
$d=Split-Path -Parent $MyInvocation.MyCommand.Path
& $Python (Join-Path $d 'prepare_v480_calibration.py')
if($LASTEXITCODE -ne 0){ exit $LASTEXITCODE }
