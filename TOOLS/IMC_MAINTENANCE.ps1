# A global native fence plus durable ACTIVE marker. No owner-gate writes.
# START releases its check before spawning RUN; RUN independently holds the
# fence through its own startup. This prevents an asynchronous handover lock.
$script:IMCMaintenanceRoot=Split-Path -Parent $PSScriptRoot
if(-not ('IMCMaintenanceNativeClose' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class IMCMaintenanceNativeClose {
    [DllImport("kernel32.dll",SetLastError=true)] public static extern bool CloseHandle(IntPtr handle);
    [DllImport("kernel32.dll",SetLastError=true)] static extern IntPtr OpenProcess(uint access,bool inherit,int pid);
    [DllImport("kernel32.dll",SetLastError=true)] static extern uint WaitForSingleObject(IntPtr handle,uint wait);
    [DllImport("kernel32.dll",SetLastError=true)] static extern bool GetProcessTimes(IntPtr handle,out long created,out long exited,out long kernel,out long user);
    public static bool OwnerEnded(int pid,long birthFileTime) {
        var h=OpenProcess(0x100000|0x1000,false,pid);
        if(h==IntPtr.Zero) {
            var error=Marshal.GetLastWin32Error();
            if(error==87)return true;
            throw new System.ComponentModel.Win32Exception(error);
        }
        try {
            long created,exited,kernel,user;
            if(!GetProcessTimes(h,out created,out exited,out kernel,out user))throw new System.ComponentModel.Win32Exception();
            var state=WaitForSingleObject(h,0);
            if(state!=0 && state!=258)throw new System.ComponentModel.Win32Exception();
            return state==0 || created!=birthFileTime;
        } finally {if(!CloseHandle(h))throw new System.ComponentModel.Win32Exception();}
    }
}
'@
}
function Close-IMCMaintenanceSnapshot($Lease) {
    if(-not $Lease.SnapshotGuard -or $Lease.SnapshotCloseFault){throw 'IMC_MAINTENANCE_SNAPSHOT_UNKNOWN'}
    $handle=$Lease.SnapshotGuard.SafeFileHandle
    if($handle.IsClosed -or $handle.IsInvalid){throw 'IMC_MAINTENANCE_SNAPSHOT_UNKNOWN'}
    $Lease.SnapshotCloseFault=$true
    if(-not [IMCMaintenanceNativeClose]::CloseHandle($handle.DangerousGetHandle())){throw 'IMC_MAINTENANCE_SNAPSHOT_CLOSE_UNCONFIRMED'}
    $handle.SetHandleAsInvalid();$Lease.SnapshotGuard.Dispose();$Lease.SnapshotGuard=$null
}
function Assert-IMCMaintenanceSnapshot($Lease) {
    if(-not $Lease.ExpectedState -or -not $Lease.SnapshotGuard -or $Lease.SnapshotCloseFault -or
        $Lease.SnapshotGuard.SafeFileHandle.IsClosed -or $Lease.SnapshotGuard.SafeFileHandle.IsInvalid -or
        -not $Lease.SnapshotGuard.CanRead -or $Lease.SnapshotGuard.CanWrite){throw 'IMC_MAINTENANCE_SNAPSHOT_UNKNOWN'}
    $Lease.SnapshotGuard.Position=0;$sha=[Security.Cryptography.SHA256]::Create()
    try {$digest=[BitConverter]::ToString($sha.ComputeHash($Lease.SnapshotGuard)).Replace('-','').ToLowerInvariant()}
    finally {$sha.Dispose()}
    if($digest -cne $Lease.SnapshotHash){throw 'IMC_MAINTENANCE_SNAPSHOT_CHANGED'}
}
function Read-IMCMaintenanceState($Python) {
    $rows=@(& $Python -I (Join-Path $script:IMCMaintenanceRoot 'scripts\maintenance_state.py') read `
        --path (Join-Path $script:IMCMaintenanceRoot 'RUNTIME\maintenance-state.json') 2>$null)
    if($LASTEXITCODE -ne 0 -or $rows.Count -ne 1){throw 'IMC_MAINTENANCE_UNKNOWN'}
    $value=$rows[0]|ConvertFrom-Json
    if(@($value.PSObject.Properties).Count -ne 2 -or $value.state -cnotin @('ACTIVE','IDLE') -or
        $value.nonce -cnotmatch '\A[a-f0-9]{32}\z'){throw 'IMC_MAINTENANCE_UNKNOWN'}
    return $value
}
function Enter-IMCMaintenanceFence {
    param([Parameter(Mandatory)][string]$Python,[ValidateSet('Start','Maintenance')][string]$Purpose='Start',
        [scriptblock]$StateReader)
    $mutex=[Threading.Mutex]::new($false,'Global\IMC_TRADING_MAIN_MAINTENANCE_V1')
    $owned=$false;$abandoned=$false
    try {
        try {$owned=$mutex.WaitOne(0)} catch [Threading.AbandonedMutexException] {$owned=$true;$abandoned=$true}
        if(-not $owned){throw 'IMC_MAINTENANCE_BUSY'}
        if($Purpose -ceq 'Start') {
            if($abandoned) {
                $recoveryLease=@{Mutex=$mutex;Owned=$true;Purpose='Maintenance';Abandoned=$true;ActiveNonce=$null}
                try {Set-IMCMaintenanceActive $recoveryLease $Python ([guid]::NewGuid().ToString('N')) 'RECOVERY_REQUIRED'}
                catch {while($true){try{Start-Sleep -Seconds 5}catch{[Threading.Thread]::Sleep(5000)}}}
                # The durable ACTIVE marker now blocks every later start;
                # this attempt has not acquired RuntimeMutex or started work.
                try {Close-IMCMaintenanceSnapshot $recoveryLease}
                catch {while($true){try{Start-Sleep -Seconds 5}catch{[Threading.Thread]::Sleep(5000)}}}
                $mutex.ReleaseMutex();$owned=$false
                throw 'IMC_MAINTENANCE_RECOVERY_REQUIRED'
            }
            $state=if($StateReader){& $StateReader}else{Read-IMCMaintenanceState $Python}
            if($state.state -cne 'IDLE'){throw 'IMC_MAINTENANCE_RECOVERY_REQUIRED'}
        }
        return @{Mutex=$mutex;Owned=$true;Purpose=$Purpose;Abandoned=$abandoned;ActiveNonce=$null}
    } catch {
        if($owned){$mutex.ReleaseMutex()};$mutex.Dispose();throw
    }
}
function Set-IMCMaintenanceActive($Lease,$Python,[string]$Nonce,[string]$Reason='OFFLINE_AUDIT') {
    if(-not $Lease.Owned -or $Lease.Purpose -cne 'Maintenance' -or $Nonce -cnotmatch '\A[a-f0-9]{32}\z') {
        throw 'IMC_MAINTENANCE_OWNER_INVALID'
    }
    $self=Get-Process -Id $PID -ErrorAction Stop
    try {$null=$self.Handle;$birth=$self.StartTime.ToUniversalTime().ToString('o')}finally{$self.Dispose()}
    $Lease.ActiveNonce=$Nonce # failures after publication must not release the fence
    $snapshotScope=Join-Path $script:IMCMaintenanceRoot ('RUNTIME\maintenance-snapshots\'+[guid]::NewGuid().ToString('N'))
    $null=New-Item -ItemType Directory -Path $snapshotScope -ErrorAction Stop
    $Lease.ExpectedState=Join-Path $snapshotScope 'active.json'
    $rows=@(& $Python -I (Join-Path $script:IMCMaintenanceRoot 'scripts\maintenance_state.py') active `
        --path (Join-Path $script:IMCMaintenanceRoot 'RUNTIME\maintenance-state.json') --nonce $Nonce `
        --owner-pid $PID --owner-birth $birth --reason $Reason --published-state $Lease.ExpectedState 2>$null)
    if($LASTEXITCODE -ne 0 -or $rows.Count -ne 1){throw 'IMC_MAINTENANCE_PERSIST_FAILED'}
    $published=$rows[0]|ConvertFrom-Json
    if(@($published.PSObject.Properties).Count -ne 3 -or $published.state -cne 'ACTIVE' -or
        $published.nonce -cne $Nonce -or $published.snapshot_sha256 -cnotmatch '\A[a-f0-9]{64}\z') {
        throw 'IMC_MAINTENANCE_SNAPSHOT_UNKNOWN'
    }
    $Lease.SnapshotHash=$published.snapshot_sha256
    $Lease.SnapshotGuard=[IO.File]::Open($Lease.ExpectedState,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    Assert-IMCMaintenanceSnapshot $Lease
    $state=Read-IMCMaintenanceState $Python
    if($state.state -cne 'ACTIVE' -or $state.nonce -cne $Nonce){throw 'IMC_MAINTENANCE_PERSIST_FAILED'}
}
function Complete-IMCMaintenance($Lease,$Python,[bool]$Integrity,[bool]$Safety,[bool]$NativeCleanup) {
    if(-not $Lease.Owned -or $Lease.Purpose -cne 'Maintenance' -or -not $Lease.ActiveNonce -or
        -not $Integrity -or -not $Safety -or -not $NativeCleanup){throw 'IMC_MAINTENANCE_CLEANUP_UNPROVEN'}
    $self=Get-Process -Id $PID -ErrorAction Stop
    try {$null=$self.Handle;$birth=$self.StartTime.ToUniversalTime().ToString('o')}finally{$self.Dispose()}
    Assert-IMCMaintenanceSnapshot $Lease
    # Native snapshot cleanup is proved before publishing IDLE. Python binds
    # its one snapshot read to the publisher's SHA even after this guard closes.
    Close-IMCMaintenanceSnapshot $Lease
    $rows=@(& $Python -I (Join-Path $script:IMCMaintenanceRoot 'scripts\maintenance_state.py') idle `
        --path (Join-Path $script:IMCMaintenanceRoot 'RUNTIME\maintenance-state.json') --nonce $Lease.ActiveNonce `
        --owner-pid $PID --owner-birth $birth --expected-state $Lease.ExpectedState --expected-sha256 $Lease.SnapshotHash 2>$null)
    if($LASTEXITCODE -ne 0 -or $rows.Count -ne 1){throw 'IMC_MAINTENANCE_PERSIST_FAILED'}
    $state=Read-IMCMaintenanceState $Python
    if($state.state -cne 'IDLE' -or $state.nonce -cne $Lease.ActiveNonce){throw 'IMC_MAINTENANCE_PERSIST_FAILED'}
    $Lease.ActiveNonce=$null
}
function Recover-IMCMaintenanceActive($Lease,$Python) {
    if(-not $Lease.Owned -or $Lease.Purpose -cne 'Maintenance' -or $Lease.ActiveNonce){throw 'IMC_MAINTENANCE_OWNER_INVALID'}
    $scope=Join-Path $script:IMCMaintenanceRoot ('RUNTIME\maintenance-snapshots\'+[guid]::NewGuid().ToString('N'))
    $null=New-Item -ItemType Directory -Path $scope -ErrorAction Stop
    $oldPath=Join-Path $scope 'old-active.json'
    [IO.File]::WriteAllBytes($oldPath,[IO.File]::ReadAllBytes((Join-Path $script:IMCMaintenanceRoot 'RUNTIME\maintenance-state.json')))
    $oldGuard=[IO.File]::Open($oldPath,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try {
        $old=Get-Content -LiteralPath $oldPath -Raw -Encoding UTF8|ConvertFrom-Json
        if($old.state -cne 'ACTIVE' -or -not [IMCMaintenanceNativeClose]::OwnerEnded(
                [int]$old.owner_pid,[DateTimeOffset]::Parse($old.owner_birth).UtcDateTime.ToFileTimeUtc())) {
            throw 'IMC_MAINTENANCE_OWNER_PRESENT_OR_UNKNOWN'
        }
        $self=Get-Process -Id $PID -ErrorAction Stop
        try {$null=$self.Handle;$birth=$self.StartTime.ToUniversalTime().ToString('o')}finally{$self.Dispose()}
        $nonce=[guid]::NewGuid().ToString('N');$Lease.ExpectedState=Join-Path $scope 'active.json'
        $Lease.ActiveNonce=$nonce
        $rows=@(& $Python -I (Join-Path $script:IMCMaintenanceRoot 'scripts\maintenance_state.py') handover `
            --path (Join-Path $script:IMCMaintenanceRoot 'RUNTIME\maintenance-state.json') --expected-state $oldPath `
            --nonce $nonce --owner-pid $PID --owner-birth $birth --published-state $Lease.ExpectedState 2>$null)
        if($LASTEXITCODE -ne 0 -or $rows.Count -ne 1){throw 'IMC_MAINTENANCE_HANDOVER_UNCONFIRMED'}
        $published=$rows[0]|ConvertFrom-Json
        if($published.state -cne 'ACTIVE' -or $published.nonce -cne $nonce -or
                $published.snapshot_sha256 -cnotmatch '\A[a-f0-9]{64}\z'){throw 'IMC_MAINTENANCE_HANDOVER_UNCONFIRMED'}
        $Lease.SnapshotHash=$published.snapshot_sha256
        $Lease.SnapshotGuard=[IO.File]::Open($Lease.ExpectedState,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
        Assert-IMCMaintenanceSnapshot $Lease
    } finally {$oldGuard.Dispose()}
}
function Exit-IMCMaintenanceFence($Lease) {
    if($Lease.ActiveNonce){throw 'IMC_MAINTENANCE_CLEANUP_UNPROVEN'}
    if($Lease.Owned){$Lease.Mutex.ReleaseMutex();$Lease.Owned=$false}
    $Lease.Mutex.Dispose()
}
