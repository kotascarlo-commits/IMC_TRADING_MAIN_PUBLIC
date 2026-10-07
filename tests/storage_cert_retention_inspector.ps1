param([Parameter(Mandatory)][hashtable]$State)
if(-not [object]::ReferenceEquals($State,$global:IMCHeldCertifications[-1]) -or $State.OwnerPid -ne $PID) {
    throw 'fixture_global_retention_missing'
}
foreach($guard in $State.Guards) {
    if(-not $guard.CanRead -or $guard.CanWrite -or $guard.SafeFileHandle.IsClosed){throw 'fixture_report_guard_lost'}
}
foreach($stage in $State.Jobs) {
    if($State.Complete -and (-not $stage.Job.CleanupConfirmed -or $stage.ExitCode -ne 0)) {
        throw 'fixture_completed_job_lost'
    }
}
