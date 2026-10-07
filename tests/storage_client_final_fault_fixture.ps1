param([string]$Workspace,[string]$Python,[ValidateSet('concurrent','reentrant')][string]$Case)
$ErrorActionPreference='Stop';$repo=Split-Path -Parent $PSScriptRoot
. (Join-Path $repo 'TOOLS\IMC_STORAGE_CHANNEL.ps1')
. (Join-Path $repo 'TOOLS\IMC_RUNTIME_JOB.ps1')
$nonce=[guid]::NewGuid().ToString('N');$server=[IMCStorageChannel]::CreateServer($nonce,'client_final',15)
$job=[IMCRuntimeJob]::new();$cleanupAttempted=$false
try{
    $birth=[Diagnostics.Process]::GetCurrentProcess().StartTime.ToUniversalTime().ToFileTimeUtc()
    $child=$job.Start($Python,[string[]]@('-I',(Join-Path $PSScriptRoot 'storage_client_final_fault_child.py'),
        $nonce,[string]$PID,[string]$birth,$Workspace,$Case),$Workspace)
    $job.ObserveMember($child);$held=[Diagnostics.Process]::GetProcessById($child);$null=$held.Handle
    $server.BindPeer($child,$held.StartTime.ToUniversalTime().ToFileTimeUtc());$server.Connect()
    $server.Write([Text.Encoding]::UTF8.GetBytes('begin'))
    if([Text.Encoding]::UTF8.GetString($server.Read()) -cne 'ready'){throw 'client_final_fixture_ready'}
    $server.WriteTerminal([Text.Encoding]::UTF8.GetBytes('close now'))
    $clock=[Diagnostics.Stopwatch]::StartNew();$report=Join-Path $Workspace 'client-final.json'
    while(-not [IO.File]::Exists($report) -and $clock.Elapsed.TotalSeconds -lt 8){Start-Sleep -Milliseconds 10}
    if(-not [IO.File]::Exists($report) -or -not $job.Running($child)){throw 'client_final_fixture_fault_not_held'}
    $evidence=Get-Content -LiteralPath $report -Raw | ConvertFrom-Json
    if(-not $evidence.faulted -or $evidence.closed -or $evidence.native_closed -ne 2 -or $evidence.retry_calls -ne 0){throw 'client_final_fixture_wrong_evidence'}
    $cleanupAttempted=$true;$job.ShutdownWithin(10000);if(-not $job.CleanupConfirmed){throw 'client_final_fixture_job_unconfirmed'}
    $server.Close();if(-not $server.Closed -or $server.Faulted){throw 'client_final_fixture_root_cleanup'}
    'NATIVE_CLIENT_FINAL_FAULT_PASS'
}finally{if(-not $cleanupAttempted){$cleanupAttempted=$true;$job.ShutdownWithin(10000)}}
