$ErrorActionPreference='SilentlyContinue'
$u='http://127.0.0.1:49090/next'
Write-Host '=== IMC SIGNAL MONITOR ===' -ForegroundColor Cyan
Write-Host 'Nur Signale - keine Ausfuehrung.' -ForegroundColor Green
$last=''
while($true){
 try{$r=Invoke-RestMethod -Uri $u -TimeoutSec 3}catch{Start-Sleep 1;continue}
 $s=$r.signal
 if($null -ne $s -and $s.event_id -ne $last){
  $last=$s.event_id
  Write-Host ''
  Write-Host ('SIGNAL: '+$s.side+' | Trade '+$s.trade_id) -ForegroundColor Yellow
  Write-Host ('Mint: '+$s.mint)
  if($s.input_lamports){Write-Host ('Input lamports: '+$s.input_lamports)}
  if($s.exit_reason){Write-Host ('Exit reason: '+$s.exit_reason)}
  Write-Host ('Zeit: '+$s.created_at)
 }
 Start-Sleep -Milliseconds 750
}
