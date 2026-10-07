$Host.UI.RawUI.WindowTitle='IMC 3x 1-TASTE TEST'
Clear-Host
$Log='%USERPROFILE%\Desktop\IMC_TRADING_MAIN\RUNTIME\EXECUTOR_GATEWAY\demo_three_one_key_log.jsonl'
Remove-Item $Log -ErrorAction SilentlyContinue

Write-Host '=== IMC 3x 1-TASTE TEST ===' -ForegroundColor Cyan
Write-Host 'Nur Demo - keine echten Orders.' -ForegroundColor Green
Write-Host ''

$orders=@(
 [pscustomobject]@{Side='BUY';Trade=1;Mint='DEMO_TOKEN_A'},
 [pscustomobject]@{Side='SELL';Trade=1;Mint='DEMO_TOKEN_A'},
 [pscustomobject]@{Side='BUY';Trade=2;Mint='DEMO_TOKEN_B'}
)

foreach($o in $orders){
 Write-Host ('ORDER: '+$o.Side+' | Trade '+$o.Trade) -ForegroundColor Yellow
 Write-Host ('Mint: '+$o.Mint)
 Write-Host 'Broadcast: AUS'
 Write-Host 'Druecke 1 zum Bestaetigen - kein Enter noetig.'
 $key=$Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown')
 $approved=($key.Character -eq '1')
 if($approved){
   Write-Host 'DEMO: bestaetigt.' -ForegroundColor Green
 } else {
   Write-Host 'DEMO: abgelehnt.' -ForegroundColor DarkGray
 }
 [pscustomobject]@{
   time=(Get-Date).ToString('o')
   side=$o.Side
   trade=$o.Trade
   mint=$o.Mint
   key=[string]$key.Character
   approved=$approved
   broadcast=$false
 } | ConvertTo-Json -Compress | Add-Content $Log
 Write-Host ''
}
Write-Host '3 Tests beendet. Es wurde nichts gesendet.' -ForegroundColor Cyan
Write-Host ('Log: '+$Log)
Write-Host 'Beliebige Taste zum Schliessen.'
$null=$Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown')
