$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
. (Join-Path $root 'LAUNCHERS\IMC_PROCESS_COMMON.ps1')
$venv=Join-Path $root '.venv\Scripts\python.exe'
$base=([string](Get-Content -LiteralPath (Join-Path $root '.venv\pyvenv.cfg') | Where-Object { $_ -match '^executable = ' })).Substring(13)
$watch=Join-Path $root 'RUNTIME\watch_breadth_v425_shadow.py'
$start=[datetime]'2026-10-02T09:00:00Z'
function New-Fixtures {
    $script:parent=[pscustomobject]@{Name='python.exe'; ProcessId=111; ParentProcessId=10; CreationDate=$start; ExecutablePath=$venv; CommandLine=('"'+$venv+'" -u "'+$watch+'"')}
    $script:child=[pscustomobject]@{Name='python.exe'; ProcessId=112; ParentProcessId=111; CreationDate=$start.AddSeconds(1); ExecutablePath=$base; CommandLine=('"'+$base+'" -u "'+$watch+'"')}
    $script:rows=@($script:parent,$script:child)
    $script:badHandle=$false
    $script:exited=$false
    $script:handleCount=0
}
function Get-CimInstance { return $script:rows }
function Get-Process([int]$Id) {
    $script:handleCount++
    $row=@($script:rows | Where-Object ProcessId -eq $Id)[0]
    $handle=[pscustomobject]@{Handle=1; HasExited=$script:exited; StartTime=$row.CreationDate.AddSeconds([int]$script:badHandle); MainModule=[pscustomobject]@{FileName=$row.ExecutablePath}}
    $handle | Add-Member -MemberType ScriptMethod -Name Dispose -Value { }
    return $handle
}
function Stop-Process { throw 'Fixture must never stop a process' }
New-Fixtures
$snapshot=Get-IMCProcessSnapshot
if ($snapshot.Processes.Count -ne 1 -or $snapshot.Processes[0].ProcessId -ne 112 -or $snapshot.InterpreterRedirectors.Count -ne 1 -or $script:handleCount -ne 2) { throw 'Canonical redirector not bound to worker' }
foreach ($case in @('args','parent','creation','path','handle','multiple','unidentified-child')) {
    New-Fixtures
    switch ($case) {
        'args' { $script:child.CommandLine += ' --extra' }
        'parent' { $script:child.ParentProcessId=999 }
        'creation' { $script:child.CreationDate=$start.AddSeconds(-1) }
        'path' { $script:child.ExecutablePath='C:\Other\python.exe' }
        'handle' { $script:badHandle=$true }
        'multiple' { $copy=$script:child.PSObject.Copy(); $copy.ProcessId=113; $script:rows += $copy }
        'unidentified-child' { $script:rows += [pscustomobject]@{Name='cmd.exe'; ProcessId=113; ParentProcessId=111; CommandLine='cmd.exe'; CreationDate=$start.AddSeconds(1)} }
    }
    $snapshot=Get-IMCProcessSnapshot
    $expected=if ($case -eq 'unidentified-child') {2} else {$script:rows.Count}
    if ($snapshot.Processes.Count -ne $expected -or $snapshot.InterpreterRedirectors.Count -ne 0) { throw "Ambiguous redirector suppressed: $case" }
}
Write-Host 'Eight redirector identity checks passed; no live process access.'
foreach ($case in @('valid-console','foreign-console','extra-child','null-console','exited','reused-console')) {
    New-Fixtures
    $console=[pscustomobject]@{Name='conhost.exe'; ExecutablePath=(Join-Path ([Environment]::GetFolderPath('System')) 'conhost.exe');
        ProcessId=114; ParentProcessId=111; CreationDate=$start.AddMilliseconds(10); CommandLine='conhost.exe 0x4'}
    $script:rows += $console
    switch ($case) {
        'foreign-console' { $console.ExecutablePath='C:\Other\conhost.exe' }
        'extra-child' { $script:rows += [pscustomobject]@{Name='cmd.exe'; ProcessId=115; ParentProcessId=111; CommandLine='cmd.exe'; CreationDate=$start.AddSeconds(1)} }
        'null-console' { $console.CreationDate=$null }
        'exited' { $script:exited=$true }
        'reused-console' { $script:badHandle=$true }
    }
    $snapshot=Get-IMCProcessSnapshot
    if ($case -eq 'valid-console') {
        if ($snapshot.Processes.Count -ne 1 -or $snapshot.InterpreterRedirectors.Count -ne 1 -or $snapshot.InterpreterRedirectors[0].ConsolePid -ne 114 -or $script:handleCount -ne 3) {throw 'Canonical console not bound'}
    } elseif ($snapshot.Processes.Count -ne 2 -or $snapshot.InterpreterRedirectors.Count) { throw "Ambiguous console suppressed: $case" }
}
Write-Host 'Six console identity checks passed; no live process access.'
foreach ($case in @('valid','foreign-parent','extra-argument','wrong-exe','older-child','reused-handle','exited')) {
    New-Fixtures
    $gateway=Join-Path $root 'RUNTIME\EXECUTOR_GATEWAY\GMGN_BRIDGE\imc_pure_signal_gateway.py'
    $rpc=Join-Path $root 'RUNTIME\EXECUTOR_GATEWAY\GMGN_BRIDGE\rpc_read_worker.py'
    $script:child.CommandLine=('"'+$base+'" "'+$gateway+'"')
    $helper=[pscustomobject]@{Name='python.exe'; ProcessId=115; ParentProcessId=112; CreationDate=$start.AddSeconds(2);
        ExecutablePath=$base; CommandLine=('"'+$base+'" "'+$rpc+'"')}
    $script:rows=@($script:child,$helper)
    switch ($case) {
        'foreign-parent' { $helper.ParentProcessId=999 }
        'extra-argument' { $helper.CommandLine += ' --extra' }
        'wrong-exe' { $helper.ExecutablePath='C:\Other\python.exe' }
        'older-child' { $helper.CreationDate=$start }
        'reused-handle' { $script:badHandle=$true }
        'exited' { $script:exited=$true }
    }
    $snapshot=Get-IMCProcessSnapshot
    if ($case -eq 'valid') {
        if (@($snapshot.Processes | Where-Object Role -eq 'RPCReadHelper').Count -ne 1 -or
            $snapshot.UnknownInterpreterPids.Count -ne 0) { throw 'Exact transient RPC helper not recognized' }
    } elseif (@($snapshot.Processes | Where-Object Role -eq 'RPCReadHelper').Count -ne 0 -or
              $snapshot.UnknownInterpreterPids -notcontains 115) { throw "Ambiguous RPC helper accepted: $case" }
}
Write-Host 'Seven transient read identity checks passed; no live process access.'
