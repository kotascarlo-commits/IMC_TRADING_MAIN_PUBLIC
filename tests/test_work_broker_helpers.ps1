param([Parameter(Mandatory=$true)][string]$Workspace)
$ErrorActionPreference='Stop'
. (Join-Path (Split-Path -Parent $PSScriptRoot) 'TOOLS\IMC_WORK_BROKERS.ps1')
$name='imc-astra-broker-'+[guid]::NewGuid().ToString('N')
$owned=New-IMCOwnedBrokerDirectory $Workspace $name
[IO.File]::WriteAllText((Join-Path $owned.Path 'unknown-file'),'preserve')
$rejected=$false
try { New-IMCOwnedBrokerDirectory $Workspace $name | Out-Null } catch {$rejected=$true}
if (-not $rejected) { throw 'existing directory reused' }
$sentinel=Join-Path $Workspace 'foreign-marker';[IO.File]::WriteAllText($sentinel,'foreign')
$foreign=[pscustomobject]@{Path=$owned.Path;Parent=$Workspace;Stop=$sentinel}
Stop-IMCOwnedWorkBrokers $null @($foreign)
if ([IO.File]::ReadAllText($sentinel) -cne 'foreign' -or [IO.File]::Exists($owned.Stop)) { throw 'foreign marker written' }
$copied=$owned | ConvertTo-Json | ConvertFrom-Json
Stop-IMCOwnedWorkBrokers $null @($copied)
if ([IO.File]::Exists($owned.Stop)) { throw 'copied directory capability accepted' }
Stop-IMCOwnedWorkBrokers $null @($owned)
if (-not [IO.File]::Exists($owned.Stop) -or -not [IO.File]::Exists((Join-Path $owned.Path 'unknown-file'))) { throw 'owned directory lost' }
[IO.File]::WriteAllText($owned.Stop,'existing')
Stop-IMCOwnedWorkBrokers $null @($owned)
if ([IO.File]::ReadAllText($owned.Stop) -cne 'existing') { throw 'marker overwritten' }
foreach($count in @(1,2)) {
  $job=[IMCRuntimeJob]::new();$children=@()
  try {
    for ($i=0;$i -lt $count;$i++) {
      $childPid=$job.Start((Join-Path $PSHOME 'powershell.exe'),[string[]]@('-NoProfile','-NonInteractive','-Command','Start-Sleep -Seconds 120'),$Workspace)
      $child=[Diagnostics.Process]::GetProcessById($childPid);$null=$child.Handle;$children+=$child
    }
    throw 'fixture partial startup failure'
  } catch {
    if ($_.Exception.Message -cne 'fixture partial startup failure') { throw }
  } finally { Stop-IMCOwnedWorkBrokers $job @($owned) }
  foreach($child in $children) {
    try { if (-not $child.WaitForExit(5000)) { throw 'own child survived partial startup cleanup' } }
    finally {$child.Dispose()}
  }
}
Write-Output 'exclusive IPC and retained-file checks passed'
