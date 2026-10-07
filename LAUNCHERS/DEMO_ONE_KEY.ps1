$Host.UI.RawUI.WindowTitle='IMC 1-TASTE TEST'
Clear-Host
Write-Host '=== IMC 1-TASTE TEST ===' -ForegroundColor Cyan
Write-Host 'Nur Demo - keine echte Order.' -ForegroundColor Green
Write-Host ''
Write-Host 'ORDER: BUY | Trade 12' -ForegroundColor Yellow
Write-Host 'Mint: DEMO_TOKEN'
Write-Host 'Broadcast: AUS'
Write-Host ''
Write-Host 'Druecke 1 zum Bestaetigen - kein Enter noetig.'
$key=$Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown')
if($key.Character -eq '1'){
 Write-Host ''
 Write-Host 'DEMO: 1 erkannt - bestaetigt.' -ForegroundColor Green
}else{
 Write-Host ''
 Write-Host 'DEMO: andere Taste - abgelehnt.' -ForegroundColor DarkGray
}
Write-Host ''
Write-Host 'Beliebige Taste zum Schliessen.'
$null=$Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown')
