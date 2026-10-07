$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'IMC_PYTHON_COMMON.ps1')
$Python = Get-IMCPython
$Base=Split-Path -Parent $PSScriptRoot
$Runtime=Join-Path $Base 'RUNTIME'
$GW=Join-Path $Runtime 'EXECUTOR_GATEWAY'
$Master=Get-Content (Join-Path $GW 'IMC_MASTER_MODE.json') -Raw | ConvertFrom-Json
$LiveRequested=[bool]$Master.live_broadcast
$Live=Join-Path $GW 'LIVE_V490'
$Core=Join-Path $Base 'CORE\START_CORE_WORKER.ps1'
$Watch=Join-Path $Runtime 'watch_breadth_v425_shadow.py'
$TwinRun=Join-Path $Live 'RUN_V490_TWIN.ps1'
$SignalGW=Join-Path $Live 'signal_gateway_v490.py'
$Supervisor=Join-Path $Live 'SESSION_SUPERVISOR_V490.ps1'
$State=Join-Path $Live 'live_signal_state_v490.json'
$CfgPath=Join-Path $Live 'config_v490_live.json'
$Cfg=Get-Content $CfgPath -Raw | ConvertFrom-Json
$Cfg.broadcast_enabled=$false
$Cfg.require_local_live_confirm=$true
$Cfg | ConvertTo-Json -Depth 20 | Set-Content $CfgPath -Encoding UTF8
$V480Path=Join-Path $GW 'config_v480.json'
$V480=Get-Content $V480Path -Raw | ConvertFrom-Json
$V480.broadcast_default=$false
$V480 | ConvertTo-Json -Depth 20 | Set-Content $V480Path -Encoding UTF8

& node (Join-Path $GW 'wallet_verify.js') | Out-Null
if($LASTEXITCODE -ne 0){throw 'Wallet verification failed.'}
$w=Get-Content (Join-Path $GW 'wallet_public.json') -Raw | ConvertFrom-Json
$u='https://api.jup.ag/swap/v2/order?inputMint=So11111111111111111111111111111111111111112&outputMint=EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v&amount=100000&taker='+$w.address
$j=Invoke-RestMethod -Uri $u -Method Get -TimeoutSec 8
if(-not $j.transaction -or -not $j.requestId){throw 'Jupiter v2 order preflight failed.'}

$fresh=$true
if(Test-Path $State){
 try{
  $s=Get-Content $State -Raw | ConvertFrom-Json
  $bc=@($s.buy_filled.PSObject.Properties.Name).Count
  $sc=@($s.sell_filled.PSObject.Properties.Name).Count
  if($s.session_active -or $bc -gt $sc -or $null -ne $s.pending){$fresh=$false}
 }catch{$fresh=$false}
}
if($fresh){
 $stamp=Get-Date -Format 'yyyyMMdd-HHmmss'
 $archive=Join-Path $Live ('archive\'+$stamp)
 New-Item -ItemType Directory -Force $archive|Out-Null
 $old=@(
  $State,
  (Join-Path $Live 'live_signal_gateway_v490_status.json'),
  (Join-Path $Live 'live_execution_journal_v490.jsonl'),
  (Join-Path $Live 'v490_twin_status.json'),
  (Join-Path $Live 'v490_twin_state.json'),
  (Join-Path $Runtime 'real-forward-v490-live-twin.db')
 )
 foreach($f in $old){if(Test-Path $f){Move-Item $f $archive -Force}}
}

$all=Get-CimInstance Win32_Process
if(-not ($all|Where-Object{$_.CommandLine -and $_.CommandLine -like '*IMC_TRADING_MAIN*imc-main.db*run*'})){
 Start-Process powershell.exe -WindowStyle Hidden -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File',$Core
}
if(-not ($all|Where-Object{$_.CommandLine -and $_.CommandLine -like '*watch_breadth_v425_shadow.py*'})){
 Start-Process $Python -WindowStyle Hidden -ArgumentList '-u',('"'+$Watch+'"')
}
if(-not ($all|Where-Object{$_.CommandLine -and $_.CommandLine -like '*--lane=v490-3trade*'})){
 Start-Process powershell.exe -WindowStyle Hidden -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File',$TwinRun
}
if(-not ($all|Where-Object{$_.CommandLine -and $_.CommandLine -like '*signal_gateway_v490.py*'})){
 Start-Process $Python -WindowStyle Hidden -ArgumentList '-u',('"'+$SignalGW+'"')
}
if(-not ($all|Where-Object{$_.CommandLine -and $_.CommandLine -like '*SESSION_SUPERVISOR_V490.ps1*'})){
 Start-Process powershell.exe -WindowStyle Hidden -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File',$Supervisor
}
Start-Sleep -Seconds 2
$h=Invoke-RestMethod -Uri 'http://127.0.0.1:49090/health' -TimeoutSec 4
[pscustomobject]@{
 Mode=$Cfg.session_mode
 MaxTrades=$Cfg.max_session_trades
 MaxOpen=$Cfg.max_open_positions
 PositionFraction=$Cfg.max_position_fraction
 Broadcast=$Cfg.broadcast_enabled
 Gateway=$h.session_active
 Completed=$h.completed_roundtrips
}|Format-List
