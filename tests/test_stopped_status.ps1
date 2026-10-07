$ErrorActionPreference='Stop'
$repo=Split-Path -Parent $PSScriptRoot
. (Join-Path $repo 'LAUNCHERS\IMC_PROCESS_COMMON.ps1')
$fixture=Join-Path $repo ('.local-tools\stop-status-fixture-'+[guid]::NewGuid().ToString('N'))
$null=[IO.Directory]::CreateDirectory((Join-Path $fixture 'RUNTIME'))
$null=[IO.Directory]::CreateDirectory((Join-Path $fixture 'config'))
$script:IMCProcessRoot=$fixture
[IO.File]::WriteAllText((Join-Path $fixture 'config\imc-execution.json'),'{"port":49090}')
$signal=Join-Path $fixture 'RUNTIME\signal-runtime-status.json'
$health=Join-Path $fixture 'RUNTIME\IMC_SYSTEM_HEALTH.json'
$oldSignal='{"state":"RUNNING","pid":123}'
$oldHealth='{"verdict":"HEALTHY"}'
[IO.File]::WriteAllText($signal,$oldSignal)
[IO.File]::WriteAllText($health,$oldHealth)
$script:fixtureSnapshot=[pscustomobject]@{Processes=@([pscustomobject]@{Role='Core'});UnknownInterpreterPids=@()}
function Get-IMCProcessSnapshot { return $script:fixtureSnapshot }
$script:listeners=@()
$script:networkError=$false
function Get-NetTCPConnection { if($script:networkError){throw 'NETWORK_VISIBILITY_UNKNOWN'}; return $script:listeners }
function Expect-Blocked([string]$Code) {
    $blocked=$false
    try { Publish-IMCStoppedStatus } catch { $blocked=$_.Exception.Message -ceq $Code }
    if (-not $blocked -or [IO.File]::ReadAllText($signal) -cne $oldSignal) { throw 'False stopped status published' }
}
Expect-Blocked 'STOP_STATUS_NATIVE_ABSENCE_UNVERIFIED'
$script:fixtureSnapshot=[pscustomobject]@{Processes=@();UnknownInterpreterPids=@(999)}
Expect-Blocked 'STOP_STATUS_NATIVE_ABSENCE_UNVERIFIED'
$script:fixtureSnapshot=[pscustomobject]@{Processes=@();UnknownInterpreterPids=@()}
$script:listeners=@([pscustomobject]@{LocalPort=49090})
Expect-Blocked 'STOP_STATUS_GATEWAY_STILL_LISTENING'
$script:listeners=@()
$script:networkError=$true
Expect-Blocked 'NETWORK_VISIBILITY_UNKNOWN'
$script:networkError=$false
Publish-IMCStoppedStatus
$saved=Get-Content -LiteralPath $signal -Raw|ConvertFrom-Json
$savedHealth=Get-Content -LiteralPath $health -Raw|ConvertFrom-Json
if ($saved.state -cne 'STOPPED' -or $saved.chain_state -cne 'NOT_VERIFIED' -or
    $saved.broadcast -ne $false -or $saved.live_ready -ne $false -or $savedHealth.runtime_state -cne 'STOPPED' -or
    $savedHealth.verdict -ceq 'HEALTHY') { throw 'Stopped evidence invalid' }
$directories=@(Get-ChildItem -LiteralPath (Join-Path $fixture '.local-tools\status-history') -Directory)
if ($directories.Count -ne 1 -or [IO.File]::ReadAllText((Join-Path $directories[0].FullName 'signal-runtime-status.json')) -cne $oldSignal -or
    [IO.File]::ReadAllText((Join-Path $directories[0].FullName 'IMC_SYSTEM_HEALTH.json')) -cne $oldHealth) { throw 'Historical bytes lost' }
Write-Output 'STOP_STATUS 5 PASS; no real processes or config changes.'
