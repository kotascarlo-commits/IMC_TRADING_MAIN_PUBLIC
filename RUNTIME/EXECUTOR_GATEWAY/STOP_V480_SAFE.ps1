$ErrorActionPreference='SilentlyContinue'
Get-CimInstance Win32_Process | Where-Object {
 $_.CommandLine -and $_.CommandLine -like '*real_market_execution_v480_build_parity.py*'
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
'V480_SIM_STOPPED'
