param([string]$Workspace,[string]$Python,[ValidateSet('Copy','Validate','Retained','Certified')][string]$Case='Copy',
      [ValidateSet('None','ReportProof','AckProof','ObserveProof','ReportValidation','AckValidation','ObserveValidation','AuthorityAck','Budget','BudgetAck','WorkBudgetAck','AfterEvidence','DelayedPreflight','Shutdown')][string]$Fault='None')
$ErrorActionPreference='Stop'
$Root=Split-Path -Parent $PSScriptRoot
$tokens=$null;$parseErrors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $Root 'TOOLS\Invoke-IMC-OfflineAudit.ps1'),[ref]$tokens,[ref]$parseErrors)
if($parseErrors.Count){throw 'controller_syntax_invalid'}
foreach($definition in $ast.FindAll({param($node)$node -is [Management.Automation.Language.FunctionDefinitionAst]},$false)) {
    . ([scriptblock]::Create($definition.Extent.Text))
}
if($Case -ceq 'Validate') {
    $nonce='a'*32
    $value=Get-Content -LiteralPath (Join-Path $Workspace 'final.json') -Raw|ConvertFrom-Json
    try {
        $null=Validate-CopyCompletion $value 123 0 $true 10 30
        Write-Output 'strict native completion PASS';exit 0
    } catch {Write-Output 'strict native completion REJECT';exit 2}
}
. (Join-Path $Root 'TOOLS\IMC_RUNTIME_JOB.ps1')
Add-Type -TypeDefinition @'
using System;using System.Runtime.InteropServices;
public static class IMCAuditNativeClose {
 [DllImport("kernel32.dll",SetLastError=true)]public static extern bool CloseHandle(IntPtr handle);
}
'@
$database=Join-Path $Workspace 'source.db'
$scope=Join-Path $Workspace 'controller';$null=New-Item -ItemType Directory -Path $scope
$nonce=[guid]::NewGuid().ToString('N');$auditBudget=30;$reportPath=Join-Path $scope 'copy.json'
if($Case -cin @('Retained','Certified')) {
    $scope=Join-Path $Workspace ('.local-tools\offline-audit-'+$nonce)
    $null=New-Item -ItemType Directory -Path $scope -Force
    $reportPath=Join-Path $scope 'copy.json'
}
$record=@{};$streams=@();$job=$null
try {
    # Same original guards as the caller, maintained across both real handoffs.
    Protect-CopyMember $database
    if([IO.File]::Exists($database+'-wal')){Protect-CopyMember ($database+'-wal')}
    $result=Run-Copy -Ledger (Join-Path $Workspace 'ledger.db') -Gate (Join-Path $Workspace 'gate.json')
    if($result.complete -ne $true -or $result.independent_comparison -ne $true -or
        $record.raw_parent_guards -ne $true -or $record.work_parent_guards -ne $true -or
        $record.copy_worker_native_pid -isnot [int] -or $record.copy_validator_native_pid -isnot [int] -or
        -not $job.CleanupConfirmed){throw 'fixture_handoff_proof_missing'}
    foreach($path in @($database,(Join-Path $scope 'raw\source.db'),(Join-Path $scope 'raw\source.db-wal'),(Join-Path $scope 'work\source.db'))) {
        $blocked=$false
        try{$writer=[IO.File]::Open($path,[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,[IO.FileShare]::ReadWrite);$writer.Dispose()}
        catch [IO.IOException]{$blocked=$true}
        if(-not $blocked){throw 'fixture_parent_guard_missing'}
    }
    if($Case -cin @('Retained','Certified')) {
        $oldScope=$scope;$oldNonce=$nonce;$failedPath=$reportPath
        $origin=Read-CopyProgress $failedPath
        # Synthetic failed header only. Actual source/RAW/WORK were produced
        # above under real native Jobs and overlapping parent guards.
        $origin.phase='FINAL';$origin.stage='BUILD';$origin.complete=$false;$origin.cleanup_confirmed=$false
        $origin|Add-Member NoteProperty code 'COPY_PROOF_FAILED'
        $origin|Add-Member NoteProperty exception_kind 'TimeoutError'
        [IO.File]::WriteAllText($failedPath,($origin|ConvertTo-Json -Depth 12))
        $nonce=[guid]::NewGuid().ToString('N')
        $scope=Join-Path $Workspace ('.local-tools\retained-repair-'+$nonce)
        $null=New-Item -ItemType Directory -Path $scope
        $verification=@{OldScope=$oldScope;OldNonce=$oldNonce;FailedReportPath=$failedPath;
            OldProducer=$origin.pid;BudgetSeconds=30;AuthorityRoot=$Workspace;
            Ledger=(Join-Path $Workspace 'ledger.db');Gate=(Join-Path $Workspace 'gate.json')}
        $actualReader=(Get-Command Read-CopyProgress).ScriptBlock
        $actualAck=(Get-Command Write-CopyAck).ScriptBlock
        function Read-CopyProgress([string]$Path) {
            $value=& $actualReader $Path
            if($value -and $value.phase -ceq 'RUNNING' -and
                    (($Fault -ceq 'ReportProof' -and $Path.EndsWith('copy-proof.json')) -or
                     ($Fault -ceq 'ReportValidation' -and $Path.EndsWith('copy-validation.json')))) {
                throw 'fixture_report_exception'
            }
            return $value
        }
        function Write-CopyAck([string]$Path,[int]$NativePid) {
            $validationAck=if($Case -ceq 'Certified'){'copy-validation-seen.json'}else{'copy-validator-seen.json'}
            if(($Fault -ceq 'AckProof' -and $Path.EndsWith('copy-proof-seen.json')) -or
                    ($Fault -ceq 'AckValidation' -and $Path.EndsWith($validationAck))) {
                throw 'fixture_ack_exception'
            }
            & $actualAck $Path $NativePid
        }
        function Observe-RetainedCopyMember($Job,[int]$NativePid) {
            $Job.ObserveMember($NativePid)
            if($Fault -ceq 'Shutdown') {
                $field=$Job.GetType().GetField('closeFault',[Reflection.BindingFlags]'NonPublic,Instance')
                $field.SetValue($Job,$true)
            }
            $isValidation=if($Case -ceq 'Certified'){$global:IMCHeldCertifications[-1].Jobs[-1].Name -ceq 'validation'}else{[bool]$record.retained_proof_native_cleanup}
            if(($Fault -ceq 'ObserveProof' -and -not $isValidation) -or
                    ($Fault -ceq 'ObserveValidation' -and $isValidation)) {
                throw 'fixture_observe_exception'
            }
        }
        $failedAsExpected=$false
        try {
            if($Case -ceq 'Certified') {
                . (Join-Path $Root 'LAUNCHERS\IMC_PROCESS_COMMON.ps1')
                . (Join-Path $Root 'TOOLS\IMC_COPY_CERTIFICATION.ps1')
                $repairAst=[Management.Automation.Language.Parser]::ParseFile((Join-Path $Root 'TOOLS\Repair-IMC-RetainedAudit.ps1'),[ref]$null,[ref]$null)
                $fresh=@($repairAst.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq 'Fresh-RepairRecord'},$false))
                . ([scriptblock]::Create($fresh[0].Extent.Text))
                $priorJob=$job;$priorStreams=$streams;$priorRecordReference=$record
                $priorRecord=$record|ConvertTo-Json -Depth 10 -Compress
                $certArgs=@{CodeRoot=$Root;AuthorityRoot=$Workspace;Python=$Python;Source=$database;
                    OldScope=$oldScope;OldNonce=$oldNonce;FailedReportPath=$failedPath;OldProducer=$origin.pid;
                    Ledger=(Join-Path $Workspace 'ledger.db');Gate=(Join-Path $Workspace 'gate.json');Scope=$scope;
                    Nonce=$nonce;BudgetSeconds=40;AuthorityCheck={
                        param($heldState,$boundary)
                        if($Fault -ceq 'DelayedPreflight' -and -not $heldState.Jobs.Count -and $boundary -ceq 'FULL'){Start-Sleep -Seconds 6}
                        if($Fault -ceq 'Budget'){Start-Sleep -Seconds 36}
                        if($Fault -ceq 'BudgetAck' -and $heldState.Jobs.Count -and $heldState.Jobs[-1].Producer -and
                            -not $heldState.Jobs[-1].CleanupAttempted){Start-Sleep -Seconds 36}
                        if($Fault -ceq 'WorkBudgetAck' -and $heldState.Jobs.Count -and $heldState.Jobs[-1].Producer -and
                            -not $heldState.Jobs[-1].CleanupAttempted){Start-Sleep -Seconds 26}
                        if($Fault -ceq 'AfterEvidence' -and [IO.File]::Exists((Join-Path $scope 'done.json'))){return $false}
                        if($Fault -ceq 'AuthorityAck' -and $heldState.Jobs.Count -and $heldState.Jobs[-1].Producer -and
                            -not $heldState.Jobs[-1].CleanupAttempted){return $false}
                        foreach($held in $streams){if(-not $held.CanRead -or $held.SafeFileHandle.IsClosed){throw 'fixture_original_guard_closed'}}
                        return $true
                    }}
                if($Fault -cin @('Budget','BudgetAck','WorkBudgetAck')){$certArgs.BudgetSeconds=35}
                & (Join-Path $PSScriptRoot 'storage_cert_scope_wrapper.ps1') -Arguments $certArgs -CodeRoot $Root
                [GC]::Collect();[GC]::WaitForPendingFinalizers();[GC]::Collect()
                $cert=$global:IMCHeldCertifications[-1]
                & (Join-Path $PSScriptRoot 'storage_cert_retention_inspector.ps1') -State $cert
                if(-not $cert.Complete -or $cert.Faulted -or $cert.Jobs.Count -ne 2 -or $cert.Guards.Count -ne 2 -or
                    -not [object]::ReferenceEquals($job,$priorJob) -or ($record|ConvertTo-Json -Depth 10 -Compress) -cne $priorRecord) {
                    throw 'fixture_certificate_ownership_changed'
                }
                foreach($stage in $cert.Jobs) {
                    if(-not $stage.Job.CleanupConfirmed -or -not $stage.NativeCleanup -or $stage.ExitCode -ne 0 -or
                        $stage.Report.Guard.SafeFileHandle.IsClosed){throw 'fixture_certificate_native_unknown'}
                    $blocked=$false
                    try{$writer=[IO.File]::Open($stage.Report.Path,[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,[IO.FileShare]::ReadWrite);$writer.Dispose()}
                    catch [IO.IOException]{$blocked=$true}
                    if(-not $blocked){throw 'fixture_final_report_mutable'}
                }
                $originAfter=Read-CopyProgress $failedPath
                if($originAfter.complete -ne $false){throw 'fixture_original_incident_changed'}
                Write-Output 'isolated new certification + separate native Jobs + protected FINAL reports PASS'
                if($Fault -ceq 'DelayedPreflight'){Write-Output 'delayed FULL preflight before native Start + short ACK check PASS'}
                return
            }
            $result=Run-RetainedCopyVerification @verification
        }
        catch {
            if($Fault -ceq 'None'){throw}
            $failure=$_
            $expected= switch -Regex ($Fault) {
                '^Report' {'fixture_report_exception';break}
                '^Ack' {'fixture_ack_exception';break}
                '^Observe' {'fixture_observe_exception';break}
                '^(AuthorityAck|AfterEvidence)$' {'COPY_CERTIFICATION_AUTHORITY';break}
                '^(Budget|BudgetAck|WorkBudgetAck)$' {'COPY_CERTIFICATION_DEADLINE';break}
                '^Shutdown$' {'runtime_handle_close_unconfirmed';break}
                default {throw 'fixture_unexpected_fault_case'}
            }
            if($Fault -ceq 'Shutdown') {
                if(-not $failure.Exception.InnerException -or $failure.Exception.InnerException.Message -cne $expected){throw 'fixture_wrong_shutdown_error'}
            } elseif($failure.FullyQualifiedErrorId -cne $expected) {throw 'fixture_wrong_expected_error'}
            if($Case -ceq 'Certified' -and (-not [object]::ReferenceEquals($record,$priorRecordReference) -or
                -not [object]::ReferenceEquals($job,$priorJob) -or -not [object]::ReferenceEquals($streams,$priorStreams) -or
                ($record|ConvertTo-Json -Depth 10 -Compress) -cne $priorRecord)){throw 'fixture_parent_original_changed'}
            $failedAsExpected=$true
            if(-not $job.CleanupConfirmed){throw 'fixture_exception_job_not_closed'}
            if($Case -ceq 'Certified') {
                $heldState=$global:IMCHeldCertifications[-1]
                [GC]::Collect();[GC]::WaitForPendingFinalizers();[GC]::Collect()
                & (Join-Path $PSScriptRoot 'storage_cert_retention_inspector.ps1') -State $heldState
                if(-not $heldState.Faulted -or $heldState.Complete -or
                    ($Fault -cne 'AfterEvidence' -and [IO.File]::Exists((Join-Path $scope 'done.json')))) {
                    throw 'fixture_certificate_false_complete'
                }
                if($Fault -ceq 'Shutdown') {
                    $stage=$heldState.Jobs[-1]
                    if(-not $stage.PrimaryFailure -or -not $stage.CleanupFailure -or $stage.NativeCleanup -or
                        -not $stage.CleanupAttempted -or $stage.CleanupStatus -cne 'ATTEMPTED_UNCONFIRMED') {
                        throw 'fixture_shutdown_failure_lost'
                    }
                }
                if($Fault -ceq 'BudgetAck') {
                    $stage=$heldState.Jobs[-1]
                    if($stage.CleanupAttempted -or $stage.CleanupFailure -or $stage.NativeCleanup -or
                        $stage.CleanupStatus -cne 'NOT_ATTEMPTED_NO_BUDGET'){throw 'fixture_budget_cleanup_claimed'}
                }
                [IO.File]::WriteAllText((Join-Path $Workspace 'certificate-failure.json'),(@{
                    faulted=$heldState.Faulted;complete=$heldState.Complete;jobs=$heldState.Jobs.Count;
                    cleanup_status=@($heldState.Jobs|ForEach-Object {$_.CleanupStatus});
                    primary_failure=@($heldState.Jobs|ForEach-Object {[bool]$_.PrimaryFailure});
                    cleanup_failure=@($heldState.Jobs|ForEach-Object {[bool]$_.CleanupFailure})}|ConvertTo-Json -Compress))
            }
            foreach($stream in $streams) {
                if(-not $stream.CanRead -or $stream.SafeFileHandle.IsClosed){throw 'fixture_exception_guard_released'}
            }
            if($Fault -cin @('Shutdown','BudgetAck')){Write-Output 'expected cleanup UNKNOWN; guards retained PASS'}
            else {Write-Output 'isolated retained exception native cleanup + guards held PASS'}
        }
        if($Fault -cne 'None') {
            if(-not $failedAsExpected){throw 'fixture_fault_did_not_trigger'}
            return
        }
        if($result.complete -ne $true -or -not $job.CleanupConfirmed -or
                $record.retained_proof_native_pid -isnot [int] -or
                $record.retained_validator_native_pid -isnot [int] -or
                $record.retained_proof_native_cleanup -ne $true){throw 'fixture_retained_native_proof_missing'}
        $stillFailed=Read-CopyProgress $failedPath
        if($stillFailed.complete -ne $false -or $stillFailed.code -cne 'COPY_PROOF_FAILED'){throw 'fixture_origin_changed'}
        Write-Output 'isolated retained proof + new native ACKs + independent validation PASS'
    }
    Write-Output 'isolated actual native Job copy + parent handoffs + independent validation PASS'
} catch {
    [IO.File]::WriteAllText((Join-Path $scope 'fixture-diagnostic.json'),($record|ConvertTo-Json))
    throw
} finally {
    foreach($cert in $global:IMCHeldCertifications) {
        foreach($stage in $cert.Jobs){if(-not $stage.Job.CleanupConfirmed -and $Fault -cnotin @('Shutdown','BudgetAck')){throw 'fixture_certificate_native_cleanup_unknown'}}
        foreach($guard in $cert.Guards){Close-IMCAuditFile $guard}
    }
    if($job -and -not $job.CleanupConfirmed){$job.ShutdownWithin(10000)}
    if($job -and -not $job.CleanupConfirmed){throw 'fixture_native_cleanup_unknown'}
    if($Fault -cnotin @('Shutdown','BudgetAck')){foreach($stream in $streams){Close-IMCAuditFile $stream}}
}
