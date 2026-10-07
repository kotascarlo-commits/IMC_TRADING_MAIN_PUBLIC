$ErrorActionPreference = 'Stop'
# Canonical default matches START_IMC.bat: the existing signal-only pipeline.
& (Join-Path $PSScriptRoot 'START_IMC_SIGNAL_ONLY.ps1')
