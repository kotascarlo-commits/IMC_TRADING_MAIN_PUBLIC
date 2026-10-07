param([string]$Workspace,[string]$Python,[switch]$Crash)
$ErrorActionPreference='Stop'
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class IMCOrphanExitProbe {
    [DllImport("kernel32.dll",SetLastError=true)] public static extern uint WaitForSingleObject(IntPtr process,uint milliseconds);
}
'@
. (Join-Path (Split-Path -Parent $PSScriptRoot) 'TOOLS\IMC_RUNTIME_JOB.ps1')
$job=[IMCRuntimeJob]::new()
try {
    $pidFixture=$job.Start($Python,[string[]]@((Join-Path $Workspace 'tree.py')),$Workspace)
    $deadline=[DateTime]::UtcNow.AddSeconds(6)
    while (-not (Test-Path -LiteralPath (Join-Path $Workspace 'child.pid')) -and [DateTime]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 50
    }
    if (-not $job.Running($pidFixture)) { throw 'fixture_process_did_not_start' }
    if ($job.ActiveProcessCount() -lt 2) { throw 'native_descendants_not_counted' }
    if ($Crash) {
        [IO.File]::WriteAllText((Join-Path $Workspace 'ready'),'ready')
        Start-Sleep -Seconds 30
    }
    $job.Dispose()
    if (-not $job.CleanupConfirmed) { throw 'job_cleanup_unconfirmed' }
    if ($job.Running($pidFixture)) { throw 'closed_job_retained_child' }
    $job=[IMCRuntimeJob]::new()
    $orphan=$job.Start($Python,[string[]]@((Join-Path $Workspace 'orphan.py')),$Workspace)
    $deadline=[DateTime]::UtcNow.AddSeconds(6)
    while (($job.Running($orphan) -or -not (Test-Path -LiteralPath (Join-Path $Workspace 'orphan-child.pid'))) -and
        [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 20 }
    if ($job.Running($orphan) -or $job.ActiveProcessCount() -lt 1) { throw 'orphan_descendant_not_counted' }
    $orphanHandle=Get-Process -Id ([int](Get-Content -LiteralPath (Join-Path $Workspace 'orphan-child.pid')))
    $orphanNative=$orphanHandle.Handle
    if ([IMCOrphanExitProbe]::WaitForSingleObject($orphanNative,0) -ne 258) {throw 'orphan_not_alive_before_shutdown'}
    $job.ObserveMember($orphanHandle.Id) # native binding to this specific job
    try {
        $job.Dispose()
        $nativeExit=[IMCOrphanExitProbe]::WaitForSingleObject($orphanNative,0)
        $managedExit=$orphanHandle.WaitForExit(0)
        if (-not $job.CleanupConfirmed -or -not $managedExit -or $nativeExit -ne 0 -or $job.LastActiveCount -ne 0) {
            throw ('orphan_descendant_cleanup_unconfirmed:cleanup='+$job.CleanupConfirmed+':native='+$nativeExit+':managed='+$managedExit+':accounting='+$job.LastActiveCount)
        }
    } finally { $orphanHandle.Dispose() }
    $job=[IMCRuntimeJob]::new()
    $failed=$false
    try { $job.Start((Join-Path $Workspace 'missing.exe'),[string[]]@(),$Workspace) | Out-Null } catch { $failed=$true }
    if (-not $failed) { throw 'bad_start_accepted' }
    $arguments=[string[]]@('with spaces','quote"inside','tail\','',('Unicode-'+[char]0x00e4))
    $fixture=$job.Start($Python,([string[]]@((Join-Path $Workspace 'args.py')) + $arguments),$Workspace)
    $deadline=[DateTime]::UtcNow.AddSeconds(6)
    while($job.Running($fixture) -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 50 }
    if ($job.ExitCode($fixture) -ne 0) { throw 'argument_fixture_failed' }
    $job.Release($fixture)
    $failedFixture=$job.Start($Python,[string[]]@('-c','raise SystemExit(7)'),$Workspace)
    $deadline=[DateTime]::UtcNow.AddSeconds(6)
    while($job.Running($failedFixture) -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 50 }
    if ($job.ExitCode($failedFixture) -ne 7) { throw 'native failure exit code lost' }
    $job.Release($failedFixture)
    Write-Output 'runtime job checks passed'
} finally { $job.Dispose() }
