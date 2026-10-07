param([Parameter(Mandatory=$true)][string]$Workspace)
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
. (Join-Path $root 'LAUNCHERS\IMC_PROCESS_COMMON.ps1')
$runner=Join-Path $root 'LAUNCHERS\RUN_IMC_SIGNAL_ONLY.ps1'
function Role([string]$Tail) {
  (Get-IMCProcessRole ([pscustomobject]@{Name='powershell.exe';
    CommandLine=('powershell.exe -NoProfile -File "'+$runner+'" '+$Tail)})).Role
}
if ((Role '-ProbeOnly -SignedQuoteProbe') -cne 'GMGNReadProbe') { throw 'probe identity' }
foreach($tail in @('', '-ProbeOnly:$false', '-ProbeOnly:$true', '-ProbeOn',
                    '-ProbeOnly -ProbeOnly:$false','-ProbeOnly -ProbeOnly')) {
  if ((Role $tail) -cne 'SignalSupervisor') { throw 'unsafe supervisor hiding' }
}
if (Get-IMCProcessRole ([pscustomobject]@{Name='powershell.exe';
  CommandLine='powershell.exe -File C:\foreign\RUN_IMC_SIGNAL_ONLY.ps1 -ProbeOnly'})) { throw 'foreign identity' }
function Get-CimInstance {
  @([pscustomobject]@{Name='python.exe';ProcessId=101;CommandLine=('python.exe "'+$root+'\RUNTIME\unrecognized.py"')},
    [pscustomobject]@{Name='node.exe';ProcessId=102;CommandLine=('node.exe C:\external\codex.js -C "'+$root+'"')},
    [pscustomobject]@{Name='python.exe';ProcessId=103;CommandLine='python.exe -m mykonos_meme_scout unknown'},
    [pscustomobject]@{Name='node.exe';ProcessId=105;CommandLine=('node.exe C:\external\codex.js "read-only'+"`n`r"+'question"')},
    [pscustomobject]@{Name='python.exe';ProcessId=106;CommandLine='python.exe RUNTIME\watch_breadth_v425_shadow.py'})
  $fixturePid=201
  foreach($name in @('Start-IMC-Work','GMGN-ReadBroker','IMC-AdvisorBroker','Invoke-IMC-AdvisorBroker','Invoke-GMGN-ReadBroker')) {
    [pscustomobject]@{Name='powershell.exe';ProcessId=$fixturePid;CommandLine=('powershell.exe -File "'+$root+'\TOOLS\'+$name+'.ps1"')}
    $fixturePid++
  }
}
$snapshot=Get-IMCProcessSnapshot
if ($snapshot.UnknownInterpreterPids.Count -ne 3 -or
  $snapshot.UnknownInterpreterPids -notcontains 101 -or $snapshot.UnknownInterpreterPids -notcontains 103 -or
  $snapshot.UnknownInterpreterPids -notcontains 106) { throw 'repository unknown omitted' }
foreach($fixturePid in @(105,201,202,203,204,205)) {
  if ($snapshot.UnknownInterpreterPids -contains $fixturePid -or
      @($snapshot.Processes | Where-Object { $_.ProcessId -eq $fixturePid }).Count) { throw 'development process treated as runtime' }
}
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($runner,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'runner syntax' }
$definition=$ast.Find({param($a) $a -is [Management.Automation.Language.FunctionDefinitionAst] -and
  $a.Name -ceq 'Write-RuntimeStatus'},$true)
Invoke-Expression $definition.Extent.Text
$statePath=Join-Path $Workspace 'existing-runtime.json'
[IO.File]::WriteAllText($statePath,'existing-state')
$ProbeOnly=$true
Write-RuntimeStatus 'UNKNOWN' 'PROBE_FAILURE' @{}
if ([IO.File]::ReadAllText($statePath) -cne 'existing-state') { throw 'probe touched runtime status' }
$ProbeOnly=$false
$workers=@{CORE=@{pid=1};WATCHER=@{pid=2};GATEWAY=@{pid=3};BRIDGE=@{pid=0;last_exit_code=7};HEALTH=@{pid=4}}
Write-RuntimeStatus 'RUNNING' 'PASSIVE_NO_TRANSPORT' $workers
$saved=Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
if ($saved.state -cne 'UNKNOWN' -or $saved.code -cne 'WORKER_RECOVERY' -or $saved.workers.BRIDGE.last_exit_code -ne 7) { throw 'missing worker reported running' }
Write-RuntimeStatus 'UNKNOWN' 'PARTIAL_START_FAILURE' @{CORE=@{pid=1}}
$saved=Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
if ($saved.workers.CORE.pid -ne 1) { throw 'partial startup diagnostics lost' }
Write-Output 'probe identity and status isolation checks passed'
