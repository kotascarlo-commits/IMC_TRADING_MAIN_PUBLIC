# Read-only re-certification over caller-held Source/RAW/WORK. No entrypoint,
# handover, marker write, file release, publication or runtime restart.
# Canonical controller validators and native Job class must already be loaded.
function Invoke-IMCHeldCopyCertification {
    param([Parameter(Mandatory)][string]$CodeRoot,
          [Parameter(Mandatory)][string]$AuthorityRoot,
          [Parameter(Mandatory)][string]$Python,
          [Parameter(Mandatory)][string]$Source,
          [Parameter(Mandatory)][string]$OldScope,
          [Parameter(Mandatory)][string]$OldNonce,
          [Parameter(Mandatory)][string]$FailedReportPath,
          [Parameter(Mandatory)][int]$OldProducer,
          [Parameter(Mandatory)][string]$Ledger,
          [Parameter(Mandatory)][string]$Gate,
          [Parameter(Mandatory)][string]$Scope,
          [Parameter(Mandatory)][string]$Nonce,
          [Parameter(Mandatory)][scriptblock]$AuthorityCheck,
          [Diagnostics.Stopwatch]$SharedClock=$null,
          [ValidateRange(10,30)][double]$CleanupReserveSeconds=10,
          [ValidateRange(35,21600)][double]$BudgetSeconds=21600)
    $nonce=$Nonce;$clock=if($SharedClock){$SharedClock}else{[Diagnostics.Stopwatch]::StartNew()}
    if(-not $clock.IsRunning){throw 'COPY_CERTIFICATION_CLOCK_INVALID'}
    $workDeadline=$BudgetSeconds-$CleanupReserveSeconds
    $state=@{Nonce=$Nonce;Scope=$Scope;OwnerPid=$PID;
        OwnerBirth=[Diagnostics.Process]::GetCurrentProcess().StartTime.ToUniversalTime().ToString('o');
        Jobs=[Collections.Generic.List[object]]::new();
        Guards=[Collections.Generic.List[IO.FileStream]]::new();Faulted=$false;Complete=$false}
    # Register before native Start or protected report acquisition.
    if(-not (Get-Variable -Name IMCHeldCertifications -Scope Global -ErrorAction SilentlyContinue)) {
        $global:IMCHeldCertifications=@()
    }
    if($global:IMCHeldCertifications -isnot [array]){throw 'COPY_CERTIFICATION_RETENTION_INVALID'}
    $global:IMCHeldCertifications+=@($state)
    function Check-Certificate([ValidateSet('FULL','ACK')][string]$Boundary='FULL') {
        if($state.Faulted -or $clock.Elapsed.TotalSeconds -ge $workDeadline){throw 'COPY_CERTIFICATION_DEADLINE'}
        $valid=& $AuthorityCheck $state $Boundary
        if($valid -isnot [bool] -or -not $valid){throw 'COPY_CERTIFICATION_AUTHORITY'}
        if($clock.Elapsed.TotalSeconds -ge $workDeadline){throw 'COPY_CERTIFICATION_DEADLINE'}
    }
    function Seal-CertificateReport([string]$Path) {
        Check-Certificate
        $guard=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
        $state.Guards.Add($guard)
        if($guard.Length -gt 131072){throw 'OFFLINE_FINAL_REPORT_INVALID'}
        $sha=[Security.Cryptography.SHA256]::Create()
        try {$hash=[BitConverter]::ToString($sha.ComputeHash($guard)).Replace('-','').ToLowerInvariant()}
        finally {$sha.Dispose()}
        $guard.Position=0
        $reader=[IO.StreamReader]::new($guard,[Text.UTF8Encoding]::new($false,$true),$true,4096,$true)
        try{$value=$reader.ReadToEnd()|ConvertFrom-Json}finally{$reader.Dispose()}
        Check-Certificate
        return @{Path=$Path;Hash=$hash;Value=$value;Guard=$guard}
    }
    function Run-CertificateStage([string]$Name,[string[]]$Arguments) {
        Check-Certificate
        $remaining=$workDeadline-$clock.Elapsed.TotalSeconds
        if($remaining -le 5){throw 'COPY_CERTIFICATION_DEADLINE'}
        $output=Join-Path $Scope ('copy-'+$Name+'.json')
        $ack=Join-Path $Scope ('copy-'+$Name+'-seen.json')
        $arguments+=@('--seconds',$remaining.ToString('R',[Globalization.CultureInfo]::InvariantCulture),
            '--output',$output,'--ack',$ack)
        $native=[IMCRuntimeJob]::new()
        $stage=@{Job=$native;Name=$Name;Primary=0;Producer=0;CleanupAttempted=$false;
            NativeCleanup=$false;ExitCode=$null;Report=$null;PrimaryFailure=$null;CleanupFailure=$null;
            CleanupStatus='NOT_ATTEMPTED'}
        $state.Jobs.Add($stage)
        try {
            $stage.Primary=$native.Start($Python,$arguments,$CodeRoot)
            while($native.Running($stage.Primary) -and $clock.Elapsed.TotalSeconds -lt $workDeadline) {
                $value=Read-CopyProgress $output
                if($value -and $value.nonce -ceq $nonce -and $value.phase -ceq 'RUNNING') {
                    if($value.pid -isnot [int] -or $value.pid -le 0){throw 'OFFLINE_WORKER_LIFETIME_UNOBSERVED'}
                    if(-not $stage.Producer) {
                        if(($Name -ceq 'proof' -and $value.stage -cne 'AWAIT_NATIVE_ACK') -or
                            ($Name -ceq 'validation' -and $value.PSObject.Properties.Name -ccontains 'stage')) {
                            throw 'OFFLINE_WORKER_LIFETIME_UNOBSERVED'
                        }
                        Observe-RetainedCopyMember $native $value.pid
                        $stage.Producer=$value.pid
                        Check-Certificate 'ACK'
                        Write-CopyAck $ack $stage.Producer
                        Check-Certificate 'ACK'
                    } elseif($value.pid -ne $stage.Producer){throw 'OFFLINE_WORKER_LIFETIME_UNOBSERVED'}
                }
                Start-Sleep -Milliseconds 50
            }
            $running=$native.Running($stage.Primary)
            if(-not $running){$stage.ExitCode=$native.ExitCode($stage.Primary)}
            $cleanupBudget=[math]::Min(10000,[math]::Floor(($BudgetSeconds-$clock.Elapsed.TotalSeconds)*1000))
            if($cleanupBudget -lt 1){$stage.CleanupStatus='NOT_ATTEMPTED_NO_BUDGET';throw 'COPY_CERTIFICATION_DEADLINE'}
            $stage.CleanupAttempted=$true
            $stage.CleanupStatus='ATTEMPTED_UNCONFIRMED'
            $native.ShutdownWithin([int]$cleanupBudget)
            $stage.NativeCleanup=$native.CleanupConfirmed
            if($stage.NativeCleanup){$stage.CleanupStatus='CONFIRMED'}
            if($running -or -not $stage.NativeCleanup -or -not $stage.Producer -or $stage.ExitCode -ne 0) {
                throw 'OFFLINE_AUDIT_JOB_UNCONFIRMED'
            }
            $stage.Report=Seal-CertificateReport $output
            return $stage
        } catch {
            $stage.PrimaryFailure=$_
            if($stage.CleanupAttempted -and -not $native.CleanupConfirmed){$stage.CleanupFailure=$_}
            if(-not $stage.CleanupAttempted -and -not $native.CleanupConfirmed) {
                $left=[math]::Min(10000,[math]::Floor(($BudgetSeconds-$clock.Elapsed.TotalSeconds)*1000))
                if($left -ge 1) {
                    $stage.CleanupAttempted=$true;$stage.CleanupStatus='ATTEMPTED_UNCONFIRMED'
                    try{$native.ShutdownWithin([int]$left)}catch{$stage.CleanupFailure=$_}
                } else {$stage.CleanupStatus='NOT_ATTEMPTED_NO_BUDGET'}
            }
            $stage.NativeCleanup=$native.CleanupConfirmed
            if($stage.NativeCleanup){$stage.CleanupStatus='CONFIRMED'}
            $state.Faulted=$true
            throw
        }
    }
    try {
        if($Nonce -cnotmatch '\A[a-f0-9]{32}\z' -or $Nonce -ceq $OldNonce -or $OldProducer -le 0 -or
            -not [IO.Directory]::Exists($Scope) -or @(Get-ChildItem -LiteralPath $Scope -Force).Count) {
            throw 'COPY_CERTIFICATION_SCOPE'
        }
        Check-Certificate
        Fresh-RepairRecord (Join-Path $Scope 'intent.json') @{
            schema_version=1;nonce=$Nonce;old_copy_nonce=$OldNonce;old_producer=$OldProducer;
            owner_pid=$PID;budget_seconds=$BudgetSeconds;operation='READ_ONLY_CERTIFICATION';
            restart_allowed=$false;publication_allowed=$false}
        $worker=Join-Path $CodeRoot 'scripts\storage_copy_contract.py'
        $first=Run-CertificateStage 'proof' @('-I',$worker,'--operation','prove-retained',
            '--root',$AuthorityRoot,'--report',$FailedReportPath,'--source',$Source,
            '--scope',$OldScope,'--old-nonce',$OldNonce,'--nonce',$Nonce,
            '--worker-pid',([string]$OldProducer),'--exit-code','2','--ledger',$Ledger,'--gate',$Gate)
        $proof=$first.Report.Value
        if(-not $proof -or $proof.kind -cne 'RETAINED_READONLY_PROOF' -or
            $proof.old_copy_nonce -cne $OldNonce -or $proof.nonce -cne $Nonce -or
            $proof.pid -isnot [int] -or $proof.pid -ne $first.Producer -or
            $proof.phase -cne 'FINAL' -or $proof.stage -cne 'FINAL' -or
            $proof.complete -isnot [bool] -or -not $proof.complete -or
            $proof.cleanup_confirmed -isnot [bool] -or -not $proof.cleanup_confirmed) {
            throw 'OFFLINE_FINAL_REPORT_INVALID'
        }
        $second=Run-CertificateStage 'validation' @('-I',$worker,'--root',$AuthorityRoot,
            '--report',$first.Report.Path,'--source',$Source,'--scope',$OldScope,'--nonce',$Nonce,
            '--worker-pid',([string]$first.Producer),'--exit-code','0')
        $result=Validate-CopyCompletion $second.Report.Value $second.Producer $second.ExitCode $second.NativeCleanup $clock.Elapsed.TotalSeconds $BudgetSeconds
        if($result.semantic_orphans -ne 0){throw 'OFFLINE_FINAL_REPORT_INVALID'}
        Check-Certificate
        Fresh-RepairRecord (Join-Path $Scope 'done.json') @{
            schema_version=1;nonce=$Nonce;phase='CERTIFICATION_EVIDENCE';complete=$false;
            proof_pid=$first.Producer;proof_exit=$first.ExitCode;proof_native_cleanup=$first.NativeCleanup;
            proof_sha256=$first.Report.Hash;validation_pid=$second.Producer;validation_exit=$second.ExitCode;
            validation_native_cleanup=$second.NativeCleanup;validation_sha256=$second.Report.Hash;
            elapsed_seconds=$clock.Elapsed.TotalSeconds;result=$result;
            source_release=$false;restart_allowed=$false;publication_allowed=$false;
            authority_grant=$false;requires_live_owner_check=$true}
        Check-Certificate
        $state.Complete=$true
        return $state
    } catch {$state.Faulted=$true;throw}
}
