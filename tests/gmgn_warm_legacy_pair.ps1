# OWN fixtures only. Dummy IPC authentication, no provider configuration.
function Start-OwnWarmLegacyPair {
 param([string]$Repo,[string]$Case)
 . (Join-Path $Repo 'TOOLS\IMC_RUNTIME_JOB.ps1')
 $scope=Join-Path (Join-Path $Repo '.local-tools') ('legacy-capture-native-'+[guid]::NewGuid().ToString('N'))
 $legacy=Join-Path $scope 'legacy';$null=[IO.Directory]::CreateDirectory($legacy)
 $utf8=[Text.UTF8Encoding]::new($false)
 [IO.File]::WriteAllText((Join-Path $scope 'broker.ps1'),'param($Scope);$c=[Diagnostics.Stopwatch]::StartNew();while($c.Elapsed.TotalSeconds -lt 110){Start-Sleep -Milliseconds 20}',$utf8)
 $parent=@'
param($Scope,$Tools,$Case)
$ErrorActionPreference='Stop';Add-Type -Path (Join-Path $Tools 'GmgnRepairProcessBinding.cs')
$script=Join-Path $Scope 'broker.ps1'
$child=Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') -ArgumentList @('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',('"'+$script+'"'),'-Scope',('"'+$Scope+'"')) -PassThru -WindowStyle Hidden
$null=$child.Handle;$self=[Diagnostics.Process]::GetCurrentProcess()
$broker=[GmgnRepairProcessBinding]::Read($child);$parent=[GmgnRepairProcessBinding]::Read($self)
function Binding($n,$s){@{pid=[int]$n[0];parent_pid=[int]$n[1];created_at=$n[2];executable=$n[3];script=$s}}
$body=@{schema_version=1;instance_id=('d'*32);directory=(Join-Path $Scope 'legacy');broker=(Binding $broker $script);parent=(Binding $parent (Join-Path $Scope 'parent.ps1'))}|ConvertTo-Json -Compress -Depth 5
$h=[Security.Cryptography.HMACSHA256]::new([Text.Encoding]::ASCII.GetBytes('f'*64))
try{$mac=([BitConverter]::ToString($h.ComputeHash([Text.Encoding]::UTF8.GetBytes($body)))).Replace('-','').ToLowerInvariant()}finally{$h.Dispose()}
if($Case -ceq 'WrongMac'){$mac='0'*64}
[IO.File]::WriteAllText((Join-Path $Scope 'legacy\broker.ready'),(@{body=$body;mac=$mac}|ConvertTo-Json -Compress),[Text.UTF8Encoding]::new($false))
[IO.File]::WriteAllText((Join-Path $Scope ('legacy\'+('a'*32)+'.response.json')),'OWN_WARM_LEGACY_RESPONSE',[Text.UTF8Encoding]::new($false))
[IO.File]::WriteAllText((Join-Path $Scope 'pair-ready.json'),(@{broker=$broker;parent=$parent}|ConvertTo-Json -Compress),[Text.UTF8Encoding]::new($false))
if($Case -ceq 'RetireNaturalExit'){
 if(-not $child.WaitForExit(110000)){throw 'OWN_RETIRE_PARENT_CHILD_WAIT_TIMEOUT'}
 if($child.ExitCode -ne 125){throw 'OWN_RETIRE_PARENT_CHILD_EXIT_CODE'}
}else{$c=[Diagnostics.Stopwatch]::StartNew();while($c.Elapsed.TotalSeconds -lt 110){Start-Sleep -Milliseconds 20}}
'@
 [IO.File]::WriteAllText((Join-Path $scope 'parent.ps1'),$parent,$utf8)
 $job=[IMCRuntimeJob]::new();$result=[pscustomobject]@{Scope=$scope;Job=$job;Parent=$null;Broker=$null;Bindings=$null;Cleanup=$false}
 try{
  $parentPid=$job.Start((Join-Path $PSHOME 'powershell.exe'),[string[]]@('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',(Join-Path $scope 'parent.ps1'),'-Scope',$scope,'-Tools',(Join-Path $Repo 'TOOLS'),'-Case',$Case),$scope);$job.ObserveMember($parentPid)
  $clock=[Diagnostics.Stopwatch]::StartNew();while(-not [IO.File]::Exists((Join-Path $scope 'pair-ready.json')) -and $clock.Elapsed.TotalSeconds -lt 8){Start-Sleep -Milliseconds 10}
  if(-not [IO.File]::Exists((Join-Path $scope 'pair-ready.json'))){throw 'OWN_WARM_LEGACY_PAIR_TIMEOUT'}
  $bindings=Get-Content -LiteralPath (Join-Path $scope 'pair-ready.json') -Raw|ConvertFrom-Json;$result.Bindings=$bindings
  foreach($role in @('parent','broker')){
   $native=$bindings.$role;$process=[Diagnostics.Process]::GetProcessById([int]$native[0]);$null=$process.Handle
   if(([GmgnRepairProcessBinding]::Read($process) -join '|') -cne ($native -join '|')){throw 'OWN_WARM_LEGACY_PAIR_IDENTITY'}
   $result.$role=$process;$job.ObserveMember([int]$native[0])
  }
  return $result
 }catch{$job.ShutdownWithin(10000);if(-not $job.CleanupConfirmed){throw 'OWN_WARM_LEGACY_PAIR_CLEANUP'};throw}
}
function Stop-OwnWarmLegacyPair {
 param($Pair)
 $Pair.Job.ShutdownWithin(10000)
 if(-not $Pair.Job.CleanupConfirmed -or -not $Pair.Parent.WaitForExit(5000) -or -not $Pair.Broker.WaitForExit(5000)){throw 'OWN_WARM_LEGACY_PAIR_EXIT_UNCONFIRMED'}
 $Pair.Cleanup=$true
}
