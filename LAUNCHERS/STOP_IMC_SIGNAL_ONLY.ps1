$ErrorActionPreference = 'Stop'
# Compatibility alias: the canonical stop also stops restart supervisors.
& (Join-Path $PSScriptRoot 'STOP_IMC.ps1')
