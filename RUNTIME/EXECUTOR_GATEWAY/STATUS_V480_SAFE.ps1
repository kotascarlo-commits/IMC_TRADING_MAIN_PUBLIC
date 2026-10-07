$d=Split-Path -Parent $MyInvocation.MyCommand.Path
$root=Split-Path -Parent $d
$p=Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine -like '*real_market_execution_v480_build_parity.py*' }
$s=Get-Content (Join-Path $root 'real_forward_v480_build_parity_status.json') -Raw | ConvertFrom-Json
[pscustomobject]@{
 Running=[bool]$p
 Mode=$s.mode
 Authority=$s.authority
 LiveExecution=$s.live_execution
 Open=$s.counts.OPEN
 Closed=$s.counts.CLOSED
} | Format-List
