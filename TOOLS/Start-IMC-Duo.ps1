$ErrorActionPreference = 'Stop'

$work = '%USERPROFILE%\Desktop\IMC_TRADING_MAIN\TOOLS\Start-IMC-Work.ps1'

Write-Host 'IMC: starte Codex/Sol HIGH ...'
Write-Host 'IMC: Astra High wird ueber den Read-only-Host-Broker des Hauptagenten konsultiert.'
& (Join-Path $PSHOME 'powershell.exe') -NoProfile -ExecutionPolicy Bypass -File $work -Level complex
exit $LASTEXITCODE
