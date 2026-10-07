$ErrorActionPreference='Stop'

$Root='%USERPROFILE%\Desktop\IMC_TRADING_MAIN'
$CfgPath=Join-Path $Root 'RUNTIME\EXECUTOR_GATEWAY\LIVE_V490\config_v490_live.json'
$ExecPath=Join-Path $Root 'RUNTIME\EXECUTOR_GATEWAY\v480_live_calibration_once.js'
$SafeStart=Join-Path $Root 'LAUNCHERS\START_IMC_TRUTH_STACK.ps1'

if(-not (Test-Path $CfgPath)){throw "Config fehlt: $CfgPath"}
if(-not (Test-Path $ExecPath)){throw "Executor fehlt: $ExecPath"}
if(-not (Test-Path $SafeStart)){throw "Safe-Start fehlt: $SafeStart"}

$cfg=Get-Content $CfgPath -Raw | ConvertFrom-Json
$exec=Get-Content $ExecPath -Raw

$hasApprovalFlag=$exec.Contains("--execute-approved")
$hasTradeIdGate=$exec.Contains("--trade-id=")
$hasSendRaw=$exec.Contains("sendRawTransaction")
$hasConfirm=$exec.Contains("confirmTransaction")

Write-Host ""
Write-Host "=== IMC LIVE-SPERREN ===" -ForegroundColor Cyan
[pscustomobject]@{
  BroadcastEnabled=$cfg.broadcast_enabled
  LocalLiveConfirm=$cfg.require_local_live_confirm
  ExecuteApprovedGate=$hasApprovalFlag
  TradeIdGate=$hasTradeIdGate
  BlockchainSendCodePresent=$hasSendRaw
  ConfirmationCodePresent=$hasConfirm
} | Format-List

Write-Host "Config:   $CfgPath"
Write-Host "Executor: $ExecPath"
Write-Host ""

if($cfg.broadcast_enabled -ne $false){
  Write-Host "START BLOCKIERT: Broadcast ist nicht AUS." -ForegroundColor Red
  exit 10
}
if($cfg.require_local_live_confirm -ne $true){
  Write-Host "START BLOCKIERT: lokale Live-Bestaetigung ist nicht aktiv." -ForegroundColor Red
  exit 11
}
if(-not $hasApprovalFlag -or -not $hasTradeIdGate){
  Write-Host "START BLOCKIERT: Trade-Freigabe-Gate fehlt." -ForegroundColor Red
  exit 12
}

Write-Host "SAFE STATUS: alle sichtbaren Echtgeld-Sperren sind aktiv." -ForegroundColor Green
Write-Host "Starte IMC im No-Broadcast-Modus..." -ForegroundColor Green
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $SafeStart
exit $LASTEXITCODE
