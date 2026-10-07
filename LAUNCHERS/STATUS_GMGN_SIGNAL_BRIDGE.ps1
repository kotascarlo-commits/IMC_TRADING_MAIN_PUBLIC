$Root='%USERPROFILE%\Desktop\IMC_TRADING_MAIN'
$Dir=Join-Path $Root 'RUNTIME\EXECUTOR_GATEWAY\GMGN_BRIDGE'
$P=Get-CimInstance Win32_Process|Where-Object{$_.CommandLine -and $_.CommandLine -like '*gmgn_signal_bridge.py*'}
Write-Host ('Bridge: '+$(if($P){'RUNNING'}else{'STOPPED'}))
Write-Host 'Execution: OFF'
$Latest=Join-Path $Dir 'outbox\latest_signal.json'
if(Test-Path $Latest){Get-Content $Latest -Raw}
