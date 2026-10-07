param([string]$Workspace,[string]$Python,[string]$Case)
$ErrorActionPreference='Stop'
$sourceRoot=Split-Path -Parent $PSScriptRoot
$Root=$Workspace
$repair=Join-Path $sourceRoot 'TOOLS\Repair-IMC-RetainedAudit.ps1'
$ast=[Management.Automation.Language.Parser]::ParseFile($repair,[ref]$null,[ref]$null)
$text=$ast.Extent.Text
$start=$text.IndexOf('$oldPid=18720;')
$end=$text.IndexOf('if($Mode -ceq ''CopyTimeout'')')
if($start -lt 0 -or $end -le $start){throw 'fixture_repair_shape_changed'}
$controller=Join-Path $Root 'TOOLS\Invoke-IMC-OfflineAudit.ps1'
$ExpectedControllerPid=10528;$ExpectedControllerBirth='2026-10-03T04:16:05.7548417Z'
$CopyNonce='14fce188ea2946deadd61bfe660a6cbf'
$JobProofScope=Join-Path $Root '.local-tools\retained-job-dddddddddddddddddddddddddddddddd'
. ([scriptblock]::Create($text.Substring($start,$end-$start)))
$script:fixtureOldGone=$false
function Call([string]$Name){[IO.File]::AppendAllText((Join-Path $Workspace 'calls'),$Name+"`n")}
. (Join-Path $sourceRoot 'LAUNCHERS\IMC_PROCESS_COMMON.ps1')
$shell=Join-Path ([Environment]::GetFolderPath('System')) 'WindowsPowerShell\v1.0\powershell.exe'
function Get-CimInstance {
    return @(
        [pscustomobject]@{ProcessId=$PID;ExecutablePath=$shell;CommandLine=($shell+' -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "" -Mode CopyTimeout -ExpectedControllerPid 10528 -ExpectedControllerBirth '+$ExpectedControllerBirth+' -CopyNonce '+$CopyNonce+' -JobProofScope "'+$JobProofScope+'"')},
        [pscustomobject]@{ProcessId=10528;ExecutablePath=$shell;CommandLine=($shell+' -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+$controller+'" -ExpectedSupervisorPid 18596 -ExpectedBirthUtc 2026-10-03T03:29:11.3000225Z -Operation Copy')}
    )
}
$fakeOld=[pscustomobject]@{StartTime=[datetime]$ExpectedControllerBirth;HasExited=$false}
if($Case -ceq 'identity'){$fakeOld.StartTime=$fakeOld.StartTime.AddSeconds(1)}
$fakeOld|Add-Member ScriptMethod Kill {Call 'old.kill';$this.HasExited=$true;$script:fixtureOldGone=$true}
$fakeOld|Add-Member ScriptMethod WaitForExit {param($Milliseconds)Call 'old.exit';return ($Case -cne 'exit')}
$fakeSelf=[pscustomobject]@{StartTime=[datetime]'2026-10-03T08:00:00Z'}
function Hold($Row){if($Row.ProcessId -eq 10528){return $fakeOld};return $fakeSelf}
function Gate-Off {Call 'gate.off'}
function Fresh-Absence {Call 'absence'}
function Safety {Call 'safety';return @{gate='OFF';schema_version=2;positions=0;uncertain=0;receipts=0}}
function Read-RepairJson([string]$Path) {
    $value=Get-Content -LiteralPath $Path -Raw -Encoding UTF8|ConvertFrom-Json
    if($Case -ceq 'proof' -and $Path.EndsWith('done.json')){$value.native_cleanup_confirmed=$false}
    return $value
}
function New-RepairMutex([string]$Name) {
    $m=[pscustomobject]@{Name=$Name;Waits=0}
    $m|Add-Member ScriptMethod WaitOne {
        param($Milliseconds)
        $this.Waits++
        if($this.Waits -eq 1){return $false}
        Call 'mutex.takeover'
        return ($Case -cne 'mutex')
    }
    return $m
}
function Read-RepairMutex($Mutex) {
    Call 'mutex.native'
    if($script:fixtureOldGone){
        return [pscustomobject]@{Owned=$true;Abandoned=$false;Count=0;OwnerProcess=$PID;OwnerThread=9;CallerThread=9}
    }
    return [pscustomobject]@{Owned=$false;Abandoned=$false;Count=0;OwnerProcess=10528;OwnerThread=18008;CallerThread=9}
}
function Run-RetainedCopyVerification {
    param($OldScope,$OldNonce,$FailedReportPath,$OldProducer,$BudgetSeconds)
    Call 'verify'
    if($Case -ceq 'validator'){throw 'fixture_validator_failure'}
    $script:job=[pscustomobject]@{CleanupConfirmed=$true}
    return @{complete=$true;eligible_for_publication=$false}
}
function Start-Sleep {
    param($Seconds)
    if($record.phase -cnotin @('RETAINED','VERIFIED_HELD')){throw 'fixture_unexpected_sleep'}
    [IO.File]::WriteAllText((Join-Path $Workspace 'fixture-failure'),($_.Exception.Message+':'+$_.InvocationInfo.ScriptLineNumber))
    [IO.File]::WriteAllText((Join-Path $Workspace 'held-ready'),$record.phase)
    while($true){[Threading.Thread]::Sleep(10)}
}
# A generation change is injected after the actual intent was flushed, before
# the CLI compare. Only the fixture marker is changed; never production state.
if($Case -ceq 'generation') {
    $fakeOld.PSObject.Members.Remove('Kill')
    $fakeOld|Add-Member ScriptMethod Kill {
        Call 'old.kill';$this.HasExited=$true;$script:fixtureOldGone=$true
        $path=Join-Path $Workspace 'RUNTIME\maintenance-state.json'
        $marker=Get-Content $path -Raw|ConvertFrom-Json;$marker.updated_at='2026-10-03T04:16:09Z'
        [IO.File]::WriteAllText($path,($marker|ConvertTo-Json -Compress))
    }
}
Repair-CopyTimeout
