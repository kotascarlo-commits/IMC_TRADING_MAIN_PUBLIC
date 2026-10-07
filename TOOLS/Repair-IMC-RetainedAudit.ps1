param([ValidateSet('Legacy','CopyTimeout')][string]$Mode='Legacy',
      [int]$ExpectedControllerPid=0,[string]$ExpectedControllerBirth='',
      [string]$CopyNonce='',[string]$JobProofScope='')
# Explicit retained-controller recovery; the legacy path remains unchanged.
# Not a runtime entrypoint or another controller implementation. Shared
# validators/recovery functions are loaded from the current canonical source.
$ErrorActionPreference='Stop'
$Root=Split-Path -Parent $PSScriptRoot
. (Join-Path $Root 'LAUNCHERS\IMC_PYTHON_COMMON.ps1')
. (Join-Path $Root 'LAUNCHERS\IMC_PROCESS_COMMON.ps1')
. (Join-Path $Root 'TOOLS\IMC_RUNTIME_JOB.ps1')
. (Join-Path $Root 'TOOLS\IMC_MAINTENANCE.ps1')
$Python=Get-IMCPython
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class IMCAuditNativeClose {
    [DllImport("kernel32.dll",SetLastError=true)] public static extern bool CloseHandle(IntPtr handle);
}
'@
$controller=Join-Path $Root 'TOOLS\Invoke-IMC-OfflineAudit.ps1'
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($controller,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'REPAIR_CONTROLLER_SYNTAX_INVALID'}
foreach($definition in $ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$false)) {
    . ([scriptblock]::Create($definition.Extent.Text))
}
$oldPid=18720;$oldBirth='2026-10-03T01:23:38.8241910Z';$oldNonce='4af28cf46a4444cb90881ddb3a4f7981'
$oldPath=Join-Path $Root ('.local-tools\offline-audit-'+$oldNonce+'\controller.json')
$nonce=[guid]::NewGuid().ToString('N')
$scope=Join-Path $Root ('.local-tools\retained-repair-'+$nonce);$null=New-Item -ItemType Directory -Path $scope
$record=@{schema_version=1;nonce=$nonce;pid=$PID;phase='PREPARE';cleanup_confirmed=$false;restart_allowed=$false}
$recordPath=Join-Path $scope 'repair.json'
$handles=@();$job=$null;$lease=$null;$old=$null;$self=$null;$recordGuard=$null
$mutexes=@();$ownedMutexes=@();$finished=$false
function Report { [IO.File]::WriteAllText($recordPath,($record|ConvertTo-Json -Depth 7),[Text.UTF8Encoding]::new($false)) }
function New-RepairMutex([string]$Name) {return [Threading.Mutex]::new($false,$Name)}
function Fresh-Absence {
    $snapshot=Get-IMCProcessSnapshot
    if($snapshot.Processes.Count -or @($snapshot.UnknownInterpreterPids|Where-Object {$_ -ne $PID -and $_ -ne $oldPid}).Count) {
        throw 'REPAIR_OTHER_RUNTIME_PRESENT'
    }
    $raw=@(Get-CimInstance Win32_Process -ErrorAction Stop)
    if(@($raw|Where-Object ParentProcessId -eq $oldPid).Count){throw 'REPAIR_OLD_CHILD_PRESENT'}
    if(@($raw|Where-Object {$_.Name -ieq 'sqlite3.exe' -and (-not $_.CommandLine -or $_.CommandLine.Contains('execution-ledger.db') -or $_.CommandLine.Contains('imc-main.db'))}).Count){throw 'REPAIR_SQLITE_PROCESS_PRESENT'}
}
function Gate-Off {
    $code=@'
import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(sys.argv[1])/'RUNTIME/EXECUTOR_GATEWAY/GMGN_BRIDGE'))
from execution_policy import owner_gate
from mykonos_meme_scout.execution_config import load_execution_config
if owner_gate()!={'value':'OFF','valid':True}:raise ValueError('gate_not_off')
load_execution_config()
print('OFF')
'@
    $rows=@(& $Python -I -c $code $Root 2>$null)
    if($LASTEXITCODE -ne 0 -or $rows.Count -ne 1 -or $rows[0] -cne 'OFF'){throw 'REPAIR_GATE_UNPROVEN'}
}

function Read-RepairJson([string]$Path) {
    $code=@'
import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(sys.argv[1])/'scripts'))
from offline_ledger import decode
body=Path(sys.argv[2]).read_bytes()
if len(body)>131072:raise ValueError('repair_report_size')
print(json.dumps(decode(body.decode('utf-8-sig')),allow_nan=False,separators=(',',':')))
'@
    $rows=@(& $Python -I -c $code $Root $Path 2>$null)
    if($LASTEXITCODE -ne 0 -or $rows.Count -ne 1){throw 'REPAIR_JSON_UNCONFIRMED'}
    return ($rows[0]|ConvertFrom-Json)
}
function Fresh-RepairRecord([string]$Path,$Value) {
    $body=[Text.Encoding]::UTF8.GetBytes(($Value|ConvertTo-Json -Depth 12 -Compress))
    $file=[IO.File]::Open($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
    try{$file.Write($body,0,$body.Length);$file.Flush($true)}finally{$file.Dispose()}
}
function Hold-RepairFile([string]$Path) {
    $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    $script:streams+=$stream
    return $stream
}
function Read-RepairMutex($Mutex) {
    return [IMCNativeMutexState]::Read($Mutex.SafeWaitHandle)
}
function Hash-RepairStream($Stream,$Clock) {
    $Stream.Position=0;$sha=[Security.Cryptography.SHA256]::Create()
    $buffer=New-Object byte[] 1048576;$bytes=0L
    try {
        while($true) {
            if($Clock.Elapsed.TotalSeconds -ge 21600){throw 'REPAIR_SHARED_BUDGET_EXPIRED'}
            $count=$Stream.Read($buffer,0,$buffer.Length)
            if(-not $count){break}
            $null=$sha.TransformBlock($buffer,0,$count,$buffer,0);$bytes+=$count
        }
        $null=$sha.TransformFinalBlock([byte[]]@(),0,0)
        if($bytes -ne $Stream.Length){throw 'REPAIR_HASH_LENGTH_CHANGED'}
        return ([BitConverter]::ToString($sha.Hash)).Replace('-','').ToLowerInvariant()
    } finally {$sha.Dispose()}
}
function Repair-CopyTimeout {
    $script:oldPid=$ExpectedControllerPid;$script:oldBirth=$ExpectedControllerBirth;$script:oldNonce=$CopyNonce
    $script:database=Join-Path $Root 'CORE\data\imc-main.db'
    $script:streams=@();$clock=[Diagnostics.Stopwatch]::StartNew()
    $script:oldPath=Join-Path $Root ('.local-tools\offline-audit-'+$oldNonce+'\controller.json')
    $oldScope=Split-Path -Parent $oldPath
    $proofScope=[IO.Path]::GetFullPath($JobProofScope)
    try {
        if($oldPid -le 0 -or $oldNonce -cnotmatch '\A[a-f0-9]{32}\z' -or
                $proofScope -cnotmatch ('\A'+[regex]::Escape((Join-Path $Root '.local-tools\retained-job-'))+'[a-f0-9]{32}\z')) {
            throw 'REPAIR_COPY_IDENTITY_INVALID'
        }
        $shell=Join-Path ([Environment]::GetFolderPath('System')) 'WindowsPowerShell\v1.0\powershell.exe'
        $raw=@(Get-CimInstance Win32_Process -Filter ('ProcessId='+$PID+' OR ProcessId='+$oldPid) -ErrorAction Stop)
        $selfRows=@($raw|Where-Object ProcessId -eq $PID);$oldRows=@($raw|Where-Object ProcessId -eq $oldPid)
        if($selfRows.Count -ne 1 -or $oldRows.Count -ne 1){throw 'REPAIR_COPY_LIFETIME_MISSING'}
        $script:self=Hold $selfRows[0];$script:old=Hold $oldRows[0]
        if($old.StartTime.ToUniversalTime().ToString('o') -cne $oldBirth -or $old.HasExited){throw 'REPAIR_OLD_BIRTH_CHANGED'}
        $argv=@([IMCCommandLine]::Split($selfRows[0].CommandLine))
        $expected=@($shell,'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',$PSCommandPath,
            '-Mode','CopyTimeout','-ExpectedControllerPid',([string]$oldPid),'-ExpectedControllerBirth',$oldBirth,
            '-CopyNonce',$oldNonce,'-JobProofScope',$proofScope)
        if($argv.Count -ne $expected.Count -or $selfRows[0].ExecutablePath -ine $shell){throw 'REPAIR_SELF_UNKNOWN'}
        for($i=0;$i -lt $expected.Count;$i++){if($argv[$i] -ine $expected[$i]){throw 'REPAIR_SELF_UNKNOWN'}}
        $argv=@([IMCCommandLine]::Split($oldRows[0].CommandLine))
        if($argv.Count -ne 13 -or $oldRows[0].ExecutablePath -ine $shell -or $argv[0] -ine $shell -or
                $argv[1] -ine '-NoProfile' -or $argv[2] -ine '-NonInteractive' -or
                $argv[3] -ine '-ExecutionPolicy' -or $argv[4] -ine 'Bypass' -or $argv[5] -ine '-File' -or
                $argv[6] -ine $controller -or $argv[7] -ine '-ExpectedSupervisorPid' -or
                $argv[9] -ine '-ExpectedBirthUtc' -or $argv[11] -ine '-Operation' -or $argv[12] -cne 'Copy') {
            throw 'REPAIR_OLD_ARGV_CHANGED'
        }
        foreach($path in @($oldPath,(Join-Path $proofScope 'intent.json'),(Join-Path $proofScope 'done.json'))) {
            $null=Hold-RepairFile $path
        }
        $oldRecord=Read-RepairJson $oldPath;$done=Read-RepairJson (Join-Path $proofScope 'done.json')
        if($oldRecord.pid -isnot [int] -or $oldRecord.pid -ne $oldPid -or $oldRecord.nonce -cne $oldNonce -or
                $oldRecord.phase -cne 'RETAINED' -or $oldRecord.stage -cne 'COPY' -or
                $oldRecord.failure_code -cne 'OFFLINE_AUDIT_WORKER_TIMEOUT' -or $oldRecord.stopped -ne $true -or
                $oldRecord.cleanup_confirmed -ne $false -or $oldRecord.restart_allowed -ne $false -or
                $oldRecord.copy_worker_native_pid -isnot [int]){throw 'REPAIR_COPY_RECORD_INVALID'}
        if($done.pid -isnot [int] -or $done.pid -ne $oldPid -or $done.birth -cne $oldBirth -or
                $done.copy_nonce -cne $oldNonce -or $done.native_cleanup_confirmed -isnot [bool] -or
                $done.native_cleanup_confirmed -ne $true -or $done.copy_complete -isnot [bool] -or
                $done.copy_complete -ne $false -or $done.restart_allowed -isnot [bool] -or
                $done.restart_allowed -ne $false -or $done.maintenance_state -cne 'ACTIVE' -or
                $done.streams_held -isnot [int] -or $done.streams_held -ne 6){throw 'REPAIR_OLD_JOB_PROOF_INVALID'}
        if($done.schema_version -isnot [int] -or $done.schema_version -ne 1 -or
                $done.code.sha256 -cne '6caed580cf47b2fec19ee6b6506d556b4ca8c34609723bd0137090109d619493' -or
                $done.code.type_count -isnot [int] -or $done.code.type_count -ne 11 -or
                $done.code.proof_bytes -isnot [int] -or $done.code.proof_bytes -ne 142231 -or
                @($done.mutexes).Count -ne 3){throw 'REPAIR_OLD_JOB_PROOF_INVALID'}
        foreach($row in $done.mutexes) {
            if($row.Owned -isnot [bool] -or $row.Owned -ne $true -or
                    $row.Abandoned -isnot [bool] -or $row.Abandoned -ne $false -or
                    $row.Count -isnot [int] -or $row.Count -ne 0 -or $row.OwnerProcess -ne $oldPid -or
                    $row.OwnerThread -le 0 -or $row.OwnerThread -ne $row.CallerThread){throw 'REPAIR_OLD_JOB_MUTEX_PROOF_INVALID'}
        }
        $expectedState=Read-RepairJson (Join-Path $Root 'RUNTIME\maintenance-state.json')
        if($expectedState.state -cne 'ACTIVE' -or $expectedState.nonce -cne $oldNonce -or
                $expectedState.owner_pid -ne $oldPid -or $expectedState.owner_birth -cne $oldBirth){throw 'REPAIR_COPY_MARKER_CHANGED'}
        $expectedPath=Join-Path $scope 'expected-state.json'
        Fresh-RepairRecord $expectedPath $expectedState;$null=Hold-RepairFile $expectedPath
        $failedPath=Join-Path $Root 'RUNTIME\core-storage-copy.json';$null=Hold-RepairFile $failedPath
        $failed=Read-RepairJson $failedPath
        if($failed.nonce -cne $oldNonce -or $failed.pid -ne $oldRecord.copy_worker_native_pid -or
                $failed.complete -isnot [bool] -or $failed.complete -ne $false -or
                $failed.cleanup_confirmed -isnot [bool] -or $failed.cleanup_confirmed -ne $false -or
                $failed.code -cne 'COPY_PROOF_FAILED' -or $failed.phase -cne 'FINAL' -or
                $failed.stage -cne 'BUILD' -or $failed.exception_kind -cne 'TimeoutError'){throw 'REPAIR_COPY_FAILURE_CHANGED'}
        $paths=@($database,($database+'-wal'),(Join-Path $oldScope 'raw\imc-main.db'),
            (Join-Path $oldScope 'raw\imc-main.db-wal'),(Join-Path $oldScope 'work\imc-main.db'),
            (Join-Path $Root '.local-tools\sqlite-tools-3530400\sqlite3.exe'),(Join-Path $oldScope 'empty.sql'))
        $dataStreams=@()
        foreach($path in $paths){$dataStreams+=,(Hold-RepairFile $path)}
        foreach($path in $paths[0..4]) {if(Test-Path -LiteralPath ($path+'-journal')){throw 'REPAIR_COPY_JOURNAL_PRESENT'}}
        if(Test-Path -LiteralPath ($paths[4]+'-wal')){throw 'REPAIR_COPY_WORK_WAL_PRESENT'}
        Gate-Off;Fresh-Absence;$record.safety_before=Safety
        $record.phase='HASHING';Report
        for($i=0;$i -lt 4;$i++) {
            $key=if($i%2){'wal'}else{'main'}
            $hash=Hash-RepairStream $dataStreams[$i] $clock
            if($hash -cne $failed.source_sha256.$key){throw 'REPAIR_COPY_HASH_MISMATCH'}
        }
        $record.candidate_sha256=Hash-RepairStream $dataStreams[4] $clock
        $names=@('Global\IMC_TRADING_MAIN_MAINTENANCE_V1','Global\IMC_TRADING_MAIN_OFFLINE_AUDIT_V1','Global\IMC_TRADING_MAIN_RUNTIME_V1')
        foreach($name in $names) {
            $m=New-RepairMutex $name;$script:mutexes+=$m
            $native=Read-RepairMutex $m
            if($native.OwnerProcess -ne $oldPid -or $native.OwnerThread -ne $done.mutexes[0].OwnerThread -or
                    $native.Owned -or $native.Abandoned -or $native.Count -ne 0){throw 'REPAIR_COPY_OLD_MUTEX_OWNER_CHANGED'}
            $free=$false
            try{$free=$m.WaitOne(0)}catch [Threading.AbandonedMutexException]{$free=$true}
            if($free){$script:ownedMutexes+=$m;throw 'REPAIR_COPY_OLD_MUTEX_NOT_HELD'}
        }
        Gate-Off;Fresh-Absence;$record.safety_handover=Safety
        if($old.HasExited){throw 'REPAIR_OLD_LIFETIME_ENDED'}
        Fresh-RepairRecord (Join-Path $scope 'handover-intent.json') @{
            schema_version=1;old_pid=$oldPid;old_birth=$oldBirth;old_nonce=$oldNonce;
            expected_state=$expectedState;old_job_cleanup=$true;source_raw_hashes=$failed.source_sha256;
            candidate_sha256=$record.candidate_sha256;restart_allowed=$false}
        $record.phase='HANDOVER';Report
        $old.Kill();if(-not $old.WaitForExit(10000)){throw 'REPAIR_OLD_EXIT_UNCONFIRMED'}
        # Use the already referenced mutex objects so their abandoned generation
        # survives until ownership is proved. Never release them on uncertainty.
        foreach($m in $mutexes) {
            $owned=$false
            try{$owned=$m.WaitOne(0)}catch [Threading.AbandonedMutexException]{$owned=$true}
            if(-not $owned){throw 'REPAIR_COPY_MUTEX_HANDOVER_UNCONFIRMED'}
            $script:ownedMutexes+=$m
            $native=Read-RepairMutex $m
            if(-not $native.Owned -or $native.Abandoned -or $native.Count -ne 0 -or
                    $native.OwnerProcess -ne $PID -or $native.OwnerThread -ne $native.CallerThread) {
                throw 'REPAIR_COPY_NEW_MUTEX_OWNER_UNCONFIRMED'
            }
        }
        $script:lease=@{Mutex=$mutexes[0];Owned=$true;Purpose='Maintenance';Abandoned=$true;ActiveNonce=$oldNonce}
        $birth=$self.StartTime.ToUniversalTime().ToString('o')
        $handoverArgs=@('-I',(Join-Path $Root 'scripts\maintenance_state.py'),'handover',
            '--path',(Join-Path $Root 'RUNTIME\maintenance-state.json'),'--expected-state',$expectedPath,
            '--nonce',$nonce,'--owner-pid',([string]$PID),'--owner-birth',$birth)
        $rows=@(& $Python @handoverArgs 2>$null)
        if($LASTEXITCODE -ne 0 -or $rows.Count -ne 1){throw 'REPAIR_COPY_GENERATION_HANDOVER_UNCONFIRMED'}
        $lease.ActiveNonce=$nonce
        $actual=Read-RepairJson (Join-Path $Root 'RUNTIME\maintenance-state.json')
        if($actual.state -cne 'ACTIVE' -or $actual.nonce -cne $nonce -or $actual.owner_pid -ne $PID -or
                $actual.owner_birth -cne $birth){throw 'REPAIR_COPY_GENERATION_HANDOVER_UNCONFIRMED'}
        Fresh-RepairRecord (Join-Path $scope 'handover-done.json') @{
            schema_version=1;old_native_exit=$true;owner_pid=$PID;owner_birth=$birth;
            nonce=$nonce;state='ACTIVE';guards_held=$streams.Count;restart_allowed=$false}
        Fresh-Absence;Gate-Off;$record.safety_after_handover=Safety
        $record.phase='VERIFYING';Report
        $remaining=21600-$clock.Elapsed.TotalSeconds
        if($remaining -le 5){throw 'REPAIR_SHARED_BUDGET_EXPIRED'}
        $verificationArgs=@{OldScope=$oldScope;OldNonce=$oldNonce;FailedReportPath=$failedPath;
            OldProducer=$oldRecord.copy_worker_native_pid;BudgetSeconds=$remaining}
        $result=Run-RetainedCopyVerification @verificationArgs
        Fresh-Absence;Gate-Off;$record.safety_after_verification=Safety
        if(-not $job.CleanupConfirmed -or $result.complete -isnot [bool] -or $result.complete -ne $true){throw 'REPAIR_COPY_VERIFICATION_UNCONFIRMED'}
        $record.phase='VERIFIED_HELD';$record.old_native_exit=$true;$record.native_job_cleanup=$true;Report
        Write-Output ('RETAINED_COPY_VERIFIED_HELD='+$recordPath)
        # Publication/restart requires separate review and continued ownership.
        while($true){try{Start-Sleep -Seconds 5}catch{[Threading.Thread]::Sleep(5000)}}
    } catch {
        $record.phase='RETAINED';$record.cleanup_confirmed=$false;$record.restart_allowed=$false
        $record.failure_code='REPAIR_COPY_UNCONFIRMED';try{Report}catch{}
        Write-Output ('RETAINED_COPY_REPAIR_HELD='+$recordPath)
        while($true){try{Start-Sleep -Seconds 5}catch{[Threading.Thread]::Sleep(5000)}}
    }
}
if($Mode -ceq 'CopyTimeout'){Repair-CopyTimeout;exit}
try {
    $lease=Enter-IMCMaintenanceFence -Python $Python -Purpose Maintenance
    $shell=Join-Path ([Environment]::GetFolderPath('System')) 'WindowsPowerShell\v1.0\powershell.exe'
    $raw=@(Get-CimInstance Win32_Process -ErrorAction Stop)
    $selfRow=@($raw|Where-Object ProcessId -eq $PID)
    if($selfRow.Count -ne 1){throw 'REPAIR_SELF_UNKNOWN'}
    $self=Hold $selfRow[0]
    $argv=@([IMCCommandLine]::Split($selfRow[0].CommandLine))
    $expected=@($shell,'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',$PSCommandPath)
    if($argv.Count -ne 7 -or $selfRow[0].ExecutablePath -ine $shell){throw 'REPAIR_SELF_UNKNOWN'}
    for($i=0;$i -lt 7;$i++){if($argv[$i] -ine $expected[$i]){throw 'REPAIR_SELF_UNKNOWN'}}
    $oldRow=@($raw|Where-Object ProcessId -eq $oldPid)
    if($oldRow.Count -ne 1){throw 'REPAIR_OLD_CONTROLLER_ABSENT'}
    $old=Hold $oldRow[0]
    if($old.StartTime.ToUniversalTime().ToString('o') -cne $oldBirth -or $oldRow[0].ExecutablePath -ine $shell){throw 'REPAIR_OLD_BIRTH_CHANGED'}
    $argv=@([IMCCommandLine]::Split($oldRow[0].CommandLine))
    $expected=@($shell,'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',$controller,
        '-ExpectedSupervisorPid','15356','-ExpectedBirthUtc','2026-10-02T23:24:41.3771672Z')
    if($argv.Count -ne 11){throw 'REPAIR_OLD_ARGV_CHANGED'}
    for($i=0;$i -lt 11;$i++){if($argv[$i] -ine $expected[$i]){throw 'REPAIR_OLD_ARGV_CHANGED'}}
    $recordGuard=[IO.File]::Open($oldPath,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    $oldRecord=Get-Content -Raw -Encoding UTF8 $oldPath|ConvertFrom-Json
    if($oldRecord.pid -ne $oldPid -or $oldRecord.nonce -cne $oldNonce -or $oldRecord.phase -cne 'RETAINED' -or
        $oldRecord.stage -cne 'POST_STOP_SAFETY' -or $oldRecord.stopped -ne $true -or
        $oldRecord.restart_allowed -ne $false -or $oldRecord.cleanup_confirmed -ne $false -or
        $oldRecord.audit_started_at -or $oldRecord.native_job_active_before_shutdown -or
        $oldRecord.absence_before.runtime_roles -ne 0 -or $oldRecord.absence_before.unknown_interpreters -ne 0) {
        throw 'REPAIR_OLD_RESOURCE_SCOPE_UNKNOWN'
    }
    foreach($s in @($oldRecord.safety_before,$oldRecord.safety_immediately_before_stop)) {
        if($s.gate -cne 'OFF' -or $s.schema_version -isnot [int] -or $s.schema_version -ne 2 -or
            $s.positions -isnot [int] -or $s.positions -ne 0 -or $s.uncertain -isnot [int] -or $s.uncertain -ne 0 -or
            $s.receipts -isnot [int] -or $s.receipts -ne 0){throw 'REPAIR_BASELINE_UNPROVEN'}
    }
    Fresh-Absence;Gate-Off
    foreach($name in @('Global\IMC_TRADING_MAIN_RUNTIME_V1','Global\IMC_TRADING_MAIN_OFFLINE_AUDIT_V1')) {
        $m=New-RepairMutex $name;$mutexes+=$m
        $owned=$false;try{$owned=$m.WaitOne(0)}catch [Threading.AbandonedMutexException]{$owned=$true}
        if(-not $owned){throw 'REPAIR_GLOBAL_OWNER_PRESENT'};$ownedMutexes+=$m
    }
    $legacy=@()
    foreach($name in @('Local\IMC_SIGNAL_RUNTIME_V1','Local\IMC_OFFLINE_AUDIT_V1')) {
        $m=New-RepairMutex $name;$mutexes+=$m;$legacy+=$m
        $acquired=$false;try{$acquired=$m.WaitOne(0)}catch [Threading.AbandonedMutexException]{$acquired=$true}
        if($acquired){$ownedMutexes+=$m;throw 'REPAIR_LEGACY_OWNER_NOT_PRESENT'}
    }
    Set-IMCMaintenanceActive $lease $Python $nonce 'RECOVERY_REQUIRED'
    $record.phase='RECOVERING';$record.old_pid=$oldPid;$record.old_birth=$oldBirth;Report
    Fresh-Absence;Gate-Off
    Recover-QuiescedLedger
    $record.safety_after_recovery=Safety
    Fresh-Absence;Gate-Off
    if($old.HasExited){throw 'REPAIR_OLD_LIFETIME_ENDED'}
    $record.phase='HANDOVER';Report
    $old.Kill()
    if(-not $old.WaitForExit(10000)){throw 'REPAIR_OLD_EXIT_UNCONFIRMED'}
    foreach($m in $legacy) {
        $acquired=$false;try{$acquired=$m.WaitOne(0)}catch [Threading.AbandonedMutexException]{$acquired=$true}
        if(-not $acquired){throw 'REPAIR_LEGACY_HANDOVER_UNCONFIRMED'};$ownedMutexes+=$m
    }
    Fresh-Absence;Gate-Off;$record.safety_final=Safety
    if($job -and -not $job.CleanupConfirmed){throw 'REPAIR_JOB_UNCONFIRMED'}
    Close-IMCAuditFile $recordGuard;$recordGuard=$null
    foreach($h in $handles){$h.Dispose()};$handles=@()
    foreach($m in $ownedMutexes){$m.ReleaseMutex()};$ownedMutexes=@()
    foreach($m in $mutexes){$m.Dispose()};$mutexes=@()
    Complete-IMCMaintenance $lease $Python $true $true $true
    Exit-IMCMaintenanceFence $lease;$lease=$null
    $finished=$true;$record.phase='FINAL';$record.cleanup_confirmed=$true;$record.restart_allowed=$true
    try{Report}catch{}
    Write-Output ('RETAINED_REPAIR_FINAL='+$recordPath)
} catch {
    if($finished){exit 0}
    $record.phase='RETAINED';$record.restart_allowed=$false;$record.cleanup_confirmed=$false
    $record.failure_code='REPAIR_UNCONFIRMED';try{Report}catch{}
    Write-Output ('RETAINED_REPAIR_HELD='+$recordPath)
    # Never auto-release protection following unknown recovery or handover.
    while($true){try{Start-Sleep -Seconds 5}catch{[Threading.Thread]::Sleep(5000)}}
}
