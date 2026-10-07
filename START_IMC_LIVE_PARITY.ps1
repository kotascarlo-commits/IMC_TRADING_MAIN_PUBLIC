param(
    [ValidateRange(0.01,44640)][double]$DurationMinutes=60,
    [ValidateRange(0.01,1000000000)][double]$VirtualCapitalEUR=1000,
    [ValidateRange(3,1000000)][int]$TargetClosedTrades=3,
    [switch]$OfflineContractTest
)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'LAUNCHERS\IMC_PYTHON_COMMON.ps1')
$python=Get-IMCPythonPath
& $python -I -S -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3,12) else 1)'
if ($LASTEXITCODE -ne 0) { throw 'IMC live-parity preflight requires Python 3.12.' }
$culture=[Globalization.CultureInfo]::InvariantCulture
if ($OfflineContractTest) {
    # Explicit isolated contract/VM fixtures; never silently substitute for trading.
    & $python -I (Join-Path $PSScriptRoot 'CORE\mykonos_meme_scout\live_parity_contract_test.py') `
        --duration-minutes $DurationMinutes.ToString($culture) `
        --virtual-capital-eur $VirtualCapitalEUR.ToString($culture) `
        --target-closed-trades $TargetClosedTrades
    exit $LASTEXITCODE
}
# Current acceptance is BLOCKED. Admission writes a report and exits before any
# network, wallet verifier, runtime, signing or broadcast operation.
& $python -I -S (Join-Path $PSScriptRoot 'CORE\mykonos_meme_scout\live_parity_preflight.py') `
    --duration-minutes $DurationMinutes.ToString($culture) `
    --virtual-capital-eur $VirtualCapitalEUR.ToString($culture) `
    --target-closed-trades $TargetClosedTrades
exit $LASTEXITCODE
