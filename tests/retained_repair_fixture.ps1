param([string]$Workspace,[string]$Python,[string]$Case)
$ErrorActionPreference='Stop'
$sourceRoot=Split-Path -Parent $PSScriptRoot
$repair=Join-Path $sourceRoot 'TOOLS\Repair-IMC-RetainedAudit.ps1'
$ast=[Management.Automation.Language.Parser]::ParseFile($repair,[ref]$null,[ref]$null)
$controller=[Management.Automation.Language.Parser]::ParseFile((Join-Path $sourceRoot 'TOOLS\Invoke-IMC-OfflineAudit.ps1'),[ref]$null,[ref]$null)
foreach($definition in $controller.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$false)) {
    . ([scriptblock]::Create($definition.Extent.Text))
}
# Execute the actual operator declarations, functions and complete try/catch
# body. Only dependencies are doubled; never its production imports/entrypoint.
$text=$ast.Extent.Text
$main=@($ast.EndBlock.Statements|Where-Object {$_ -is [Management.Automation.Language.TryStatementAst]})
if($main.Count -ne 1){throw 'fixture_operator_shape_changed'}
$start=$text.IndexOf('$oldPid=18720;')
if($start -lt 0){throw 'fixture_operator_identity_changed'}
$Root=$Workspace
$controller=Join-Path $Root 'TOOLS\Invoke-IMC-OfflineAudit.ps1'
. ([scriptblock]::Create($text.Substring($start,$main[0].Extent.StartOffset-$start)))
$shell=Join-Path ([Environment]::GetFolderPath('System')) 'WindowsPowerShell\v1.0\powershell.exe'
$global:fixtureCalls=[Collections.Generic.List[string]]::new()
function Call([string]$Name){$global:fixtureCalls.Add($Name);[IO.File]::AppendAllText((Join-Path $Workspace 'calls'),$Name+"`n")}
function Get-CimInstance {
    return @(
        [pscustomobject]@{ProcessId=$PID;ExecutablePath=$shell;CommandLine=($shell+' -NoProfile -NonInteractive -ExecutionPolicy Bypass -File ""')},
        [pscustomobject]@{ProcessId=$oldPid;ExecutablePath=$shell;CommandLine=($shell+' -NoProfile -NonInteractive -ExecutionPolicy Bypass -File '+(Join-Path $Root 'TOOLS\Invoke-IMC-OfflineAudit.ps1')+' -ExpectedSupervisorPid 15356 -ExpectedBirthUtc 2026-10-02T23:24:41.3771672Z')}
    )
}
. (Join-Path $sourceRoot 'LAUNCHERS\IMC_PROCESS_COMMON.ps1')
$fakeOld=[pscustomobject]@{StartTime=[datetime]$oldBirth;HasExited=$false}
if($Case -ceq 'identity'){$fakeOld.StartTime=$fakeOld.StartTime.AddSeconds(1)}
$fakeOld|Add-Member ScriptMethod Kill {Call 'old.kill';$this.HasExited=$true}
$fakeOld|Add-Member ScriptMethod WaitForExit {param($Milliseconds)Call 'old.exit';return $true}
$fakeOld|Add-Member ScriptMethod Dispose {Call 'old.dispose'}
$fakeSelf=[pscustomobject]@{}
$fakeSelf|Add-Member ScriptMethod Dispose {Call 'self.dispose'}
function Hold($Row){if($Row.ProcessId -eq $oldPid){$script:handles+=$fakeOld;return $fakeOld};$script:handles+=$fakeSelf;return $fakeSelf}
function Fresh-Absence {Call 'absence'}
function Gate-Off {Call 'gate.off'}
function Safety {Call 'safety';return @{gate='OFF';schema_version=2;positions=0;uncertain=0;receipts=0}}
function Recover-QuiescedLedger {Call 'recovery.verified';$record.ledger_recovery=@{integrity=$true};$script:job=$null}
function Enter-IMCMaintenanceFence {param($Python,$Purpose)Call 'fence.enter';return @{Owned=$true;Purpose='Maintenance';ActiveNonce=$null}}
function Set-IMCMaintenanceActive($Lease,$Python,$Nonce,$Reason) {
    Call 'marker.active';$Lease.ActiveNonce=$Nonce
    $Lease.ExpectedState=Join-Path $Workspace ('expected-'+$Nonce+'.json')
    $response=(& $Python -I (Join-Path $sourceRoot 'scripts\maintenance_state.py') active --path (Join-Path $Workspace 'state.json') --nonce $Nonce --owner-pid $PID --owner-birth '2026-10-03T00:00:00Z' --reason $Reason --published-state $Lease.ExpectedState)|ConvertFrom-Json
    if($LASTEXITCODE -ne 0){throw 'fixture_marker_failure'}
    $Lease.SnapshotHash=$response.snapshot_sha256
}
function Complete-IMCMaintenance($Lease,$Python,$Integrity,$Safety,$NativeCleanup) {
    if(-not $Integrity -or -not $Safety -or -not $NativeCleanup){throw 'fixture_unproven_complete'}
    Call 'marker.idle'
    & $Python -I (Join-Path $sourceRoot 'scripts\maintenance_state.py') idle --path (Join-Path $Workspace 'state.json') --nonce $Lease.ActiveNonce --owner-pid $PID --owner-birth '2026-10-03T00:00:00Z' --expected-state $Lease.ExpectedState --expected-sha256 $Lease.SnapshotHash | Out-Null
    if($LASTEXITCODE -ne 0){throw 'fixture_idle_failure'}
    $Lease.ActiveNonce=$null
}
function Exit-IMCMaintenanceFence($Lease){Call 'fence.exit'}
function New-RepairMutex([string]$Name) {
    $m=[pscustomobject]@{Name=$Name;Legacy=$Name.StartsWith('Local\');Waits=0}
    $m|Add-Member ScriptMethod WaitOne {
        param($Milliseconds)
        $this.Waits++
        if(-not $this.Legacy){return $true}
        if($this.Waits -eq 1){return $false}
        Call 'legacy.takeover'
        if($Case -ceq 'legacy'){return $false}
        if($Case -ceq 'crash') {
            [IO.File]::WriteAllText((Join-Path $Workspace 'crash-ready'),'ready')
            while($true){[Threading.Thread]::Sleep(10)}
        }
        return $true
    }
    $m|Add-Member ScriptMethod ReleaseMutex {Call 'mutex.release'}
    $m|Add-Member ScriptMethod Dispose {Call 'mutex.dispose'}
    return $m
}
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class IMCAuditNativeClose {
    [DllImport("kernel32.dll",SetLastError=true)] public static extern bool CloseHandle(IntPtr handle);
}
'@
if($Case -ceq 'close'){function Close-IMCAuditFile($Stream){Call 'close.failure';throw 'OFFLINE_PARENT_FILE_CLOSE_UNCONFIRMED'}}
function Start-Sleep {
    param($Seconds)
    # Catch's production retain-loop is observed, not allowed to release.
    if($record.phase -cne 'RETAINED'){throw 'fixture_unexpected_sleep'}
    [IO.File]::WriteAllText((Join-Path $Workspace 'fixture-failure'),($_.ToString()+"`n"+$_.InvocationInfo.PositionMessage))
    [IO.File]::WriteAllText((Join-Path $Workspace 'retained-ready'),'ready')
    while($true){[Threading.Thread]::Sleep(10)}
}
. ([scriptblock]::Create($main[0].Extent.Text))
