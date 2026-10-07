$ErrorActionPreference='Stop'
$Log='%USERPROFILE%\Desktop\IMC_TRADING_MAIN\RUNTIME\EXECUTOR_GATEWAY\demo_three_one_key_log.jsonl'
Remove-Item $Log -ErrorAction SilentlyContinue
$orders=@(
 [pscustomobject]@{Side='BUY';Trade=1;Mint='DEMO_TOKEN_A'},
 [pscustomobject]@{Side='SELL';Trade=1;Mint='DEMO_TOKEN_A'},
 [pscustomobject]@{Side='BUY';Trade=2;Mint='DEMO_TOKEN_B'}
)
foreach($o in $orders){
 $approved=$true
 [pscustomobject]@{
  time=(Get-Date).ToString('o')
  side=$o.Side
  trade=$o.Trade
  mint=$o.Mint
  key='1'
  approved=$approved
  broadcast=$false
 } | ConvertTo-Json -Compress | Add-Content $Log
 Write-Host ('SIMULATED 1 -> '+$o.Side+' Trade '+$o.Trade+' approved='+$approved+' broadcast=false')
}
