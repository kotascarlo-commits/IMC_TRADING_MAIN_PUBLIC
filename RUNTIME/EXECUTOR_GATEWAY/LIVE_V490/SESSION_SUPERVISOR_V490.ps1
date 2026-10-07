$ErrorActionPreference='SilentlyContinue'
$Here=$PSScriptRoot
$Status=Join-Path $Here 'live_signal_gateway_v490_status.json'
while($true){
 Start-Sleep -Seconds 2
 if(-not (Test-Path $Status)){ continue }
 try{$s=Get-Content $Status -Raw | ConvertFrom-Json}catch{continue}
 if(-not $s.session_complete){ continue }
 $all=Get-CimInstance Win32_Process
 $targets=$all|Where-Object{
   $_.ProcessId -ne $PID -and $_.CommandLine -and (
    $_.CommandLine -like '*signal_gateway_v490.py*' -or
    $_.CommandLine -like '*--lane=v490-3trade*' -or
    $_.CommandLine -like '*watch_breadth_v425_shadow.py*' -or
    $_.CommandLine -like '*IMC_TRADING_MAIN*imc-main.db*run*'
   )
 }
 foreach($p in $targets){Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue}
 "AUTO_STOP $(Get-Date -Format o) completed=$($s.completed_roundtrips)" | Out-File (Join-Path $Here 'AUTO_STOP_V490.log') -Append -Encoding utf8
 break
}
