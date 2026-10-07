$ErrorActionPreference='SilentlyContinue'
$Here=$PSScriptRoot
$Gateway=Join-Path $Here 'live_signal_gateway_v490_status.json'
$Twin=Join-Path $Here 'v490_twin_status.json'
$Cfg=Get-Content (Join-Path $Here 'config_v490_live.json') -Raw | ConvertFrom-Json
$g=if(Test-Path $Gateway){Get-Content $Gateway -Raw | ConvertFrom-Json}else{$null}
$t=if(Test-Path $Twin){Get-Content $Twin -Raw | ConvertFrom-Json}else{$null}
[pscustomobject]@{
 Mode=$Cfg.session_mode
 Broadcast=$Cfg.broadcast_enabled
 MaxTrades=$Cfg.max_session_trades
 Completed=if($g){$g.completed_roundtrips}else{0}
 OpenPositions=if($g){$g.open_live_positions}else{0}
 SessionActive=if($g){$g.session_active}else{$false}
 SessionComplete=if($g){$g.session_complete}else{$false}
 TwinStatus=if($t){$t.mode}else{'NOT_RUNNING_OR_NO_STATUS'}
}|Format-List
