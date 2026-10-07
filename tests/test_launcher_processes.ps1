$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $root 'LAUNCHERS\IMC_PROCESS_COMMON.ps1')
$script:checks = 0
$self=Get-CimInstance Win32_Process -Filter ('ProcessId='+$PID)
$all=@($self)+@(Get-CimInstance Win32_Process -Filter ('ProcessId='+$self.ParentProcessId))
$known=Join-Path $root 'TOOLS\IMC_Terminal.ps1'
$evidence=Get-IMCUnknownEvidence $self $all 'UNRECOGNIZED_REPOSITORY_ARGUMENT' @($known,'API_SECRET_FIXTURE_DO_NOT_LOG','C:\Other\secret.py') $false $true
$json=$evidence|ConvertTo-Json -Depth 5
if($json.Contains('API_SECRET_FIXTURE_DO_NOT_LOG') -or $json.Contains('C:\\Other\\secret.py') -or
    $evidence.registered_script_arguments.Count -ne 1 -or $evidence.registered_script_arguments[0] -cne $known -or
    -not $evidence.native_bound -or $evidence.native_exited -or -not $evidence.parent.creation_filetime){throw 'Unknown-process evidence binding/redaction failed'}
$script:checks++
function Assert-Role([string]$Name, [string]$Line, [string]$Expected) {
    $role = Get-IMCProcessRole ([pscustomobject]@{Name=$Name; CommandLine=$Line})
    if ([string]$role.Role -cne $Expected) { throw "Unexpected role: $Line -> $($role.Role), expected $Expected" }
    $script:checks++
}
foreach ($definition in $script:IMCScripts) {
    $path = Join-Path $root $definition.Path
    switch ($definition.Kind) {
        'shell' { Assert-Role 'powershell.exe' ('powershell.exe -NoProfile -ExecutionPolicy Bypass -File "'+$path+'"') $definition.Role }
        'python' { Assert-Role 'python.exe' ('python.exe -u "'+$path+'"') $definition.Role }
        'node' { Assert-Role 'node.exe' ('node.exe "'+$path+'"') $definition.Role }
    }
}
$watch = Join-Path $root 'RUNTIME\watch_breadth_v425_shadow.py'
$wrapper = Join-Path $root 'CORE\START_CORE_WORKER.ps1'
$db = Join-Path $root 'CORE\data\imc-main.db'
Assert-Role 'py.exe' ('py.exe -3.12 -u "'+$watch+'"') 'Watcher'
Assert-Role 'python.exe' ('python.exe -u -m mykonos_meme_scout --database "'+$db+'" run --interval 0.5') 'Core'
Assert-Role 'python.exe' ('python.exe -m mykonos_meme_scout --database "'+$db+'" dashboard') ''
Assert-Role 'python.exe' ('python.exe -m mykonos_meme_scout --database C:\Other\imc-main.db run') ''
Assert-Role 'python.exe' ('python.exe -c "print(1)" "'+$watch+'"') ''
Assert-Role 'python.exe' ('python.exe C:\Other\reader.py "'+$watch+'"') ''
Assert-Role 'python.exe' 'python.exe "C:\Other Project\watch_breadth_v425_shadow.py"' ''
Assert-Role 'python.exe' 'python.exe -u watch_breadth_v425_shadow.py' ''
Assert-Role 'powershell.exe' ('powershell.exe -Command "Get-Content '+$wrapper+'"') ''
Assert-Role 'powershell.exe' ('powershell.exe -EncodedCommand abc "'+$wrapper+'"') ''
Assert-Role 'powershell.exe' ('powershell.exe -File C:\Other\read.ps1 "'+$wrapper+'"') ''
Assert-Role 'node.exe' ('node.exe C:\tools\codex.js "Task: read '+$watch+'"') ''
Assert-Role 'node.exe' ('node.exe -e "readFileSync(process.argv[1])" "'+$watch+'"') ''
Assert-Role 'node.exe' ('python.exe "'+$watch+'"') ''
$a = [pscustomobject]@{ProcessId=123; CreationDate=[datetime]'2026-10-02T09:00:00Z'; Role='Watcher'}
$b = [pscustomobject]@{ProcessId=123; CreationDate=[datetime]'2026-10-02T09:00:01Z'; Role='Watcher'}
if (-not (Test-IMCSameProcess $a $a)) { throw 'Same process identity rejected' }
if (Test-IMCSameProcess $a $b) { throw 'Reused PID accepted' }
$b.CreationDate=$a.CreationDate; $b.Role='Bridge'
if (Test-IMCSameProcess $a $b) { throw 'Changed role accepted' }
$script:checks += 3
# Mock every operation that could reach a real process for stop-path tests.
function Get-Process { throw 'Live process access forbidden in fixtures' }
function Stop-Process { throw 'Live process stop forbidden in fixtures' }
$b.CreationDate=[datetime]'2026-10-02T09:00:01Z'; $b.Role='Watcher'
$script:fixtureSnapshot=[pscustomobject]@{Processes=@($b); UnknownInterpreterPids=@()}
function Get-IMCProcessSnapshot { return $script:fixtureSnapshot }
$blocked=$false
try { Stop-IMCIdentifiedProcess $a }
catch { if ($_.Exception.Message -like 'Process identity changed*') { $blocked=$true } else { throw } }
if (-not $blocked) { throw 'Stop of reused PID was not blocked' }
$script:checks++

$script:stopCalls=@(); $script:snapshotIndex=0
$wrapperFixture=[pscustomobject]@{Role='CoreWrapper'; Supervisor=$true}
$childFixture=[pscustomobject]@{Role='Core'; Supervisor=$false}
$script:fixtureSnapshots=@(
 [pscustomobject]@{Processes=@($childFixture,$wrapperFixture); UnknownInterpreterPids=@()},
 [pscustomobject]@{Processes=@($childFixture); UnknownInterpreterPids=@()},
 [pscustomobject]@{Processes=@(); UnknownInterpreterPids=@()}
)
function Get-IMCProcessSnapshot { $result=$script:fixtureSnapshots[$script:snapshotIndex]; $script:snapshotIndex++; return $result }
function Stop-IMCIdentifiedProcess($Expected) { $script:stopCalls += $Expected.Role }
Stop-IMCRoles
if (($script:stopCalls -join ',') -ne 'CoreWrapper,Core') { throw 'Supervisor/child stop order is wrong' }
if ($script:snapshotIndex -ne 3) { throw 'Children/rest processes were not re-enumerated' }
$script:checks++

$script:fixtureSnapshot=[pscustomobject]@{Processes=@($childFixture); UnknownInterpreterPids=@(999)}
function Get-IMCProcessSnapshot { return $script:fixtureSnapshot }
$script:stopCalls=@(); $blocked=$false
try { Stop-IMCRoles }
catch { if ($_.Exception.Message -like 'Process command lines unavailable*') { $blocked=$true } else { throw } }
if (-not $blocked -or $script:stopCalls.Count -ne 0) { throw 'Unknown process metadata did not block stop' }
$script:checks++

foreach ($relative in @('LAUNCHERS\IMC_PROCESS_COMMON.ps1','LAUNCHERS\START_IMC.ps1',
 'LAUNCHERS\START_IMC_SIGNAL_ONLY.ps1','LAUNCHERS\STOP_IMC.ps1',
 'LAUNCHERS\STOP_IMC_SIGNAL_ONLY.ps1','LAUNCHERS\STATUS_IMC.ps1')) {
    $tokens=$null; $errors=$null
    $null=[System.Management.Automation.Language.Parser]::ParseFile((Join-Path $root $relative),[ref]$tokens,[ref]$errors)
    if ($errors.Count -gt 0) { throw "Syntax errors in ${relative}: $errors" }
}
Write-Host "$script:checks process identity checks passed; six launcher files parsed. No services started or stopped."

# Reload the real snapshot implementation; only CIM rows are synthetic.
. (Join-Path $root 'LAUNCHERS\IMC_PROCESS_COMMON.ps1')
function Get-CimInstance {
    if ($global:IMCTestAlreadyRunning) {
        $supervisorRows=@($global:IMCTestEntryRows | Where-Object { (Get-IMCProcessRole $_).Role -ceq 'SignalSupervisor' })
        if ($supervisorRows.Count -ne 1) { throw 'Synthetic supervisor missing; launcher invocation blocked' }
    }
    return $global:IMCTestEntryRows
}
function Start-Process { throw 'Fixture must never start a service' }
function Stop-Process { throw 'Fixture must never stop a service' }
$entries=@($script:IMCScripts | Where-Object NoScriptArguments)
$canonical=@($entries | Where-Object {$_.Role -match 'Entrypoint$'})
if ($canonical.Count -ne 5 -or $entries.Count -ne 7) { throw 'Canonical/control acceptance entrypoint set changed' }
foreach ($entry in $entries) {
    $path=Join-Path $root $entry.Path
    $line='powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+$path+'"'
    Assert-Role 'powershell.exe' $line $entry.Role
    foreach ($suffix in @(' --extra',' ""',' -Command noop')) {
        Assert-Role 'powershell.exe' ($line+$suffix) ''
    }
    foreach ($badPath in @($path.Substring(0,2)+$path.Substring(3), $path.Substring(2),
        (Join-Path $root ('LAUNCHERS\..\'+$entry.Path)), $entry.Path,
        ('C:\Other\'+[IO.Path]::GetFileName($path)))) {
        Assert-Role 'powershell.exe' ('powershell.exe -File "'+$badPath+'"') ''
    }
    Assert-Role 'powershell.exe' ('powershell.exe -Command "& '+$path+'"') ''
    Assert-Role 'powershell.exe' ('powershell.exe -EncodedCommand abc "'+$path+'"') ''
    $global:IMCTestEntryRows=@([pscustomobject]@{Name='powershell.exe'; ProcessId=900; CommandLine=$line})
    $snapshot=Get-IMCProcessSnapshot
    if ($snapshot.Processes.Count -or $snapshot.UnknownInterpreterPids.Count) {
        throw 'Canonical entrypoint contaminated runtime snapshot'
    }
    $global:IMCTestEntryRows[0].CommandLine=$line+' ""'
    $snapshot=Get-IMCProcessSnapshot
    if ($snapshot.Processes.Count -or ($snapshot.UnknownInterpreterPids -join ',') -cne '900') {
        throw 'Repository entrypoint with extra arguments was silently excluded'
    }
}

# Validate before invoking either real START file: the direct native interpreter
# preflight is reachable only after the no-supervisor/conflict checks. Function
# mocks alone cannot intercept a regression to an unconditional native call.
$starter=Join-Path $root 'LAUNCHERS\START_IMC_SIGNAL_ONLY.ps1'
$ast=[System.Management.Automation.Language.Parser]::ParseFile($starter,[ref]$null,[ref]$null)
$pythonCalls=@($ast.FindAll({param($node)
    $node -is [System.Management.Automation.Language.CommandAst] -and $node.GetCommandName() -eq 'Get-IMCPython'
},$true))
if ($pythonCalls.Count -ne 1) { throw 'Interpreter preflight call count changed' }
$ancestor=$pythonCalls[0].Parent
while ($ancestor -and $ancestor -isnot [System.Management.Automation.Language.IfStatementAst]) { $ancestor=$ancestor.Parent }
if (-not $ancestor -or $ancestor.Clauses.Count -ne 1 -or
    $ancestor.Clauses[0].Item1.Extent.Text -cne '-not $supervisors.Count' -or
    $ancestor.Extent.Text.IndexOf('if ($snapshot.Processes.Count)') -lt 0 -or
    $ancestor.Extent.Text.IndexOf('if ($snapshot.Processes.Count)') -gt
        $ancestor.Extent.Text.IndexOf('Get-IMCPython')) { throw 'Interpreter preflight escaped guarded start branch' }
$runner=Join-Path $root 'LAUNCHERS\RUN_IMC_SIGNAL_ONLY.ps1'
$global:IMCTestAlreadyRunning=$true
# Copy unchanged entrypoints and isolate their dependencies. This fixture
# must never read production maintenance state or invoke its interpreter.
$fixture=Join-Path ([IO.Path]::GetTempPath()) ('imc-entry-'+[guid]::NewGuid().ToString('N'))
$null=New-Item -ItemType Directory -Path (Join-Path $fixture 'LAUNCHERS'),(Join-Path $fixture 'TOOLS')
foreach($name in @('START_IMC.ps1','START_IMC_SIGNAL_ONLY.ps1')) {
    Copy-Item -LiteralPath (Join-Path $root ('LAUNCHERS\'+$name)) -Destination (Join-Path $fixture ('LAUNCHERS\'+$name))
}
Copy-Item -LiteralPath (Join-Path $root 'LAUNCHERS\IMC_PROCESS_COMMON.ps1') -Destination (Join-Path $fixture 'LAUNCHERS\IMC_PROCESS_COMMON.ps1')
$runner=Join-Path $fixture 'LAUNCHERS\RUN_IMC_SIGNAL_ONLY.ps1'
[IO.File]::WriteAllText((Join-Path $fixture 'LAUNCHERS\IMC_PYTHON_COMMON.ps1'),@'
function Get-IMCPythonPath {return 'fixture-no-interpreter'}
function Get-IMCPython {throw 'Already-running fixture invoked import preflight'}
'@)
[IO.File]::WriteAllText((Join-Path $fixture 'TOOLS\IMC_MAINTENANCE.ps1'),@'
function Enter-IMCMaintenanceFence($Python,$Purpose) {
    if($Python -cne 'fixture-no-interpreter' -or $Purpose -cne 'Start'){throw 'Fixture fence contract mismatch'}
    $global:IMCTestFenceChecks++;return @{Owned=$true}
}
function Exit-IMCMaintenanceFence($Lease) {$global:IMCTestFenceReleases++}
'@)
$global:IMCTestFenceChecks=0;$global:IMCTestFenceReleases=0
try { foreach ($relative in @('LAUNCHERS\START_IMC.ps1','LAUNCHERS\START_IMC_SIGNAL_ONLY.ps1')) {
    $global:IMCTestEntryRows=@(
        [pscustomobject]@{Name='powershell.exe'; ProcessId=901; CommandLine=('powershell.exe -File "'+$runner+'"')},
        [pscustomobject]@{Name='powershell.exe'; ProcessId=902; CommandLine=('powershell.exe -File "'+(Join-Path $fixture $relative)+'"')}
    )
    $result=@(& (Join-Path $fixture $relative))
    if (($result -join '') -cne 'IMC supervised runtime start requested; persisted execution gates remain authoritative.') {
        throw 'Already-running start did not return the bounded status'
    }
} } finally {Remove-Item -LiteralPath $fixture -Recurse -Force}
if($global:IMCTestFenceChecks -ne 2 -or $global:IMCTestFenceReleases -ne 2){throw 'Fixture fence lifecycle missing'}
Write-Host 'Canonical entrypoint and already-running checks passed; no native interpreter or services started.'
