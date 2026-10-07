$ErrorActionPreference='Stop'
$Root='%USERPROFILE%\Desktop\IMC_TRADING_MAIN'
$CfgPath=Join-Path $Root 'RUNTIME\EXECUTOR_GATEWAY\LIVE_V490\config_v490_live.json'
$ExecPath=Join-Path $Root 'RUNTIME\EXECUTOR_GATEWAY\v480_live_calibration_once.js'
$Cfg=Get-Content $CfgPath -Raw | ConvertFrom-Json
$Exec=Get-Content $ExecPath -Raw
function S([bool]$v){if($v){'AN'}else{'AUS'}}
Write-Host ''
Write-Host '=== IMC SPERREN STATUS ===' -ForegroundColor Cyan
Write-Host ('Broadcast              : '+(S([bool]$Cfg.broadcast_enabled)))
Write-Host ('Lokale Live-Bestaetigung: '+(S([bool]$Cfg.require_local_live_confirm)))
Write-Host ('Execute-Approved Gate  : '+(S($Exec.Contains('--execute-approved'))))
Write-Host ('Trade-ID Gate          : '+(S($Exec.Contains('--trade-id='))))
Write-Host ('Sendecode vorhanden    : '+(S($Exec.Contains('sendRawTransaction'))))
Write-Host ('Confirm-Code vorhanden : '+(S($Exec.Contains('confirmTransaction'))))
