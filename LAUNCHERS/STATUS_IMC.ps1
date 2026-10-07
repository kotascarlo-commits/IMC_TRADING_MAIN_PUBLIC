$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'IMC_PROCESS_COMMON.ps1')
$Base = Split-Path -Parent $PSScriptRoot
$expected = @('Core','CoreWrapper','Watcher','PureSignalGateway','Bridge')
try { $snapshot = Get-IMCProcessSnapshot }
catch { Write-Host ('PROCESS STATUS: UNKNOWN - '+$_.Exception.Message); exit 1 }
foreach ($role in @($expected + @($snapshot.Processes.Role)) | Select-Object -Unique) {
    $processes = @($snapshot.Processes | Where-Object { $_.Role -eq $role })
    $state = if ($processes.Count -gt 0) { 'RUNNING' }
             elseif ($snapshot.UnknownInterpreterPids.Count -gt 0) { 'UNKNOWN' }
             else { 'STOPPED' }
    '{0}: {1} PIDs={2}' -f $role,$state,($processes.ProcessId -join ',')
}
if ($snapshot.UnknownInterpreterPids.Count -gt 0) {
    'Unclassified interpreter PIDs (command line unavailable): '+($snapshot.UnknownInterpreterPids -join ',')
}
# File timestamps are historical evidence, never process-liveness proof.
foreach ($relative in @('RUNTIME\IMC_SYSTEM_HEALTH.json',
 'RUNTIME\EXECUTOR_GATEWAY\GMGN_BRIDGE\pure_signal_status.json',
 'RUNTIME\real_forward_v480_build_parity_status.json')) {
    $path = Join-Path $Base $relative
    if (Test-Path -LiteralPath $path) {
        'SAVED STATUS ONLY: {0} modified_utc={1}' -f $relative,(Get-Item -LiteralPath $path).LastWriteTimeUtc.ToString('o')
    }
}
