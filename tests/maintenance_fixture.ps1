param([string]$Workspace,[string]$Python,[ValidateSet('start','hold','entry_start','entry_run')][string]$Case)
$ErrorActionPreference='Stop'
$Root=Split-Path -Parent $PSScriptRoot
if($Case -clike 'entry_*') {
    # Launcher bodies are unchanged copies. Their Python/process-discovery
    # dependencies are test doubles; an actual held RuntimeMutex prevents all
    # broker/worker startup even if the native maintenance guard admits RUN.
    $script=if($Case -ceq 'entry_start'){'START_IMC_SIGNAL_ONLY.ps1'}else{'RUN_IMC_SIGNAL_ONLY.ps1'}
    & (Join-Path $Workspace ('LAUNCHERS\'+$script))
    exit $LASTEXITCODE
}
. (Join-Path $Root 'TOOLS\IMC_MAINTENANCE.ps1')
# Only the fixture's state file is redirected; the real native global mutex
# and actual helper/validator remain in use. No production state is written.
$script:IMCMaintenanceRoot=$Workspace
try {
    if($Case -ceq 'start') {
        $lease=Enter-IMCMaintenanceFence -Python $Python -Purpose Start
        Exit-IMCMaintenanceFence $lease
        Write-Output 'fixture start allowed'
    } else {
        $lease=Enter-IMCMaintenanceFence -Python $Python -Purpose Maintenance
        Set-IMCMaintenanceActive $lease $Python ([guid]::NewGuid().ToString('N'))
        [IO.File]::WriteAllText((Join-Path $Workspace 'ready'),'ready')
        while(-not (Test-Path -LiteralPath (Join-Path $Workspace 'release'))){Start-Sleep -Milliseconds 10}
        Complete-IMCMaintenance $lease $Python $true $true $true
        Exit-IMCMaintenanceFence $lease
    }
} catch {
    $code=[string]$_.Exception.Message
    if($code -cnotmatch '\AIMC_MAINTENANCE_[A-Z_]+\z'){$code='FIXTURE_FAILURE'}
    Write-Output $code
    exit 2
}
