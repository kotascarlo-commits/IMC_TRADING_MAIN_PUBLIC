$ErrorActionPreference='SilentlyContinue'
$all=Get-CimInstance Win32_Process
$targets=$all|Where-Object{
 $_.CommandLine -and (
  $_.CommandLine -like '*signal_gateway_v490.py*' -or
  $_.CommandLine -like '*--lane=v490-3trade*' -or
  $_.CommandLine -like '*SESSION_SUPERVISOR_V490.ps1*' -or
  $_.CommandLine -like '*watch_breadth_v425_shadow.py*' -or
  $_.CommandLine -like '*IMC_TRADING_MAIN*imc-main.db*run*'
 )
}
foreach($p in $targets){
 Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}
"IMC 3-trade session stopped. Processes=$(@($targets).Count)"
