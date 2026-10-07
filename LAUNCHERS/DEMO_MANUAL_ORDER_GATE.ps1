$Host.UI.RawUI.WindowTitle='IMC MANUAL ORDER GATE - DEMO'
Clear-Host
Write-Host '=== IMC MANUAL ORDER GATE - DEMO ===' -ForegroundColor Cyan
Write-Host 'Nur Testdaten. Kein Wallet-Zugriff. Kein Broadcast.' -ForegroundColor Green
Write-Host ''

$orders=@(
 [pscustomobject]@{Side='BUY';Trade=12;Mint='DEMO7xKfExampleMoon';Info='Input lamports: 1655706'},
 [pscustomobject]@{Side='SELL';Trade=12;Mint='DEMO7xKfExampleMoon';Info='Exit reason: TRAILING_EXEC_PROFIT'}
)

foreach($o in $orders){
 Write-Host ('ORDER: '+$o.Side+' | Trade '+$o.Trade) -ForegroundColor Yellow
 Write-Host ('Mint : '+$o.Mint)
 Write-Host $o.Info
 Write-Host 'Broadcast: AUS'
 Write-Host ''
 $answer=Read-Host '1 = Order FREIGEBEN | Enter = NICHT freigeben'
 if($answer -eq '1'){
   Write-Host 'DEMO: Freigabe erkannt. Es wurde NICHTS gesendet.' -ForegroundColor Green
 } else {
   Write-Host 'DEMO: Keine Freigabe.' -ForegroundColor DarkGray
 }
 Write-Host ''
}
Write-Host 'DEMO beendet. Kein Broadcast wurde ausgefuehrt.' -ForegroundColor Cyan
Write-Host 'Fenster kann geschlossen werden.'
