$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
. (Join-Path $root 'LAUNCHERS/IMC_PROCESS_COMMON.ps1')
$checks=0
function Check($Value,[string]$Name){if(-not $Value){throw $Name};$script:checks++}
$self=Get-Process -Id $PID
$alive=[pscustomobject]@{ProcessId=$PID;ParentProcessId=0;Name='powershell.exe';CreationDate=$self.StartTime;CommandLine=$null;ExecutablePath=$self.Path}
$self.Dispose()
Check ($null -eq (Get-IMCExpiredSnapshotEvidence $alive 'fixture')) 'alive missing commandline remains unknown'
$absent=[pscustomobject]@{ProcessId=2147483647;ParentProcessId=0;Name='powershell.exe';CreationDate=[datetime]::UtcNow.AddSeconds(-1);CommandLine=$null}
$e=Get-IMCExpiredSnapshotEvidence $absent 'fixture'
Check ($e.decision_reason -eq 'ABSENT_AT_PROBE' -and $e.native_error -eq 87 -and $e.snapshot_id -eq 'fixture') 'real positive-pid OpenProcess error87'
foreach($id in @(0,-1,2147483648L,$true)){
    $row=[pscustomobject]@{ProcessId=$id;CreationDate=[datetime]::UtcNow.AddSeconds(-1)}
    Check ($null -eq (Get-IMCExpiredSnapshotEvidence $row 'fixture')) 'invalid pid never expired'
}
foreach($birth in @($null,[datetime]::UtcNow.AddHours(1),[datetime]::MinValue)){
    $row=[pscustomobject]@{ProcessId=2147483647;CreationDate=$birth}
    Check ($null -eq (Get-IMCExpiredSnapshotEvidence $row 'fixture')) 'missing or invalid birth never expired'
}
Check ([IMCSnapshotLifetime]::BoundEnd(0,$true,1009,1000,$true)) '9tick boundary bound'
Check (-not [IMCSnapshotLifetime]::BoundEnd(0,$true,1010,1000,$true)) '10tick mismatch rejected'
Check (-not [IMCSnapshotLifetime]::BoundEnd([uint32]::MaxValue,$true,1000,1000,$true)) 'WAIT_FAILED rejected'
Check (-not [IMCSnapshotLifetime]::BoundEnd(0,$false,1000,1000,$true)) 'GetProcessTimes failure rejected'
Check (-not [IMCSnapshotLifetime]::BoundEnd(258,$true,1000,1000,$true)) 'live handle rejected'
Check (-not [IMCSnapshotLifetime]::BoundEnd(0,$true,1000,1000,$false)) 'unconfirmed close rejected'
$p=Start-Process (Join-Path $PSHOME powershell.exe) -ArgumentList @('-NoProfile','-Command','Start-Sleep -Milliseconds 300') -WindowStyle Hidden -PassThru
$null=$p.Handle
try{
    $ended=[pscustomobject]@{ProcessId=$p.Id;ParentProcessId=$PID;Name='powershell.exe';CreationDate=$p.StartTime;CommandLine=$null}
    Check ($p.WaitForExit(5000)) 'owned fixture child native exit'
    $e=Get-IMCExpiredSnapshotEvidence $ended 'fixture'
    Check ($e.decision_reason -eq 'SIGNALED_BIRTH_BOUND' -and $e.native_close_confirmed) 'same held native lifetime signaled'
    $wrong=[pscustomobject]@{ProcessId=$p.Id;CreationDate=$p.StartTime.AddTicks(10)}
    Check ($null -eq (Get-IMCExpiredSnapshotEvidence $wrong 'fixture')) 'native reused-or-mismatched birth never expired'
    $relative=[pscustomobject]@{ProcessId=$p.Id;ParentProcessId=$PID;Name='powershell.exe';CreationDate=$p.StartTime;
        CommandLine='powershell.exe -NoProfile -File TOOLS/IMC_Terminal.ps1 t status'}
    $script:rows=@($alive,$absent,$relative)
    function Get-CimInstance {return $script:rows}
    $s=Get-IMCProcessSnapshot
    Check ($s.UnknownInterpreterPids -contains $PID -and $s.UnknownInterpreterPids -contains $p.Id -and
        $s.UnknownInterpreterPids -notcontains 2147483647) 'only missing-commandline OS-expired row excluded'
    Check ($s.ExpiredProcessEvidence.Count -eq 1 -and $s.ExpiredProcessEvidence[0].snapshot_id -eq $s.SnapshotId) 'expired audit persisted with snapshot identity'
}finally{$p.Dispose()}
$original=${function:Get-IMCExpiredSnapshotEvidence}
try{
    # Access denial and native API faults all return no expired proof.
    function Get-IMCExpiredSnapshotEvidence {return}
    $script:rows=@($alive,$absent)
    $s=Get-IMCProcessSnapshot
    Check ($s.UnknownInterpreterPids.Count -eq 2 -and $s.ExpiredProcessEvidence.Count -eq 0) 'unprovable native probe remains blocking'
}finally{${function:Get-IMCExpiredSnapshotEvidence}=$original}
[pscustomobject]@{ok=$true;checks=$checks}|ConvertTo-Json -Compress
