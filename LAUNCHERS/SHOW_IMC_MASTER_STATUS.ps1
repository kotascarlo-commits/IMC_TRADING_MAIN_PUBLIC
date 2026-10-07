$Root='%USERPROFILE%\Desktop\IMC_TRADING_MAIN'
$GW=Join-Path $Root 'RUNTIME\EXECUTOR_GATEWAY'
$m=Get-Content (Join-Path $GW 'IMC_MASTER_MODE.json') -Raw | ConvertFrom-Json
$v490=Get-Content (Join-Path $GW 'LIVE_V490\config_v490_live.json') -Raw | ConvertFrom-Json
$v480=Get-Content (Join-Path $GW 'config_v480.json') -Raw | ConvertFrom-Json

Write-Host ''
Write-Host '=== IMC MASTER STATUS ===' -ForegroundColor Cyan
Write-Host ('MASTER live_broadcast       : '+$m.live_broadcast)
Write-Host ('V490 broadcast_enabled      : '+$v490.broadcast_enabled)
Write-Host ('V490 local_live_confirm     : '+$v490.require_local_live_confirm)
Write-Host ('V480 broadcast_default      : '+$v480.broadcast_default)
