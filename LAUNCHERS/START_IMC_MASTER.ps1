$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'IMC_PYTHON_COMMON.ps1')
$Python = Get-IMCPython
$Root='%USERPROFILE%\Desktop\IMC_TRADING_MAIN'
$GW=Join-Path $Root 'RUNTIME\EXECUTOR_GATEWAY'
$MasterPath=Join-Path $GW 'IMC_MASTER_MODE.json'
$V490Path=Join-Path $GW 'LIVE_V490\config_v490_live.json'
$V480Path=Join-Path $GW 'config_v480.json'
$Prep=Join-Path $Root 'LAUNCHERS\START_IMC_AUTONOMOUS_PREP.ps1'

foreach($f in @($MasterPath,$V490Path,$V480Path,$Prep)){
 if(-not (Test-Path $f)){throw "IMC file missing: $f"}
}
$m=Get-Content $MasterPath -Raw | ConvertFrom-Json
$LiveRequested=[bool]$m.live_broadcast

$v490=Get-Content $V490Path -Raw | ConvertFrom-Json
$v490.broadcast_enabled=$false
$v490.require_local_live_confirm=$true
$v490 | ConvertTo-Json -Depth 20 | Set-Content $V490Path -Encoding UTF8

$v480=Get-Content $V480Path -Raw | ConvertFrom-Json
$v480.broadcast_default=$false
$v480 | ConvertTo-Json -Depth 20 | Set-Content $V480Path -Encoding UTF8
$ReadyPath=Join-Path $GW 'IMC_LIVE_READY.json'
[pscustomobject]@{
 live_requested=$LiveRequested
 mode=if($LiveRequested){'LIVE_READY'}else{'NO_BROADCAST'}
 broadcast_active=$false
 final_manual_approval_required=$LiveRequested
 updated_at=(Get-Date).ToString('o')
} | ConvertTo-Json | Set-Content $ReadyPath -Encoding UTF8

Write-Host ''
Write-Host '=== IMC MASTER START ===' -ForegroundColor Cyan
Write-Host ('Master-Schalter : live_broadcast = '+$m.live_broadcast)
if($LiveRequested){
 Write-Host 'Mode            : LIVE_READY' -ForegroundColor Yellow
 Write-Host 'IMC-Stack       : startet komplett'
 Write-Host 'Broadcast       : wartet auf separate manuelle Live-Freigabe'
}else{
 Write-Host 'Mode            : NO_BROADCAST' -ForegroundColor Green
 Write-Host 'IMC-Stack       : startet komplett ohne Blockchain-Broadcast'
}
Write-Host ''

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Prep
exit $LASTEXITCODE
