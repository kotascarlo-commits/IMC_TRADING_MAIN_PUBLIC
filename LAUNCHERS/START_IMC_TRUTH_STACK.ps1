$ErrorActionPreference='Stop'
$Master='%USERPROFILE%\Desktop\IMC_TRADING_MAIN\LAUNCHERS\START_IMC_MASTER.ps1'
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Master
exit $LASTEXITCODE
