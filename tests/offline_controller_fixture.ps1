param([string]$Workspace,[string]$Python,[ValidateSet('retain','partial','released','safety','diagnostic','recover')][string]$Case)
$ErrorActionPreference='Stop'
$Root=Split-Path -Parent $PSScriptRoot
$controller=Join-Path $Root 'TOOLS\Invoke-IMC-OfflineAudit.ps1'
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($controller,[ref]$tokens,[ref]$errors)
if ($errors.Count) {throw 'controller_syntax_invalid'}
# Load only function definitions from the actual reviewed source, never its
# production entrypoint, imports, process selection or STOP body.
$functions=$ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst]},$false)
foreach($definition in $functions) {. ([scriptblock]::Create($definition.Extent.Text))}
if ($Case -ceq 'recover') {
    . (Join-Path $Root 'TOOLS\IMC_RUNTIME_JOB.ps1')
    $scope=Join-Path $Workspace 'controller-scope';$null=New-Item -ItemType Directory -Path $scope
    $nonce=[guid]::NewGuid().ToString('N');$record=@{};$job=$null
    # The fixture owns unique source files; the real global fence additionally
    # blocks concurrent canonical startup during this actual worker handshake.
    . (Join-Path $Root 'TOOLS\IMC_MAINTENANCE.ps1')
    $lease=Enter-IMCMaintenanceFence -Python $Python -Purpose Maintenance
    try {
        Recover-QuiescedLedger -Ledger (Join-Path $Workspace 'source\execution-ledger.db') -Gate (Join-Path $Workspace 'gate.json')
        if($record.ledger_recovery.integrity -ne $true -or $record.ledger_worker_native_pid -isnot [int]){throw 'fixture_recovery_proof_missing'}
        Write-Output 'isolated owned recovery PASS'
    } finally {
        if($job -and -not $job.CleanupConfirmed){$job.ShutdownWithin(10000)}
        Exit-IMCMaintenanceFence $lease
    }
} elseif ($Case -ceq 'diagnostic') {
    if ((Safe-FailureCode 'OFFLINE_SAFETY_UNPROVEN') -cne 'OFFLINE_SAFETY_UNPROVEN') {throw 'diagnostic_known_rejected'}
    foreach($unknown in @('offline_safety_unproven','OFFLINE_SAFETY_UNPROVEN ',"OFFLINE_SAFETY_UNPROVEN`n",'fixture-secret=never-display','https://example.invalid/?key=fixture')) {
        if ((Safe-FailureCode $unknown) -cne 'OFFLINE_INTERNAL_FAILURE') {throw 'diagnostic_unknown_leaked'}
    }
    $knownPath=Join-Path $Root 'TOOLS\Invoke-IMC-OfflineAudit.ps1'
    $record=@{}
    Add-FailureDiagnostics ([pscustomobject]@{Exception=[System.Management.Automation.PSInvalidOperationException]::new('fixture');InvocationInfo=[pscustomobject]@{ScriptName=$knownPath;ScriptLineNumber=1}})
    if($record.exception_kind -cne 'System.Management.Automation.PSInvalidOperationException' -or $record.failure_location.file -cne 'OFFLINE_CONTROLLER' -or $record.failure_location.line -ne 1){throw 'diagnostic_known_location_failed'}
    foreach($bad in @(
        @{ScriptName=$knownPath;ScriptLineNumber=0},@{ScriptName=$knownPath;ScriptLineNumber=5001},
        @{ScriptName=$knownPath;ScriptLineNumber='1'},@{ScriptName='C:\fixture-secret\unknown.ps1';ScriptLineNumber=1},
        @{ScriptName=$knownPath.ToLowerInvariant();ScriptLineNumber=1})) {
        Add-FailureDiagnostics ([pscustomobject]@{Exception=[System.Exception]::new('fixture-secret');InvocationInfo=[pscustomobject]$bad})
        if($record.exception_kind -cne 'UNKNOWN' -or $null -ne $record.failure_location){throw 'diagnostic_unknown_location_leaked'}
    }
    Add-FailureDiagnostics ([pscustomobject]@{Exception=[System.Exception]::new('fixture');InvocationInfo=$null})
    if($null -ne $record.failure_location){throw 'diagnostic_missing_info_leaked'}
    Add-FailureDiagnostics $null
    if($null -ne $record.failure_location){throw 'diagnostic_failure_interfered'}
    Write-Output 'isolated diagnostic PASS'
} elseif ($Case -ceq 'safety') {
    $env:PYTHONOPTIMIZE='2'
    try {
        $value=Safety -Ledger (Join-Path $Workspace 'ledger.db') -Gate (Join-Path $Workspace 'gate.json')
        if ($value.gate -cne 'OFF' -or $value.positions -ne 0) {throw 'safety_result_invalid'}
        Write-Output 'isolated optimized safety PASS'
    } catch {Write-Output 'isolated optimized safety REJECT';exit 2}
} else {
    $record=@{phase='PREPARE'}
    # A real filesystem error in Save-Record, plus a separately failed output.
    $recordPath=Join-Path (Join-Path $Workspace 'ordinary-file') 'report.json'
    [IO.File]::WriteAllText((Join-Path $Workspace 'ordinary-file'),'fixture')
    function Write-Output {
        if ($Case -ceq 'partial') {[IO.File]::WriteAllText((Join-Path $Workspace 'partial-saved'),'saved')}
        throw 'fixture_output_failure'
    }
    $held=[IO.File]::Open((Join-Path $Workspace 'guarded-file'),[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    [IO.File]::WriteAllText((Join-Path $Workspace 'ready'),'ready')
    if ($Case -ceq 'released') {
        $held.Dispose()
        # Real mutex ownership/release too; post-release logging must not hold.
        $mutex=[Threading.Mutex]::new($false,('Local\IMC_RELEASED_FIXTURE_'+$PID))
        if (-not $mutex.WaitOne(0)) {throw 'fixture_mutex_missing'}
        [IO.File]::WriteAllText((Join-Path $Workspace 'mutex-ready'),'ready')
        $wait=[Diagnostics.Stopwatch]::StartNew()
        while (-not (Test-Path -LiteralPath (Join-Path $Workspace 'parent-opened')) -and $wait.ElapsedMilliseconds -lt 3000) {
            Start-Sleep -Milliseconds 10
        }
        if (-not (Test-Path -LiteralPath (Join-Path $Workspace 'parent-opened'))) {throw 'fixture_parent_did_not_open_mutex'}
        $mutex.ReleaseMutex();$mutex.Dispose()
        $released=$true
        Finish-Released
        Retained 'POST_RELEASE_OUTPUT_FAILURE'
        if ($record.phase -cne 'FINAL' -or $record.restart_allowed -ne $true -or $record.cleanup_confirmed -ne $true) {
            throw 'released_status_false'
        }
        [IO.File]::WriteAllText((Join-Path $Workspace 'finished'),'finished')
        $wait.Restart()
        while (-not (Test-Path -LiteralPath (Join-Path $Workspace 'parent-checked')) -and $wait.ElapsedMilliseconds -lt 3000) {
            Start-Sleep -Milliseconds 10
        }
        if (-not (Test-Path -LiteralPath (Join-Path $Workspace 'parent-checked'))) {throw 'fixture_parent_did_not_check_release'}
        exit 0
    }
    if ($Case -ceq 'partial') {
        $held.Dispose()
        $mutex=[Threading.Mutex]::new($false,('Local\IMC_PARTIAL_FIXTURE_'+$PID))
        if (-not $mutex.WaitOne(0)) {throw 'fixture_partial_mutex_missing'}
        $recordPath=Join-Path $Workspace 'partial-report.json'
        $record.cleanup_confirmed=$true;$record.restart_allowed=$true
        $released=$false
        Retained 'FIXTURE_BETWEEN_FILE_AND_MUTEX_RELEASE'
        throw 'partial_retained_returned'
    }
    Retained 'FIXTURE'
    throw 'retained_returned'
}
