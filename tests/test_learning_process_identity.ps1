$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
. (Join-Path $root 'LAUNCHERS\IMC_PROCESS_COMMON.ps1')
$venv=Join-Path $root '.venv\Scripts\python.exe'
$base=([string](Get-Content -LiteralPath (Join-Path $root '.venv\pyvenv.cfg') | Where-Object { $_ -match '^executable = ' })).Substring(13)
$db=Join-Path $root 'CORE\data\imc-main.db'
$shell=Join-Path ([Environment]::GetFolderPath('System')) 'WindowsPowerShell\v1.0\powershell.exe'
$runner=Join-Path $root 'LAUNCHERS\RUN_IMC_SIGNAL_ONLY.ps1'
$start=[datetime]'2026-10-02T09:00:00Z'
$script:checks=0
function Line($Exe,$Tail) { return (@($Exe)+@($Tail) | ForEach-Object { '"'+$_+'"' }) -join ' ' }
function Row([int]$Id,[int]$Parent,[int]$Second,$Exe,$Tail) {
    return [pscustomobject]@{ProcessId=$Id; ParentProcessId=$Parent; CreationDate=$start.AddSeconds($Second);
        ExecutablePath=$Exe; Name=[IO.Path]::GetFileName($Exe); CommandLine=(Line $Exe $Tail)}
}
function New-Fixture([bool]$WithBase=$true) {
    $script:supervisor=Row 10 9 0 $shell @('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',$runner)
    $coreArgs=@('-u','-m','mykonos_meme_scout','--database',$db,'run','--maximum','30','--interval','0.5')
    $mlArgs=@('-m','mykonos_meme_scout','--database',$db,'ml-auto-once')
    $script:coreVenv=Row 11 10 1 $venv $coreArgs
    $script:coreBase=Row 12 11 2 $base $coreArgs
    $script:mlVenv=Row 13 12 3 $venv $mlArgs
    $script:mlBase=Row 14 13 4 $base $mlArgs
    $script:rows=@($supervisor,$coreVenv,$coreBase,$mlVenv)
    if ($WithBase) { $script:rows += $mlBase }
    $script:cfg=@('executable = '+$base);$script:cfgLength=100
    $script:opened=@();$script:disposed=@();$script:reads=@{}
    $script:failOpen=0;$script:dieAtFinal=0;$script:wrongImage=0;$script:offset=@{}
}
function Get-CimInstance { return $script:rows }
function Get-Item { return [pscustomobject]@{Length=$script:cfgLength} }
function Get-Content { return $script:cfg }
function Get-Process([int]$Id) {
    if ($Id -eq $script:failOpen) { throw 'FIXTURE_OPEN_FAILED' }
    $row=@($script:rows | Where-Object ProcessId -eq $Id)[0]
    $script:opened += $Id
    $ticks=if ($script:offset.ContainsKey($Id)) {$script:offset[$Id]} else {0}
    $image=if ($Id -eq $script:wrongImage) {'C:\Other\python.exe'} else {$row.ExecutablePath}
    $h=[pscustomobject]@{ProcessId=$Id; Handle=1; StartTime=$row.CreationDate.AddTicks($ticks);
        MainModule=[pscustomobject]@{FileName=$image}}
    $h | Add-Member ScriptProperty HasExited {
        $id=$this.ProcessId
        if (-not $script:reads.ContainsKey($id)) {$script:reads[$id]=0}
        $script:reads[$id]++
        return ($id -eq $script:dieAtFinal -and $script:reads[$id] -gt 1)
    }
    $h | Add-Member ScriptMethod Dispose { $script:disposed += $this.ProcessId }
    return $h
}
function Stop-Process { throw 'Live process stop forbidden in fixtures' }
function Assert-Disposed {
    if ((@($script:opened | Sort-Object) -join ',') -cne (@($script:disposed | Sort-Object) -join ',')) {
        throw 'Native fixture handle leaked'
    }
}
foreach ($withBase in @($false,$true)) {
    New-Fixture $withBase
    if (-not (Test-IMCTransientLearningProcess $mlVenv $rows)) { throw 'Canonical ML chain rejected' }
    Assert-Disposed
    $snapshot=Get-IMCProcessSnapshot
    $ml=@($snapshot.Processes | Where-Object Role -ceq 'MLAutoOnce')
    if ($ml.Count -ne 1 -or $snapshot.UnknownInterpreterPids.Count) { throw 'Canonical ML snapshot rejected' }
    if ($withBase) {
        if ($ml[0].ProcessId -ne 14 -or @($snapshot.InterpreterRedirectors | Where-Object Role -ceq 'MLAutoOnce').Count -ne 1) {
            throw 'ML redirector not independently deduplicated'
        }
    } elseif ($ml[0].ProcessId -ne 13 -or @($snapshot.InterpreterRedirectors | Where-Object Role -ceq 'MLAutoOnce').Count) {
        throw 'Absent BASE incorrectly claimed as redirector'
    }
    Assert-Disposed;$script:checks++
}
foreach ($case in @('extra-ml','config-ml','relative-db','bad-present-base','foreign-base-image','missing-birth',
    'missing-core','missing-supervisor','extra-core','wrong-core-maximum','extra-supervisor','foreign-supervisor',
    'duplicate-pid','cycle','older-ml','extra-ml-child','extra-core-child','foreign-console','null-console',
    'open-failure','late-exit','reused-10-ticks','cfg-missing','cfg-duplicate','cfg-relative','cfg-alias','cfg-venv','cfg-oversize')) {
    New-Fixture
    switch ($case) {
        'extra-ml' { $mlVenv.CommandLine += ' --extra' }
        'config-ml' { $mlVenv.CommandLine += ' --config C:\Other\config.json' }
        'relative-db' { $mlVenv.CommandLine=$mlVenv.CommandLine.Replace($db,'CORE\data\imc-main.db') }
        'bad-present-base' { $mlBase.CommandLine += ' --extra' }
        'foreign-base-image' { $script:wrongImage=14 }
        'missing-birth' { $mlBase.CreationDate=$null }
        'missing-core' { $script:rows=@($rows | Where-Object ProcessId -ne 12) }
        'missing-supervisor' { $script:rows=@($rows | Where-Object ProcessId -ne 10) }
        'extra-core' { $coreBase.CommandLine += ' --extra' }
        'wrong-core-maximum' { $coreVenv.CommandLine=$coreVenv.CommandLine.Replace('"30"','"29"') }
        'extra-supervisor' { $supervisor.CommandLine += ' -ProbeOnly' }
        'foreign-supervisor' { $supervisor.ExecutablePath='C:\Other\powershell.exe' }
        'duplicate-pid' { $script:rows += $coreBase.PSObject.Copy() }
        'cycle' { $coreVenv.ParentProcessId=13 }
        'older-ml' { $mlVenv.CreationDate=$start }
        'extra-ml-child' { $script:rows += Row 20 13 4 'C:\Other\cmd.exe' @() }
        'extra-core-child' { $script:rows += Row 20 11 4 'C:\Other\cmd.exe' @() }
        'foreign-console' { $script:rows += Row 20 13 4 'C:\Other\conhost.exe' @() }
        'null-console' { $console=Row 20 13 4 (Join-Path ([Environment]::GetFolderPath('System')) 'conhost.exe') @();$console.CreationDate=$null;$script:rows+=$console }
        'open-failure' { $script:failOpen=12 }
        'late-exit' { $script:dieAtFinal=14 }
        'reused-10-ticks' { $script:offset[14]=10 }
        'cfg-missing' { $script:cfg=@() }
        'cfg-duplicate' { $script:cfg += 'executable = '+$base }
        'cfg-relative' { $script:cfg=@('executable = python.exe') }
        'cfg-alias' { $script:cfg=@('executable = '+(Join-Path (Split-Path $base) '.\python.exe')) }
        'cfg-venv' { $script:cfg=@('executable = '+$venv) }
        'cfg-oversize' { $script:cfgLength=8193 }
    }
    if (Test-IMCTransientLearningProcess $mlVenv $rows) { throw "Invalid chain accepted: $case" }
    Assert-Disposed;$script:checks++
}
New-Fixture
$script:offset[14]=9
if (-not (Test-IMCTransientLearningProcess $mlVenv $rows)) { throw 'Documented CIM precision tolerance rejected' }
Assert-Disposed;$script:checks++
New-Fixture
foreach ($parent in @($coreVenv,$mlVenv)) {
    $script:rows += Row (20+$parent.ProcessId) $parent.ProcessId 4 (Join-Path ([Environment]::GetFolderPath('System')) 'conhost.exe') @()
}
if (-not (Test-IMCTransientLearningProcess $mlBase $rows)) { throw 'Canonical consoles rejected' }
Assert-Disposed;$script:checks++
# No parent-role/name exception may identify an orphan module by itself.
New-Fixture
if (Get-IMCProcessRole $mlVenv) { throw 'Context-free module was identified' }
$script:rows=@($mlVenv,$mlBase)
$orphan=Get-IMCProcessSnapshot
if (@($orphan.Processes | Where-Object Role -ceq 'MLAutoOnce').Count -or
    $orphan.UnknownInterpreterPids -notcontains 13 -or $orphan.UnknownInterpreterPids -notcontains 14) { throw 'Orphan escaped UNKNOWN' }
Assert-Disposed;$script:checks++
foreach ($leaveOrphan in @($false,$true)) {
    New-Fixture
    $script:stopped=@();$script:leaveOrphan=$leaveOrphan
    function Stop-IMCIdentifiedProcess($Expected) {
        $script:stopped += $Expected.Role
        if ($Expected.Role -cne 'SignalSupervisor') { throw 'Unexpected child stop after supervisor' }
        $script:rows=if ($script:leaveOrphan) {@($mlVenv,$mlBase)} else {@()}
    }
    $blocked=$false
    try { Stop-IMCRoles } catch { if ($_.Exception.Message -like 'Stop not confirmed:*') {$blocked=$true} else {throw} }
    if ($blocked -ne $leaveOrphan -or ($script:stopped -join ',') -cne 'SignalSupervisor') { throw 'Orphan stop transition unsafe' }
    Assert-Disposed;$script:checks++
}
Write-Host "Learning identity checks passed: $script:checks; no live process access."
