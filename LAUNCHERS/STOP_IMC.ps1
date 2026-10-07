$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'IMC_PROCESS_COMMON.ps1')
Stop-IMCRoles
Publish-IMCStoppedStatus
Write-Host 'IMC: all identified services stopped and verified.'
