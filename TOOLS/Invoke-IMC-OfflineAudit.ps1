param([Parameter(Mandatory)][ValidateRange(1,2147483647)][int]$ExpectedSupervisorPid,
      [Parameter(Mandatory)][string]$ExpectedBirthUtc,
      [ValidateSet('Audit','Copy')][string]$Operation='Audit')
$ErrorActionPreference='Stop'
$Root=Split-Path -Parent $PSScriptRoot
. (Join-Path $Root 'LAUNCHERS\IMC_PROCESS_COMMON.ps1')
. (Join-Path $Root 'LAUNCHERS\IMC_PYTHON_COMMON.ps1')
. (Join-Path $Root 'TOOLS\IMC_RUNTIME_JOB.ps1')
. (Join-Path $Root 'TOOLS\IMC_MAINTENANCE.ps1')
$Python=Get-IMCPython
$database=Join-Path $Root 'CORE\data\imc-main.db'
$cli=Join-Path $Root '.local-tools\sqlite-tools-3530400\sqlite3.exe'
$reportPath=Join-Path $Root 'RUNTIME\core-storage-quiesced.json'
$auditBudget=900 # cooperative worker budget; cleanup/retained protection are separate
if($Operation -ceq 'Copy'){$auditBudget=7200;$reportPath=Join-Path $Root 'RUNTIME\core-storage-copy.json'}
$nonce=[guid]::NewGuid().ToString('N')
$scope=Join-Path (Join-Path $Root '.local-tools') ('offline-audit-'+$nonce)
$null=New-Item -ItemType Directory -Path $scope
$init=Join-Path $scope 'empty.sql'
[IO.File]::WriteAllBytes($init,[byte[]]@())
$recordPath=Join-Path $scope 'controller.json'
$record=@{schema_version=1;nonce=$nonce;operation=$Operation;phase='PREPARE';stage='LOCK';pid=$PID;cleanup_confirmed=$false;stopped=$false;restart_allowed=$false}
$handles=@();$streams=@();$job=$null;$auditOwned=$false;$runtimeOwned=$false;$released=$false;$maintenanceLease=$null
$auditMutex=[Threading.Mutex]::new($false,'Global\IMC_TRADING_MAIN_OFFLINE_AUDIT_V1')
$runtimeMutex=[Threading.Mutex]::new($false,'Global\IMC_TRADING_MAIN_RUNTIME_V1')
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class IMCAuditNativeClose {
    [DllImport("kernel32.dll",SetLastError=true)] public static extern bool CloseHandle(IntPtr handle);
}
'@
function Save-Record {
    $record.updated_at=[DateTimeOffset]::UtcNow.ToString('o')
    [IO.File]::WriteAllText($recordPath,($record | ConvertTo-Json -Depth 8),[Text.UTF8Encoding]::new($false))
}
function Close-IMCAuditFile($Stream) {
    # Pin before checked native close. Retain the pin and protection on
    # failure; invalidation/disposal occur only after a successful close.
    $safe=$Stream.SafeFileHandle;$added=$false
    $safe.DangerousAddRef([ref]$added)
    if (-not $added -or -not [IMCAuditNativeClose]::CloseHandle($safe.DangerousGetHandle())) {
        throw 'OFFLINE_PARENT_FILE_CLOSE_UNCONFIRMED'
    }
    $safe.SetHandleAsInvalid();$safe.DangerousRelease();$Stream.Dispose()
}
function Safe-FailureCode([string]$Message) {
    $codes=@('OFFLINE_OTHER_AUDIT_PRESENT','OFFLINE_NATIVE_IDENTITY_MISSING','OFFLINE_NATIVE_IDENTITY_CHANGED',
        'OFFLINE_SELF_IDENTITY_UNKNOWN','OFFLINE_SAFETY_UNPROVEN','OFFLINE_SAFETY_SCHEMA_INVALID',
        'OFFLINE_SUPERVISOR_NOT_OWNED','OFFLINE_RUNTIME_STATUS_INVALID','OFFLINE_SUPERVISOR_CHANGED',
        'OFFLINE_EXPECTED_BIRTH_MISMATCH','OFFLINE_ANCESTRY_UNKNOWN','OFFLINE_DESCENDANT_LIMIT',
        'OFFLINE_FOREIGN_RUNTIME_PRESENT','OFFLINE_LIFETIME_CHANGED','OFFLINE_STOP_UNCONFIRMED',
        'OFFLINE_REPLACEMENT_MUTEX_PRESENT','OFFLINE_RUNTIME_NOT_ABSENT','OFFLINE_SQLITE_IDENTITY_UNKNOWN',
        'OFFLINE_SQLITE_PROCESS_PRESENT','OFFLINE_AUDIT_WORKER_TIMEOUT','OFFLINE_AUDIT_WORKER_FAILED',
        'OFFLINE_AUDIT_JOB_UNCONFIRMED','OFFLINE_WORKER_LIFETIME_UNOBSERVED','OFFLINE_FINAL_REPORT_INVALID',
        'OFFLINE_PARENT_FILE_CLOSE_UNCONFIRMED','OFFLINE_RESOURCES_NOT_RELEASED',
        'OFFLINE_LEDGER_RECOVERY_UNCONFIRMED','OFFLINE_LEDGER_INTEGRITY_UNPROVEN')
    foreach($code in $codes) {if([string]::Equals($Message,$code,[StringComparison]::Ordinal)) {return $code}}
    return 'OFFLINE_INTERNAL_FAILURE'
}
function Add-FailureDiagnostics($Failure) {
    $record.exception_kind='UNKNOWN';$record.failure_location=$null
    try {
        $kind=$Failure.Exception.GetType().FullName
        foreach($allowed in @('System.Management.Automation.RuntimeException',
            'System.Management.Automation.MethodInvocationException','System.Management.Automation.PSArgumentException',
            'System.Management.Automation.PSInvalidOperationException','System.Management.Automation.ActionPreferenceStopException',
            'System.Management.Automation.PSInvalidCastException','System.ComponentModel.Win32Exception',
            'System.InvalidOperationException','System.IO.IOException','System.UnauthorizedAccessException')) {
            if([string]::Equals($kind,$allowed,[StringComparison]::Ordinal)) {$record.exception_kind=$allowed;break}
        }
        $info=$Failure.InvocationInfo
        if($info -and $info.ScriptLineNumber -is [int] -and $info.ScriptLineNumber -ge 1 -and $info.ScriptLineNumber -le 5000) {
            foreach($location in @(
                @{id='OFFLINE_CONTROLLER';path=(Join-Path $Root 'TOOLS\Invoke-IMC-OfflineAudit.ps1')},
                @{id='PROCESS_COMMON';path=(Join-Path $Root 'LAUNCHERS\IMC_PROCESS_COMMON.ps1')},
                @{id='RUNTIME_JOB';path=(Join-Path $Root 'TOOLS\IMC_RUNTIME_JOB.ps1')})) {
                if([string]::Equals($info.ScriptName,$location.path,[StringComparison]::Ordinal)) {
                    $record.failure_location=@{file=$location.id;line=$info.ScriptLineNumber};break
                }
            }
        }
    } catch {} # diagnostics cannot interfere with protection or cleanup
}
function Safety {
    param([string]$Ledger=(Join-Path $Root 'RUNTIME\EXECUTOR_GATEWAY\GMGN_BRIDGE\execution-ledger.db'),
          [string]$Gate=(Join-Path $Root 'config\owner-broadcast-gate.json'))
    $text=@(& $Python -I (Join-Path $Root 'scripts\offline_ledger.py') verify --ledger $Ledger --gate $Gate 2>$null)
    if ($LASTEXITCODE -ne 0 -or $text.Count -ne 1) { throw 'OFFLINE_SAFETY_UNPROVEN' }
    $value=$text[0] | ConvertFrom-Json
    if ($value.gate -cne 'OFF' -or $value.schema_version -isnot [int] -or $value.schema_version -ne 2 -or
        $value.positions -isnot [int] -or $value.positions -ne 0 -or $value.uncertain -isnot [int] -or
        $value.uncertain -ne 0 -or $value.receipts -isnot [int] -or $value.receipts -ne 0 -or
        @($value.PSObject.Properties).Count -ne 5) {throw 'OFFLINE_SAFETY_SCHEMA_INVALID'}
    return $value
}
function Hold($row) {
    if (-not $row.CreationDate -or -not $row.ExecutablePath) { throw 'OFFLINE_NATIVE_IDENTITY_MISSING' }
    $handle=Get-Process -Id $row.ProcessId -ErrorAction Stop
    $script:handles += $handle
    $null=$handle.Handle
    if ($handle.HasExited -or $handle.MainModule.FileName -ine $row.ExecutablePath -or
        [math]::Abs(($handle.StartTime.ToUniversalTime()-$row.CreationDate.ToUniversalTime()).Ticks) -ge 10) {
        throw 'OFFLINE_NATIVE_IDENTITY_CHANGED'
    }
    return $handle
}
function Validate-Final([int]$ExitCode) {
    $code=@'
import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(sys.argv[1])/'scripts'))
from db_offline_probe import read_final,validate_final,state
database=Path(sys.argv[3])
result=validate_final(read_final(sys.argv[2]),source=database,nonce=sys.argv[4],exit_code=int(sys.argv[5]),current_files=state(database),expected_seconds=int(sys.argv[6]),expected_allocation_mode='objects')
print(json.dumps(result))
'@
    $text=@(& $Python -I -c $code $Root $reportPath $database $nonce ([string]$ExitCode) ([string]$auditBudget) 2>$null)
    if ($LASTEXITCODE -ne 0 -or $text.Count -ne 1) {throw 'OFFLINE_FINAL_REPORT_INVALID'}
    return $text[0] | ConvertFrom-Json
}
function Recover-QuiescedLedger {
    param([string]$Ledger=(Join-Path $Root 'RUNTIME\EXECUTOR_GATEWAY\GMGN_BRIDGE\execution-ledger.db'),
          [string]$Gate=(Join-Path $Root 'config\owner-broadcast-gate.json'))
    $recoveryPath=Join-Path $scope 'ledger-recovery.json'
    $ackPath=Join-Path $scope 'ledger-worker-seen.json'
    $script:job=[IMCRuntimeJob]::new()
    $worker=$job.Start($Python,[string[]]@('-I',(Join-Path $Root 'scripts\offline_ledger.py'),'recover',
        '--ledger',$ledger,'--gate',$gate,'--backup',(Join-Path $scope 'ledger-backup'),
        '--output',$recoveryPath,'--nonce',$nonce,'--ack',$ackPath),$Root)
    $clock=[Diagnostics.Stopwatch]::StartNew();$observed=$false;$observedPid=0
    while($job.Running($worker) -and $clock.ElapsedMilliseconds -lt 37000) {
        if(-not $observed -and [IO.File]::Exists($recoveryPath)) {
            $value=Get-Content -Raw -Encoding UTF8 $recoveryPath|ConvertFrom-Json
            if($value.phase -ceq 'RUNNING' -and $value.nonce -ceq $nonce -and $value.pid -is [int] -and $value.pid -gt 0) {
                $job.ObserveMember($value.pid);$observed=$true;$observedPid=$value.pid
                $record.ledger_worker_native_pid=$observedPid
                [IO.File]::WriteAllText($ackPath,(@{nonce=$nonce;pid=$value.pid}|ConvertTo-Json -Compress),[Text.UTF8Encoding]::new($false))
            }
        }
        Start-Sleep -Milliseconds 10
    }
    if($job.Running($worker)){throw 'OFFLINE_LEDGER_RECOVERY_UNCONFIRMED'}
    $exitCode=$job.ExitCode($worker)
    $job.ShutdownWithin(10000)
    if(-not $job.CleanupConfirmed -or -not $observed -or $exitCode -ne 0){throw 'OFFLINE_LEDGER_RECOVERY_UNCONFIRMED'}
    $code=@'
import sys,json,time
from pathlib import Path
sys.path.insert(0,str(Path(sys.argv[1])/'scripts'))
from offline_ledger import checked_database,decode
with Path(sys.argv[2]).open('rb') as stream:body=stream.read(65537)
if len(body)>65536:raise ValueError('recovery_report_size')
report=decode(body.decode('utf-8'))
if (type(report['schema_version'])is not int or report['schema_version']!=1 or report['nonce']!=sys.argv[3]
 or type(report['pid'])is not int or report['pid']!=int(sys.argv[6])
 or report['phase']!='FINAL' or report['complete']is not True or report['cleanup_confirmed']is not True
 or type(report['recovered'])is not bool):raise ValueError('recovery_report_invalid')
actual=checked_database(sys.argv[4],sys.argv[5],time.monotonic()+5)
for k,v in actual.items():
 if json.dumps(report.get(k),sort_keys=True,separators=(',',':'),allow_nan=False)!=json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False):raise ValueError('recovery_final_mismatch')
print(json.dumps({'recovered':report['recovered'],'integrity':True,'logical_sha256':actual['logical_sha256']}))
'@
    $rows=@(& $Python -I -c $code $Root $recoveryPath $nonce $ledger $gate ([string]$observedPid) 2>$null)
    if($LASTEXITCODE -ne 0 -or $rows.Count -ne 1){throw 'OFFLINE_LEDGER_RECOVERY_UNCONFIRMED'}
    $result=$rows[0]|ConvertFrom-Json
    if($result.integrity -ne $true -or $result.recovered -isnot [bool] -or $result.logical_sha256 -cnotmatch '\A[a-f0-9]{64}\z') {
        throw 'OFFLINE_LEDGER_RECOVERY_UNCONFIRMED'
    }
    $record.ledger_recovery=$result
    $script:job=$null
}
function Read-CopyProgress([string]$Path) {
    if(-not [IO.File]::Exists($Path)){return $null}
    # Pin one complete report generation while allowing atomic replacement.
    $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,
        ([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete))
    try {
        if($stream.Length -gt 131072){throw 'OFFLINE_FINAL_REPORT_INVALID'}
        $reader=[IO.StreamReader]::new($stream,[Text.UTF8Encoding]::new($false,$true))
        try{$text=$reader.ReadToEnd()}finally{$reader.Dispose()}
    } finally {$stream.Dispose()}
    return $text|ConvertFrom-Json
}
function Write-CopyAck([string]$Path,[int]$ProducerPid) {
    $bytes=[Text.UTF8Encoding]::new($false).GetBytes((@{nonce=$nonce;pid=$ProducerPid}|ConvertTo-Json -Compress))
    $temporary=$Path+'.tmp'
    $stream=[IO.File]::Open($temporary,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try{$stream.Write($bytes,0,$bytes.Length);$stream.Flush($true)}finally{$stream.Dispose()}
    [IO.File]::Move($temporary,$Path)
}
function Protect-CopyMember([string]$Path) {
    $script:streams += [IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
}
function Validate-CopyCompletion($Value,[int]$ProducerPid,[int]$ExitCode,[bool]$NativeCleanup,
        [double]$ElapsedSeconds,[double]$BudgetSeconds) {
    $fields=@('schema_version','nonce','pid','phase','complete','cleanup_confirmed','result')
    $resultFields=@('complete','independent_comparison','logical_tables','source_unchanged','raw_verified',
        'target_verified','eligible_for_publication','semantic_orphans')
    if(-not $NativeCleanup -or $ProducerPid -le 0 -or $ExitCode -ne 0 -or $ElapsedSeconds -gt $BudgetSeconds -or
        -not $Value -or @(Compare-Object -CaseSensitive $fields @($Value.PSObject.Properties.Name)).Count -or
        $Value.schema_version -isnot [int] -or $Value.schema_version -ne 1 -or
        $Value.pid -isnot [int] -or $Value.pid -ne $ProducerPid -or $Value.nonce -isnot [string] -or $Value.nonce -cne $nonce -or
        $Value.phase -isnot [string] -or $Value.phase -cne 'FINAL' -or
        $Value.complete -isnot [bool] -or $Value.complete -ne $true -or
        $Value.cleanup_confirmed -isnot [bool] -or $Value.cleanup_confirmed -ne $true -or -not $Value.result -or
        @(Compare-Object -CaseSensitive $resultFields @($Value.result.PSObject.Properties.Name)).Count) {
        throw 'OFFLINE_FINAL_REPORT_INVALID'
    }
    foreach($field in @('complete','independent_comparison','source_unchanged','raw_verified','target_verified')) {
        if($Value.result.$field -isnot [bool] -or $Value.result.$field -ne $true){throw 'OFFLINE_FINAL_REPORT_INVALID'}
    }
    if($Value.result.eligible_for_publication -isnot [bool] -or $Value.result.eligible_for_publication -ne $false -or
        $Value.result.logical_tables -isnot [int] -or $Value.result.logical_tables -ne 11 -or
        ($Value.result.semantic_orphans -isnot [int] -and $Value.result.semantic_orphans -isnot [long]) -or
        $Value.result.semantic_orphans -lt 0) {throw 'OFFLINE_FINAL_REPORT_INVALID'}
    return $Value.result
}
function Run-Copy {
    param([string]$Ledger=(Join-Path $Root 'RUNTIME\EXECUTOR_GATEWAY\GMGN_BRIDGE\execution-ledger.db'),
          [string]$Gate=(Join-Path $Root 'config\owner-broadcast-gate.json'))
    $ackPath=Join-Path $scope 'copy-worker-seen.json'
    $rawAck=Join-Path $scope 'copy-raw-held.json';$endAck=Join-Path $scope 'copy-work-held.json'
    $script:job=[IMCRuntimeJob]::new()
    $worker=$job.Start($Python,[string[]]@('-I',(Join-Path $Root 'scripts\storage_copy_worker.py'),
        '--database',$database,'--scope',$scope,'--ledger',$Ledger,'--gate',$Gate,'--output',$reportPath,
        '--nonce',$nonce,'--ack',$ackPath,'--raw-ack',$rawAck,'--end-ack',$endAck,'--seconds',([string]$auditBudget)),$Root)
    $record.copy_primary_pid=$worker
    $clock=[Diagnostics.Stopwatch]::StartNew();$producer=0;$rawHeld=$false;$workHeld=$false
    while($job.Running($worker) -and $clock.ElapsedMilliseconds -lt ($auditBudget*1000)) {
        $value=Read-CopyProgress $reportPath
        if($value -and $value.nonce -ceq $nonce -and $value.phase -ceq 'RUNNING' -and $value.pid -is [int] -and $value.pid -gt 0) {
            if(-not $producer) {
                if($value.stage -cne 'AWAIT_NATIVE_ACK'){throw 'OFFLINE_WORKER_LIFETIME_UNOBSERVED'}
                $job.ObserveMember($value.pid);$producer=$value.pid;$record.copy_worker_native_pid=$producer
                Write-CopyAck $ackPath $producer
            } elseif($value.pid -ne $producer){throw 'OFFLINE_WORKER_LIFETIME_UNOBSERVED'}
            if($value.stage -ceq 'RAW_READY' -and -not $rawHeld) {
                $rawDb=Join-Path (Join-Path $scope 'raw') ([IO.Path]::GetFileName($database))
                Protect-CopyMember $rawDb;Protect-CopyMember ($rawDb+'-wal')
                $rawHeld=$true;$record.raw_parent_guards=$true
                Write-CopyAck $rawAck $producer
            }
            if($value.stage -ceq 'WORK_READY' -and -not $workHeld) {
                if(-not $rawHeld){throw 'OFFLINE_FINAL_REPORT_INVALID'}
                Protect-CopyMember (Join-Path (Join-Path $scope 'work') ([IO.Path]::GetFileName($database)))
                $workHeld=$true;$record.work_parent_guards=$true
                Write-CopyAck $endAck $producer
            }
        }
        Start-Sleep -Milliseconds 20
    }
    if($job.Running($worker)){throw 'OFFLINE_AUDIT_WORKER_TIMEOUT'}
    $exitCode=$job.ExitCode($worker)
    $record.copy_primary_exit=$exitCode;$record.copy_active_before_shutdown=$job.ActiveProcessCount()
    $record.copy_raw_handoff=$rawHeld;$record.copy_work_handoff=$workHeld
    $job.ShutdownWithin(10000)
    if(-not $job.CleanupConfirmed -or -not $producer -or -not $rawHeld -or -not $workHeld -or $exitCode -ne 0) {
        throw 'OFFLINE_AUDIT_JOB_UNCONFIRMED'
    }
    # Independent comparison is another observed, owned read-only producer.
    $validationPath=Join-Path $scope 'copy-validation.json';$validationAck=Join-Path $scope 'copy-validator-seen.json'
    $remaining=$auditBudget-$clock.Elapsed.TotalSeconds
    if($remaining -le 5){throw 'OFFLINE_AUDIT_WORKER_TIMEOUT'}
    $script:job=[IMCRuntimeJob]::new()
    $validator=$job.Start($Python,[string[]]@('-I',(Join-Path $Root 'scripts\storage_copy_contract.py'),
        '--report',$reportPath,'--source',$database,'--scope',$scope,'--nonce',$nonce,
        '--worker-pid',([string]$producer),'--exit-code',([string]$exitCode),
        '--seconds',$remaining.ToString('R',[Globalization.CultureInfo]::InvariantCulture),
        '--output',$validationPath,'--ack',$validationAck),$Root)
    $validationProducer=0
    while($job.Running($validator) -and $clock.ElapsedMilliseconds -lt ($auditBudget*1000)) {
        $value=Read-CopyProgress $validationPath
        if(-not $validationProducer -and $value -and $value.nonce -ceq $nonce -and $value.phase -ceq 'RUNNING' -and
            $value.pid -is [int] -and $value.pid -gt 0) {
            $job.ObserveMember($value.pid);$validationProducer=$value.pid;$record.copy_validator_native_pid=$validationProducer
            Write-CopyAck $validationAck $validationProducer
        }
        Start-Sleep -Milliseconds 20
    }
    if($job.Running($validator)){throw 'OFFLINE_AUDIT_WORKER_TIMEOUT'}
    $validationExit=$job.ExitCode($validator);$job.ShutdownWithin(10000)
    $value=Read-CopyProgress $validationPath
    $result=Validate-CopyCompletion $value $validationProducer $validationExit $job.CleanupConfirmed $clock.Elapsed.TotalSeconds $auditBudget
    $record.copy_result=$result
    return $result
}
function Observe-RetainedCopyMember($Job,[int]$NativePid) {
    $Job.ObserveMember($NativePid)
}
function Run-RetainedCopyVerification {
    param([Parameter(Mandatory)][string]$OldScope,[Parameter(Mandatory)][string]$OldNonce,
          [Parameter(Mandatory)][string]$FailedReportPath,[Parameter(Mandatory)][int]$OldProducer,
          [ValidateRange(5,21600)][double]$BudgetSeconds,
          [string]$AuthorityRoot=$Root,
          [string]$Ledger=(Join-Path $Root 'RUNTIME\EXECUTOR_GATEWAY\GMGN_BRIDGE\execution-ledger.db'),
          [string]$Gate=(Join-Path $Root 'config\owner-broadcast-gate.json'))
    # Called only by the guarded retained-repair operator after native handover.
    # Parent already holds Original/RAW/WORK and never releases them here.
    $clock=[Diagnostics.Stopwatch]::StartNew()
    $proofPath=Join-Path $scope 'copy-proof.json';$ackPath=Join-Path $scope 'copy-proof-seen.json'
    $cleanupAttempted=$false
    try {
    $script:job=[IMCRuntimeJob]::new()
    $primary=$job.Start($Python,[string[]]@('-I',(Join-Path $Root 'scripts\storage_copy_contract.py'),
        '--operation','prove-retained','--root',$AuthorityRoot,'--report',$FailedReportPath,
        '--source',$database,'--scope',$OldScope,'--old-nonce',$OldNonce,'--nonce',$nonce,
        '--worker-pid',([string]$OldProducer),'--exit-code','2','--ledger',$Ledger,'--gate',$Gate,
        '--seconds',$BudgetSeconds.ToString('R',[Globalization.CultureInfo]::InvariantCulture),
        '--output',$proofPath,'--ack',$ackPath),$Root)
    $producer=0
    while($job.Running($primary) -and $clock.Elapsed.TotalSeconds -lt $BudgetSeconds) {
        $value=Read-CopyProgress $proofPath
        if($value -and $value.nonce -ceq $nonce -and $value.phase -ceq 'RUNNING' -and
                $value.pid -is [int] -and $value.pid -gt 0) {
            if(-not $producer) {
                if($value.stage -cne 'AWAIT_NATIVE_ACK'){throw 'OFFLINE_WORKER_LIFETIME_UNOBSERVED'}
                Observe-RetainedCopyMember $job $value.pid;$producer=$value.pid
                $record.retained_proof_native_pid=$producer
                Write-CopyAck $ackPath $producer
            } elseif($value.pid -ne $producer){throw 'OFFLINE_WORKER_LIFETIME_UNOBSERVED'}
        }
        Start-Sleep -Milliseconds 50
    }
    $running=$job.Running($primary);$exitCode=$null
    if(-not $running){$exitCode=$job.ExitCode($primary)}
    $cleanupAttempted=$true;$job.ShutdownWithin(10000)
    if($running -or -not $job.CleanupConfirmed -or -not $producer -or $exitCode -ne 0) {
        throw 'OFFLINE_AUDIT_JOB_UNCONFIRMED'
    }
    $proof=Read-CopyProgress $proofPath
    if(-not $proof -or $proof.kind -cne 'RETAINED_READONLY_PROOF' -or
            $proof.old_copy_nonce -cne $OldNonce -or $proof.nonce -cne $nonce -or
            $proof.pid -isnot [int] -or $proof.pid -ne $producer -or $proof.phase -cne 'FINAL' -or
            $proof.stage -cne 'FINAL' -or $proof.complete -isnot [bool] -or $proof.complete -ne $true -or
            $proof.cleanup_confirmed -isnot [bool] -or $proof.cleanup_confirmed -ne $true) {
        throw 'OFFLINE_FINAL_REPORT_INVALID'
    }
    $record.retained_proof_native_cleanup=$true
    $remaining=$BudgetSeconds-$clock.Elapsed.TotalSeconds
    if($remaining -le 5){throw 'OFFLINE_AUDIT_WORKER_TIMEOUT'}
    $validationPath=Join-Path $scope 'copy-validation.json'
    $validationAck=Join-Path $scope 'copy-validator-seen.json'
    $script:job=[IMCRuntimeJob]::new()
    $cleanupAttempted=$false
    $validator=$job.Start($Python,[string[]]@('-I',(Join-Path $Root 'scripts\storage_copy_contract.py'),
        '--report',$proofPath,'--source',$database,'--scope',$OldScope,'--nonce',$nonce,
        '--worker-pid',([string]$producer),'--exit-code','0',
        '--seconds',$remaining.ToString('R',[Globalization.CultureInfo]::InvariantCulture),
        '--output',$validationPath,'--ack',$validationAck),$Root)
    $validationProducer=0
    while($job.Running($validator) -and $clock.Elapsed.TotalSeconds -lt $BudgetSeconds) {
        $value=Read-CopyProgress $validationPath
        if(-not $validationProducer -and $value -and $value.nonce -ceq $nonce -and
                $value.phase -ceq 'RUNNING' -and $value.pid -is [int] -and $value.pid -gt 0) {
            Observe-RetainedCopyMember $job $value.pid;$validationProducer=$value.pid
            $record.retained_validator_native_pid=$validationProducer
            Write-CopyAck $validationAck $validationProducer
        }
        Start-Sleep -Milliseconds 50
    }
    $running=$job.Running($validator);$validationExit=$null
    if(-not $running){$validationExit=$job.ExitCode($validator)}
    $cleanupAttempted=$true;$job.ShutdownWithin(10000)
    if($running){throw 'OFFLINE_AUDIT_WORKER_TIMEOUT'}
    $value=Read-CopyProgress $validationPath
    $result=Validate-CopyCompletion $value $validationProducer $validationExit $job.CleanupConfirmed $clock.Elapsed.TotalSeconds $BudgetSeconds
    $record.copy_result=$result
    return $result
    } catch {
        # One bounded cleanup attempt for any Start/Report/ACK/Observe failure.
        # Never blindly repeat an already uncertain native-close attempt.
        if($job -and -not $job.CleanupConfirmed -and -not $cleanupAttempted) {
            $cleanupAttempted=$true
            $job.ShutdownWithin(10000)
        }
        throw
    }
}

function Absent {
    $snapshot=Get-IMCProcessSnapshot
    if ($snapshot.Processes.Count -or @($snapshot.UnknownInterpreterPids | Where-Object { $_ -ne $PID }).Count) {
        throw 'OFFLINE_RUNTIME_NOT_ABSENT'
    }
    $raw=@(Get-CimInstance Win32_Process -ErrorAction Stop)
    foreach($row in $raw) {
        if ($row.Name -ieq 'sqlite3.exe') {
            if (-not $row.CommandLine -or -not $row.ExecutablePath) { throw 'OFFLINE_SQLITE_IDENTITY_UNKNOWN' }
            $argv=@([IMCCommandLine]::Split($row.CommandLine))
            if ($row.ExecutablePath -ieq $cli -or @($argv | Where-Object { $_ -ieq $database }).Count) {
                throw 'OFFLINE_SQLITE_PROCESS_PRESENT'
            }
        }
    }
    return @{runtime_roles=0;unknown_interpreters=0;canonical_sqlite_processes=0;scope='CIM observation, not universal writer or mapping proof'}
}
function Finish-Released {
    if (-not $released) {throw 'OFFLINE_RESOURCES_NOT_RELEASED'}
    $record.phase='FINAL';$record.restart_allowed=$true;$record.cleanup_confirmed=$true
    try {Save-Record} catch {} # cleanup evidence is already established in RAM
    try {Write-Output ('OFFLINE_CONTROLLER_FINAL='+$recordPath)} catch {}
    try {Write-Output ('OFFLINE_MEASUREMENT_COMPLETE='+$record.measurement_complete)} catch {}
}
function Retained([string]$Code) {
    if ($released) {Finish-Released;return}
    $record.phase='RETAINED';$record.code=$Code;$record.restart_allowed=$false;$record.cleanup_confirmed=$false
    try {Save-Record} catch {} # protection cannot depend on logging/disk/output
    try {Write-Output ('OFFLINE_CONTROLLER_RETAINED='+$recordPath)} catch {}
    # Keep mutex/streams/job/native handles alive. No cleanup finally can
    # accidentally release this controller's protection on an unknown result.
    while($true) {try {Start-Sleep -Seconds 5} catch {[Threading.Thread]::Sleep(5000)}}
}
Save-Record
try {
    $maintenanceLease=Enter-IMCMaintenanceFence -Python $Python -Purpose Maintenance
    try {$auditOwned=$auditMutex.WaitOne(0)} catch [Threading.AbandonedMutexException] {$auditOwned=$true}
    if (-not $auditOwned) { throw 'OFFLINE_OTHER_AUDIT_PRESENT' }
    $record.stage='SELF_IDENTITY'
    # Bind the sole permitted self exclusion to this exact canonical script.
    $self=Get-CimInstance Win32_Process -Filter ('ProcessId={0}' -f $PID) -ErrorAction Stop
    $selfHandle=Hold $self
    $shell=Join-Path ([Environment]::GetFolderPath('System')) 'WindowsPowerShell\v1.0\powershell.exe'
    $selfArgs=@([IMCCommandLine]::Split($self.CommandLine))
    $expected=@($shell,'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',$PSCommandPath,
        '-ExpectedSupervisorPid',[string]$ExpectedSupervisorPid,'-ExpectedBirthUtc',$ExpectedBirthUtc)
    if($Operation -ceq 'Copy'){$expected+=@('-Operation','Copy')}
    if ($selfArgs.Count -ne $expected.Count -or $self.ExecutablePath -ine $shell) { throw 'OFFLINE_SELF_IDENTITY_UNKNOWN' }
    for($i=0;$i -lt $expected.Count;$i++) {if($selfArgs[$i] -ine $expected[$i]) {throw 'OFFLINE_SELF_IDENTITY_UNKNOWN'}}
    $record.stage='INITIAL_SAFETY';$record.safety_before=Safety
    $record.stage='SUPERVISOR_IDENTITY'
    $snapshot=Get-IMCProcessSnapshot
    $supervisors=@($snapshot.Processes | Where-Object Role -ceq 'SignalSupervisor')
    if ($supervisors.Count -ne 1 -or $supervisors[0].ProcessId -ne $ExpectedSupervisorPid -or
        @($snapshot.UnknownInterpreterPids | Where-Object {$_ -ne $PID}).Count) { throw 'OFFLINE_SUPERVISOR_NOT_OWNED' }
    $runtime=Get-Content -Raw -Encoding UTF8 (Join-Path $Root 'RUNTIME\signal-runtime-status.json') | ConvertFrom-Json
    $age=([DateTimeOffset]::UtcNow-[DateTimeOffset]::Parse($runtime.updated_at)).TotalSeconds
    if ($runtime.pid -ne $ExpectedSupervisorPid -or $runtime.state -cne 'RUNNING' -or $runtime.code -cne 'PASSIVE_NO_TRANSPORT' -or
        $runtime.broadcast -ne $false -or $runtime.live_ready -ne $false -or
        $age -lt 0 -or $age -gt 15) {throw 'OFFLINE_RUNTIME_STATUS_INVALID'}
    $raw=@(Get-CimInstance Win32_Process -ErrorAction Stop)
    $sup=@($raw | Where-Object ProcessId -eq $ExpectedSupervisorPid)
    if ($sup.Count -ne 1) {throw 'OFFLINE_SUPERVISOR_CHANGED'}
    $supArgs=@([IMCCommandLine]::Split($sup[0].CommandLine))
    $runner=Join-Path $Root 'LAUNCHERS\RUN_IMC_SIGNAL_ONLY.ps1'
    $expected=@($shell,'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',$runner)
    $expected[6]=$runner
    if ($supArgs.Count -ne 7 -or $sup[0].ExecutablePath -ine $shell) {throw 'OFFLINE_SUPERVISOR_CHANGED'}
    for($i=0;$i -lt 7;$i++) {if($supArgs[$i] -ine $expected[$i]) {throw 'OFFLINE_SUPERVISOR_CHANGED'}}
    $birth=[DateTime]::ParseExact($ExpectedBirthUtc,'o',[Globalization.CultureInfo]::InvariantCulture,[Globalization.DateTimeStyles]::RoundtripKind)
    if ($birth.Kind -ne [DateTimeKind]::Utc -or [math]::Abs(($sup[0].CreationDate.ToUniversalTime()-$birth).Ticks) -ge 10) {
        throw 'OFFLINE_EXPECTED_BIRTH_MISMATCH'
    }
    $closure=@($sup[0]);$found=@{};$found[$ExpectedSupervisorPid]=$true
    do {
        $new=@($raw | Where-Object {$found.ContainsKey([int]$_.ParentProcessId) -and -not $found.ContainsKey([int]$_.ProcessId)})
        foreach($row in $new) {
            $parent=@($closure | Where-Object ProcessId -eq $row.ParentProcessId)
            if ($parent.Count -ne 1 -or $row.CreationDate -lt $parent[0].CreationDate) {throw 'OFFLINE_ANCESTRY_UNKNOWN'}
            $found[[int]$row.ProcessId]=$true;$closure += $row
        }
        if ($closure.Count -gt 64) {throw 'OFFLINE_DESCENDANT_LIMIT'}
    } while($new.Count)
    if (@($snapshot.Processes | Where-Object {-not $found.ContainsKey([int]$_.ProcessId)}).Count) {throw 'OFFLINE_FOREIGN_RUNTIME_PRESENT'}
    $ownedHandles=@();foreach($row in $closure) {$ownedHandles += Hold $row}
    if (@($ownedHandles | Where-Object HasExited).Count) {throw 'OFFLINE_LIFETIME_CHANGED'}
    $record.owned_native=@($closure | Select-Object ProcessId,ParentProcessId,@{n='birth_utc';e={$_.CreationDate.ToUniversalTime().ToString('o')}},Name)
    $record.identity_scope='held native lifetimes + observed ancestry + suspended Job-start source; not direct old Job-membership query'
    $record.phase='STOP';$record.stage='PRE_STOP_SAFETY';Save-Record
    $record.safety_immediately_before_stop=Safety
    if (@($ownedHandles | Where-Object HasExited).Count) {throw 'OFFLINE_LIFETIME_CHANGED'}
    Set-IMCMaintenanceActive $maintenanceLease $Python $nonce
    $record.stage='STOP_OWNED';$ownedHandles[0].Kill();$record.stopped=$true
    $stopClock=[Diagnostics.Stopwatch]::StartNew()
    foreach($handle in $ownedHandles) {
        if (-not $handle.WaitForExit([int][math]::Max(0,10000-$stopClock.ElapsedMilliseconds))) {throw 'OFFLINE_STOP_UNCONFIRMED'}
    }
    try {$runtimeOwned=$runtimeMutex.WaitOne(0)} catch [Threading.AbandonedMutexException] {$runtimeOwned=$true}
    if (-not $runtimeOwned) {throw 'OFFLINE_REPLACEMENT_MUTEX_PRESENT'}
    $record.stage='POST_STOP_SAFETY';$record.absence_before=Absent
    Recover-QuiescedLedger
    $record.absence_after_ledger_recovery=Absent
    $record.safety_after_stop=Safety
    $record.stage='FILE_GUARDS'
    foreach($path in @($database,($database+'-wal'),$cli,$init)) {
        if ($path -eq ($database+'-wal') -and -not [IO.File]::Exists($path)) {continue}
        $streams += [IO.File]::Open($path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    }
    $record.phase='AUDIT';$record.stage='AUDIT';$record.audit_started_at=[DateTimeOffset]::UtcNow.ToString('o');Save-Record
    if($Operation -ceq 'Copy') {
        $record.phase='COPY';$record.stage='COPY';Save-Record
        $final=Run-Copy
    } else {
    $job=[IMCRuntimeJob]::new()
    $auditPid=$job.Start($Python,[string[]]@((Join-Path $Root 'scripts\db_offline_probe.py'),'--database',$database,
        '--cli',$cli,'--init',$init,'--output',$reportPath,'--nonce',$nonce,'--seconds',([string]$auditBudget),
        '--allocation-mode','objects'),$Root)
    $auditClock=[Diagnostics.Stopwatch]::StartNew()
    $workerObserved=$false
    while($job.Running($auditPid) -and $auditClock.ElapsedMilliseconds -lt (($auditBudget+2)*1000)) {
        if (-not $workerObserved -and [IO.File]::Exists($reportPath)) {
            $progress=Get-Content -Raw -Encoding UTF8 $reportPath | ConvertFrom-Json
            if ($progress.nonce -ceq $nonce -and $progress.phase -ceq 'RUNNING' -and $progress.pid -is [int] -and $progress.pid -gt 0) {
                $job.ObserveMember($progress.pid);$workerObserved=$true
            }
        }
        Start-Sleep -Milliseconds 50
    }
    if ($job.Running($auditPid)) {throw 'OFFLINE_AUDIT_WORKER_TIMEOUT'}
    $exitCode=$job.ExitCode($auditPid)
    if ($exitCode -notin @(0,2)) {throw 'OFFLINE_AUDIT_WORKER_FAILED'}
    $record.stage='AUDIT_CLEANUP';$record.native_job_active_before_shutdown=$job.ActiveProcessCount()
    $job.ShutdownWithin(10000)
    if (-not $job.CleanupConfirmed) {throw 'OFFLINE_AUDIT_JOB_UNCONFIRMED'}
    if (-not $workerObserved) {throw 'OFFLINE_WORKER_LIFETIME_UNOBSERVED'}
    $record.stage='FINAL_VALIDATION';$final=Validate-Final $exitCode
    }
    $record.absence_after=Absent
    $record.safety_after_audit=Safety
    $integrityCode=@'
import sys,sqlite3,json
from pathlib import Path
c=sqlite3.connect(Path(sys.argv[1]).resolve(strict=True).as_uri()+'?mode=ro',uri=True,timeout=1)
try:
 c.execute('PRAGMA query_only=ON')
 rows=c.execute('PRAGMA integrity_check').fetchall()
 if rows!=[('ok',)]:raise RuntimeError('ledger_integrity_failed')
 print(json.dumps({'integrity':True}))
finally:c.close()
'@
    $integrity=@(& $Python -I -c $integrityCode (Join-Path $Root 'RUNTIME\EXECUTOR_GATEWAY\GMGN_BRIDGE\execution-ledger.db') 2>$null)
    if($LASTEXITCODE -ne 0 -or $integrity.Count -ne 1 -or ($integrity[0]|ConvertFrom-Json).integrity -ne $true){throw 'OFFLINE_LEDGER_INTEGRITY_UNPROVEN'}
    $record.measurement_complete=[bool]$final.complete
    $record.phase='RELEASE';$record.stage='RELEASE';Save-Record
    foreach($stream in $streams) {
        Close-IMCAuditFile $stream
    };$streams=@()
    foreach($handle in $handles) {$handle.Dispose()};$handles=@()
    $runtimeMutex.ReleaseMutex();$runtimeOwned=$false
    $auditMutex.ReleaseMutex();$auditOwned=$false
    $runtimeMutex.Dispose();$auditMutex.Dispose()
    Complete-IMCMaintenance $maintenanceLease $Python $true $true $true
    Exit-IMCMaintenanceFence $maintenanceLease;$maintenanceLease=$null
    $released=$true
    Finish-Released
    # Root must recheck GateOFF and perform canonical START as a separate step.
} catch {
    $failure=$_
    $record.failure_stage=$record.stage
    $record.failure_code=Safe-FailureCode $failure.Exception.Message
    Add-FailureDiagnostics $failure
    if ($released) {Finish-Released;exit 0}
    if ($record.stopped -or $streams.Count -or ($job -and -not $job.CleanupConfirmed) -or
        ($maintenanceLease -and $maintenanceLease.ActiveNonce)) {Retained 'OFFLINE_PROTECTION_RETAINED'}
    foreach($handle in $handles) {$handle.Dispose()}
    if ($runtimeOwned) {$runtimeMutex.ReleaseMutex()};if($auditOwned) {$auditMutex.ReleaseMutex()}
    $runtimeMutex.Dispose();$auditMutex.Dispose()
    if($maintenanceLease){Exit-IMCMaintenanceFence $maintenanceLease;$maintenanceLease=$null}
    $record.phase='ABORTED_BEFORE_STOP';$record.code='OFFLINE_PRECONDITION_FAILED';Save-Record
    Write-Output ('OFFLINE_CONTROLLER_ABORTED='+$recordPath)
    exit 2
}
