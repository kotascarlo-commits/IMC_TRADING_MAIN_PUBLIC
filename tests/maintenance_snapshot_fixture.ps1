param([string]$Workspace,[string]$Python,[string]$Case)
$ErrorActionPreference='Stop'
$sourceRoot=Split-Path -Parent $PSScriptRoot
if($Case -cin @('NativeFalse','NativeException')) {
    # Preload only the native dependency; actual Close helper stays unchanged.
    Add-Type -TypeDefinition @'
using System;
public static class IMCMaintenanceNativeClose {
    public static bool Throw;
    public static int Calls;
    public static bool CloseHandle(IntPtr handle) {
        Calls++;
        if(Throw)throw new InvalidOperationException("FIXTURE_NATIVE_EXCEPTION");
        return false;
    }
}
'@
    [IMCMaintenanceNativeClose]::Throw=($Case -ceq 'NativeException')
}
. (Join-Path $sourceRoot 'TOOLS\IMC_MAINTENANCE.ps1')
$script:IMCMaintenanceRoot=$Workspace
# An isolated lease double exercises the actual publisher/snapshot helper.
# No production mutex, launcher, source database or runtime is touched.
$lease=@{Owned=$true;Purpose='Maintenance';ActiveNonce=$null}
$path=Join-Path $Workspace 'RUNTIME\maintenance-state.json'
try {
    Set-IMCMaintenanceActive $lease $Python ([guid]::NewGuid().ToString('N')) 'RECOVERY_REQUIRED'
    $protected=$false
    try {
        $attempt=[IO.File]::Open($lease.ExpectedState,[IO.FileMode]::Open,[IO.FileAccess]::Write,[IO.FileShare]::ReadWrite)
        $attempt.Dispose()
    } catch [IO.IOException] {$protected=$true}
    if(-not $protected){throw 'FIXTURE_SNAPSHOT_WRITABLE'}
    if($Case -ceq 'Reason' -or $Case -ceq 'Updated') {
        $marker=[IO.File]::ReadAllText($path)|ConvertFrom-Json
        if($Case -ceq 'Reason'){$marker.reason='OFFLINE_AUDIT'}else{$marker.updated_at='2026-10-03T00:00:00Z'}
        [IO.File]::WriteAllText($path,($marker|ConvertTo-Json -Compress),[Text.UTF8Encoding]::new($false))
    }
    if($Case -ceq 'Missing'){$lease.ExpectedState=$null}
    if($Case -ceq 'Hash'){$lease.SnapshotHash='0'*64}
    if($Case -ceq 'Repoint') {
        $other=[IO.File]::ReadAllText($path)|ConvertFrom-Json;$other.reason='OFFLINE_AUDIT'
        $body=$other|ConvertTo-Json -Compress
        $lease.ExpectedState=Join-Path $Workspace 'other-active.json'
        [IO.File]::WriteAllText($lease.ExpectedState,$body,[Text.UTF8Encoding]::new($false))
        [IO.File]::WriteAllText($path,$body,[Text.UTF8Encoding]::new($false))
    }
    if($Case -ceq 'CloseException') {
        function Close-IMCMaintenanceSnapshot($Lease) {
            $Lease.SnapshotCloseFault=$true;throw 'IMC_MAINTENANCE_SNAPSHOT_CLOSE_UNCONFIRMED'
        }
    }
    if($Case -ceq 'Readback') {
        function Read-IMCMaintenanceState($Python){throw 'IMC_MAINTENANCE_UNKNOWN'}
    }
    $before=[IO.File]::ReadAllText($path)
    $rejected=$false
    try {Complete-IMCMaintenance $lease $Python $true $true $true} catch {$rejected=$true}
    if($Case -cin @('Happy','Repeat')) {
        if($rejected -or $lease.ActiveNonce -or $lease.SnapshotGuard){throw 'FIXTURE_COMPLETE_FAILED'}
        if($Case -ceq 'Repeat') {
            $before=[IO.File]::ReadAllText($path);$rejected=$false
            try {Complete-IMCMaintenance $lease $Python $true $true $true} catch {$rejected=$true}
            if(-not $rejected -or [IO.File]::ReadAllText($path) -cne $before){throw 'FIXTURE_REPEAT_CHANGED'}
        }
    } else {
        if($Case -cin @('NativeFalse','NativeException')) {
            if(-not $lease.SnapshotCloseFault -or [IMCMaintenanceNativeClose]::Calls -ne 1 -or
                $lease.SnapshotGuard.SafeFileHandle.IsClosed){throw 'FIXTURE_NATIVE_CLOSE_STATE'}
        }
        if($Case -ceq 'Readback') {
            if(-not $rejected -or -not $lease.ActiveNonce -or $lease.SnapshotGuard -or
                ([IO.File]::ReadAllText($path)|ConvertFrom-Json).state -cne 'IDLE'){throw 'FIXTURE_READBACK_STATE'}
            # Publication succeeded; readback is unknown and lease stays blocked.
            $before=[IO.File]::ReadAllText($path)
        }
        if(-not $rejected -or -not $lease.ActiveNonce -or [IO.File]::ReadAllText($path) -cne $before) {
            throw 'FIXTURE_REJECTION_CHANGED'
        }
        # Only the disposable fixture exits. No production release is inferred.
        if($Case -cne 'Readback') {
            if($Case -cin @('CloseException','NativeFalse','NativeException')) {
                # Fixture injection keeps the real original handle open.
                $lease.SnapshotGuard.Dispose()
            } elseif($lease.SnapshotGuard) {Close-IMCMaintenanceSnapshot $lease}
        }
        $retryRejected=$false;$exitRejected=$false
        try {Complete-IMCMaintenance $lease $Python $true $true $true} catch {$retryRejected=$true}
        try {Exit-IMCMaintenanceFence $lease} catch {$exitRejected=$true}
        if(-not $retryRejected -or -not $exitRejected -or [IO.File]::ReadAllText($path) -cne $before) {
            throw 'FIXTURE_FAILURE_RETRIED'
        }
        if($Case -cin @('NativeFalse','NativeException') -and [IMCMaintenanceNativeClose]::Calls -ne 1) {
            throw 'FIXTURE_NATIVE_CLOSE_RETRIED'
        }
    }
    Write-Output 'PASS'
} catch {Write-Output 'FIXTURE_FAILED';exit 2}
