$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'IMC_PYTHON_COMMON.ps1')
$Python = Get-IMCPython
$Root='%USERPROFILE%\Desktop\IMC_TRADING_MAIN'
$GW=Join-Path $Root 'RUNTIME\EXECUTOR_GATEWAY'
$Live=Join-Path $GW 'LIVE_V490'
$EventPath=Join-Path $GW 'v490_manual_event.json'
$ApprovalPath=Join-Path $GW 'manual_order_approval.json'
$Cfg=Get-Content (Join-Path $Live 'config_v490_live.json') -Raw | ConvertFrom-Json
$Gateway=$Cfg.gateway_url

Write-Host ''
Write-Host '=== IMC MANUAL ORDER APPROVAL ===' -ForegroundColor Cyan
Write-Host 'BUY und SELL werden nur angezeigt. Broadcast bleibt AUS.'
Write-Host ''

while($true){
 try {$next=Invoke-RestMethod -Uri ($Gateway+'/next') -TimeoutSec 3}
 catch {Start-Sleep -Seconds 1; continue}
 $ev=$next.signal
 if($null -eq $ev){Start-Sleep -Milliseconds 750; continue}

 $ev | ConvertTo-Json -Depth 20 | Set-Content $EventPath -Encoding UTF8
 Write-Host ''
 Write-Host ('ORDER: '+$ev.side+' | Trade '+$ev.trade_id) -ForegroundColor Yellow
 Write-Host ('Mint : '+$ev.mint)
 if($ev.side -eq 'BUY'){Write-Host ('Input lamports: '+$ev.input_lamports)}
 if($ev.exit_reason){Write-Host ('Exit reason: '+$ev.exit_reason)}
 Write-Host 'Broadcast: AUS'
 Write-Host '1 = Order manuell FREIGEBEN | jede andere Taste = nicht freigeben'
 $key=$Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown')
 $answer=$key.Character
 if($answer -eq '1'){
  [pscustomobject]@{
   approved=$true
   event_id=$ev.event_id
   side=$ev.side
   trade_id=$ev.trade_id
   mint=$ev.mint
   approved_at=(Get-Date).ToString('o')
   broadcast=$false
  } | ConvertTo-Json | Set-Content $ApprovalPath -Encoding UTF8
  Write-Host 'Freigabe gespeichert. Bereite Order vor...' -ForegroundColor Green
  $prep=& $Python (Join-Path $GW 'prepare_v490_manual_event.py') 2>&1
  if($LASTEXITCODE -ne 0){
   Write-Host ('Vorbereitung fehlgeschlagen: '+($prep -join ' ')) -ForegroundColor Red
  } else {
   $sim=& node (Join-Path $GW '_v480_sign_sim_no_broadcast.js') 2>&1
   if($LASTEXITCODE -eq 0){
    Write-Host ($sim -join [Environment]::NewLine) -ForegroundColor Green
    Write-Host 'STATUS: READY_TO_SEND - Broadcast weiterhin AUS.' -ForegroundColor Yellow
   } else {
    Write-Host ($sim -join [Environment]::NewLine) -ForegroundColor Red
    Write-Host 'STATUS: BLOCKED - signed simulation fehlgeschlagen.' -ForegroundColor Red
   }
  }
 } else {
  Write-Host 'Keine Freigabe.' -ForegroundColor DarkGray
 }
 Start-Sleep -Seconds 1
}
