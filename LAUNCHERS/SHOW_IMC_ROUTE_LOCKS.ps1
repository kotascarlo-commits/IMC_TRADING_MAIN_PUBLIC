$ErrorActionPreference='Stop'
$Root='%USERPROFILE%\Desktop\IMC_TRADING_MAIN'
$CfgPath=Join-Path $Root 'RUNTIME\EXECUTOR_GATEWAY\LIVE_V490\config_v490_live.json'
$ExecPath=Join-Path $Root 'RUNTIME\EXECUTOR_GATEWAY\v480_live_calibration_once.js'
$Cfg=Get-Content $CfgPath -Raw | ConvertFrom-Json
$Exec=Get-Content $ExecPath -Raw

$approval=$Exec.Contains('--execute-approved')
$tradeId=$Exec.Contains('--trade-id=')
$sendRaw=$Exec.Contains('sendRawTransaction')
$confirm=$Exec.Contains('confirmTransaction')

Write-Host ''
Write-Host '=== IMC ORDER-ROUTE + SPERREN ===' -ForegroundColor Cyan
Write-Host 'IMC Signal'
Write-Host '  -> SAFE100'
Write-Host '  -> Jupiter Quote/Build'
Write-Host '  -> Transaktion bauen'
Write-Host '  -> Signieren'
Write-Host '  -> Live-Freigaben'
Write-Host '  -> Blockchain Broadcast'
Write-Host '  -> Solana'
Write-Host '  -> Fill/Confirmation'
Write-Host ''
[pscustomobject]@{
  BroadcastEnabled=$Cfg.broadcast_enabled
  RequireLocalLiveConfirm=$Cfg.require_local_live_confirm
  ExecuteApprovedGate=$approval
  TradeIdGate=$tradeId
  SendRawTransactionPresent=$sendRaw
  ConfirmTransactionPresent=$confirm
  SessionMode=$Cfg.session_mode
  MaxSessionTrades=$Cfg.max_session_trades
  MaxOpenPositions=$Cfg.max_open_positions
} | Format-List
Write-Host ('Config:   '+$CfgPath)
Write-Host ('Executor: '+$ExecPath)
