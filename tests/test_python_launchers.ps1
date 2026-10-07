$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $root 'LAUNCHERS\IMC_PYTHON_COMMON.ps1')
$expected = Join-Path $root '.venv\Scripts\python.exe'
if ((Get-IMCPython) -cne $expected) { throw 'Unexpected interpreter path' }
function Test-Path { return $false }
$blocked = $false
try { Get-IMCPython } catch { $blocked = $_.Exception.Message -like 'IMC .venv missing*' }
if (-not $blocked) { throw 'Missing environment was accepted' }
Remove-Item Function:\Test-Path
# Shadow only this executable invocation; do not change files or launch services.
Set-Item -LiteralPath ('Function:\' + $expected) -Value {
    if (($args -join ' ') -notmatch '^-I -c import sys,numpy,solders,mykonos_meme_scout;') {
        throw 'Dependency preflight omitted'
    }
    $global:LASTEXITCODE = 1
}
$blocked = $false
try { Get-IMCPython } catch { $blocked = $_.Exception.Message -like 'IMC requires Python 3.12*' }
if (-not $blocked) { throw 'Failed version/dependency preflight was accepted' }
Remove-Item -LiteralPath ('Function:\' + $expected)
$files = @('CORE\START_CORE_WORKER.ps1','LAUNCHERS\IMC_PYTHON_COMMON.ps1',
 'LAUNCHERS\START_IMC_SIGNAL_ONLY.ps1','LAUNCHERS\START_GMGN_SIGNAL_BRIDGE.ps1',
 'LAUNCHERS\START_IMC_AUTONOMOUS_PREP.ps1','LAUNCHERS\START_IMC_MASTER.ps1',
 'LAUNCHERS\MANUAL_ORDER_GATE.ps1','RUNTIME\WATCHDOG_V460_TRUTH_TWIN.ps1',
 'RESEARCH\WATCHDOG_RESEARCH_V482.ps1',
 'RUNTIME\EXECUTOR_GATEWAY\LIVE_V490\RUN_V490_TWIN.ps1',
 'RUNTIME\EXECUTOR_GATEWAY\START_V480_SAFE.ps1',
 'RUNTIME\EXECUTOR_GATEWAY\PREPARE_V480_CALIBRATION.ps1',
 'RUNTIME\EXECUTOR_GATEWAY\PREPARE_V480_LIVE.ps1')
foreach ($relative in $files) {
    $tokens = $null; $errors = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile(
        (Join-Path $root $relative), [ref]$tokens, [ref]$errors)
    if ($errors.Count -gt 0) { throw "Syntax errors in ${relative}: $errors" }
}
Write-Host 'Python preflight checks passed; all modified launchers parsed. No services started or stopped.'
