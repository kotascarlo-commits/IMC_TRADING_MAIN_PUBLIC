param([Parameter(Mandatory=$true)][string]$Workspace)
$ErrorActionPreference='Stop'
. (Join-Path (Split-Path -Parent $PSScriptRoot) 'TOOLS\IMC_WORK_PROGRESS.ps1')
$path=Join-Path $Workspace 'progress.json'
$packages='[{"id":"implementation","weight":50,"percent":0},{"id":"verification","weight":50,"percent":0}]'
$first=Set-IMCWorkProgress -Path $path -SessionId 'session' -TaskId 'task' -ExpectedRevision 0 -Begin `
  -Task 'fixture task' -PackagesJson $packages -CurrentStep 'fixture step'
if ($first.percent -ne 0 -or $first.revision -ne 1) { throw 'initial progress' }
$second=Set-IMCWorkProgress -Path $path -SessionId 'session' -TaskId 'task' -ExpectedRevision 1 `
  -PackageId implementation -PackagePercent 100
if ($second.percent -ne 50 -or $second.revision -ne 2) { throw 'atomic replace' }
$before=[IO.File]::ReadAllText($path)
foreach ($case in @(@{Session='session';Task='task';Revision=1},
                    @{Session='foreign';Task='task';Revision=2},
                    @{Session='session';Task='other';Revision=2})) {
  $rejected=$false
  try { Set-IMCWorkProgress -Path $path -SessionId $case.Session -TaskId $case.Task -ExpectedRevision $case.Revision | Out-Null }
  catch { $rejected=$true }
  if (-not $rejected -or [IO.File]::ReadAllText($path) -cne $before) { throw 'progress CAS or identity corruption' }
}
$third=Set-IMCWorkProgress -Path $path -SessionId 'session' -TaskId 'task' -ExpectedRevision 2 `
  -TestsPassed 3 -TestsTotal 3 -TestsVerified $true -AstraStatus checked
$fourth=Set-IMCWorkProgress -Path $path -SessionId 'session' -TaskId 'task' -ExpectedRevision 3 `
  -PackageId verification -PackagePercent 100
if ($fourth.tests_verified -or $fourth.percent -ne 99) { throw 'new work did not invalidate tests' }
$before=[IO.File]::ReadAllText($path)
$rejected=$false
try { Set-IMCWorkProgress -Path $path -SessionId 'session' -TaskId 'task' -ExpectedRevision 4 -State completed | Out-Null }
catch {$rejected=$true}
if (-not $rejected -or [IO.File]::ReadAllText($path) -cne $before) { throw 'unverified completion allowed' }
$finished=Set-IMCWorkProgress -Path $path -SessionId 'session' -TaskId 'task' -ExpectedRevision 4 `
 -TestsVerified $true -State completed
if ($finished.percent -ne 100) { throw 'verified completion blocked' }
foreach ($change in @('work','tests','counts','review')) {
  $copy=$before | ConvertFrom-Json
  $copy.state='completed';$copy.tests_verified=$true
  switch($change) {
    work {$copy.work_packages[0].percent=99}
    tests {$copy.tests_verified=$false}
    counts {$copy.tests_passed=2}
    review {$copy.astra_status='pending'}
  }
  $rejected=$false
  try { Test-IMCProgressStatus $copy | Out-Null } catch {$rejected=$true}
  if (-not $rejected) { throw 'completion guard missing' }
}
$forged=$before | ConvertFrom-Json
$forged.work_packages[1].percent=0;$forged.percent=100
if ((Format-IMCWorkProgress $forged 'session') -notlike '[[]IMC WORK[]] 50%*') { throw 'forged percent displayed' }
if ((Format-IMCWorkProgress $forged 'foreign') -notlike '*Andere Sitzung*') { throw 'foreign task auto-adopted' }
Write-Output 'work progress CAS, identity and completion guards passed'
