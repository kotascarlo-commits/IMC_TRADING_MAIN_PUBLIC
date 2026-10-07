param([ValidateSet('Normal','CommandMismatch','SidMismatch','PeerMismatch','EarlyExit','InstanceSwitch','EngineSwitch')][string]$Case='Normal')
$ErrorActionPreference='Stop';$repo=Split-Path -Parent $PSScriptRoot
. (Join-Path $repo 'TOOLS\GMGN-SupervisorTask.ps1')
Add-Type -Path (Join-Path $repo 'TOOLS\GmgnRepairProcessBinding.cs')
Add-Type -Path @((Join-Path $repo 'TOOLS\GmgnScheduledActionIdentity.cs'),(Join-Path $repo 'TOOLS\GmgnRepairNamespace.cs'),(Join-Path $repo 'TOOLS\GmgnReadDirectoryIdentity.cs'))
$scope=Join-Path (Join-Path $repo '.local-tools') ('task-instance-native-'+[guid]::NewGuid().ToString('N'));$null=[IO.Directory]::CreateDirectory($scope)
$worker=Join-Path $scope 'worker.ps1'
$code=@'
param([string]$Scope)
$ErrorActionPreference='Stop'
[IO.File]::WriteAllText((Join-Path $Scope 'worker-hint.json'),(@{pid=$PID;provider_calls=0}|ConvertTo-Json -Compress))
$clock=[Diagnostics.Stopwatch]::StartNew()
while(-not [IO.File]::Exists((Join-Path $Scope 'worker-exit')) -and $clock.Elapsed.TotalSeconds -lt 20){Start-Sleep -Milliseconds 10}
exit 0
'@
[IO.File]::WriteAllText($worker,$code,[Text.UTF8Encoding]::new($false))
$pin=[IO.FileStream]::new($worker,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
$sha=[Security.Cryptography.SHA256]::Create();try{$hash=[BitConverter]::ToString($sha.ComputeHash($pin)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
# Small public bootstrap verified by this launcher. Contains no auth material.
$launch='$ErrorActionPreference="Stop";$p="'+$worker+'";$s=[IO.FileStream]::new($p,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read);$h=[Security.Cryptography.SHA256]::Create();try{$v=[BitConverter]::ToString($h.ComputeHash($s)).Replace("-","").ToLowerInvariant()}finally{$h.Dispose()};if($v -cne "'+$hash+'"){exit 125};$s.Position=0;$r=[IO.StreamReader]::new($s);$t=$r.ReadToEnd();& ([scriptblock]::Create($t)) -Scope "'+$scope+'"'
$encoded=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($launch))
$arguments='-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -EncodedCommand '+$encoded
$service=New-Object -ComObject Schedule.Service;$service.Connect();$folder=$service.GetFolder('\')
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value;$exe=Join-Path $PSHOME 'powershell.exe';$name='IMC-READ-FIXTURE-'+[guid]::NewGuid().ToString('N')
$created=$false;$task=$null;$originalWorker=$null;$engine=$null
function RemoveOwnActionTask {
 $current=$folder.GetTask($name)
 Assert-GmgnSupervisorTaskDefinition $current.Definition $sid $exe $arguments $repo
 if($current.GetInstances(0).Count){throw 'OWN_ACTION_TASK_CLEANUP_ACTIVE'}
 $folder.DeleteTask($name,0)
 $absent=$false;try{$null=$folder.GetTask($name)}catch{$cause=$_.Exception;while($cause.InnerException){$cause=$cause.InnerException};$absent=($cause.HResult -band 65535) -eq 2}
 if(-not $absent){throw 'OWN_ACTION_TASK_CLEANUP_UNCONFIRMED'}
}
try{
 $definition=New-GmgnSupervisorTaskDefinition $service $sid $exe $arguments $repo
 try{$task=Register-GmgnSupervisorTask $folder $name $definition $sid $exe $arguments $repo;$created=$true}catch{
  if($_.Exception.Data.Contains('GmgnCreatedTask')){$task=$_.Exception.Data['GmgnCreatedTask'];$created=$true};throw
 }
 $earliest=[DateTime]::UtcNow.ToString('o')
 $instance=Start-GmgnSupervisorTask $task $sid $exe $arguments $repo
 $savedGuid=$instance.InstanceGuid;$instance.Refresh();$savedEngine=[int]$instance.EnginePID
 if(-not $savedGuid -or $savedEngine -le 0){throw 'OWN_ACTION_SCHEDULER_INSTANCE_MISSING'}
 $clock=[Diagnostics.Stopwatch]::StartNew();$hint=Join-Path $scope 'worker-hint.json'
 while(-not [IO.File]::Exists($hint) -and $clock.Elapsed.TotalSeconds -lt 10){Start-Sleep -Milliseconds 10}
 if(-not [IO.File]::Exists($hint)){throw 'OWN_TASK_INSTANCE_HINT_TIMEOUT'}
 $instance.Refresh();$workerHint=[IO.File]::ReadAllText($hint)|ConvertFrom-Json
 # A hint is observation only, NOT an authorized peer or source-code proof.
 $originalWorker=[Diagnostics.Process]::GetProcessById($workerHint.pid);$null=$originalWorker.Handle
 $binding=[GmgnRepairProcessBinding]::Read($originalWorker)
 $usedArguments=if($Case -ceq 'CommandMismatch'){$arguments+' -OWN_TAMPERED'}else{$arguments}
 $usedSid=if($Case -ceq 'SidMismatch'){'S-1-5-18'}else{$sid}
 $action=$null;$code=''
 $expectedGuid=if($Case -ceq 'InstanceSwitch'){'{00000000-0000-0000-0000-000000000001}'}else{$savedGuid}
 $expectedEngine=if($Case -ceq 'EngineSwitch'){$PID}else{$savedEngine}
 try{
  if($Case -cin @('CommandMismatch','SidMismatch')){$action=[GmgnScheduledActionIdentity]::new($savedEngine,$usedSid,$exe,$usedArguments,$earliest)}
  else{$action=Bind-GmgnSupervisorTaskAction $instance $task $expectedGuid $expectedEngine $sid $exe $arguments $repo $earliest}
 }catch{
  $cause=$_.Exception;while($cause.InnerException){$cause=$cause.InnerException};$code=$cause.Message
 }
 if($Case -cin @('CommandMismatch','SidMismatch','InstanceSwitch','EngineSwitch')){
  $expected=switch($Case){'CommandMismatch'{'GMGN_ACTION_COMMAND_MISMATCH'};'SidMismatch'{'GMGN_ACTION_OWNER_MISMATCH'};default{'GMGN_ACTION_SCHEDULER_INSTANCE_CHANGED'}}
  if($action -or $code -cne $expected){throw ('OWN_ACTION_WRONG_REJECTION_'+$code)}
 }else{
  if(-not $action -or $code){throw ('OWN_ACTION_NOT_ADMITTED_'+$code)}
  $instance.Refresh();$currentGuid=$instance.InstanceGuid;$currentEngine=[int]$instance.EnginePID
  if($currentGuid -cne $savedGuid -or $currentEngine -ne $savedEngine){$action.Abort();$code='GMGN_ACTION_SCHEDULER_INSTANCE_CHANGED'}
  elseif($Case -ceq 'PeerMismatch'){
   try{$action.CheckPeerPid($PID)}catch{$cause=$_.Exception;while($cause.InnerException){$cause=$cause.InnerException};$code=$cause.Message}
   if($code -cne 'GMGN_ACTION_PIPE_PEER_MISMATCH' -or $action.State -cne 'FAULT'){throw 'OWN_ACTION_PEER_NOT_REJECTED'}
  }elseif($Case -ceq 'EarlyExit'){
   [IO.File]::WriteAllText((Join-Path $scope 'worker-exit'),'OWN_EARLY_EXIT')
   if(-not $originalWorker.WaitForExit(5000)){throw 'OWN_ACTION_EARLY_EXIT_NOT_OBSERVED'}
   try{$action.Check()}catch{$cause=$_.Exception;while($cause.InnerException){$cause=$cause.InnerException};$code=$cause.Message}
   if($code -cne 'GMGN_ACTION_ORIGINAL_ENDED' -or $action.State -cne 'FAULT'){throw 'OWN_ACTION_EARLY_EXIT_ADMITTED'}
  }else{
   $action.CheckPeerPid($savedEngine);$action.Check();$native=$action.ReadNativeBinding()
   foreach($i in 0..3){if($native[$i] -cne $binding[$i]){throw 'OWN_ACTION_HINT_NOT_SCHEDULER_ACTION'}}
  }
 }
 $engineError='';$engineBinding=$null
 try{$engine=[Diagnostics.Process]::GetProcessById($instance.EnginePID);$null=$engine.Handle;$engineBinding=[GmgnRepairProcessBinding]::Read($engine)}catch{$engineError='OWN_ENGINE_QUERY_UNAVAILABLE'}
 $children=[GmgnRepairProcessBinding]::Children($instance.EnginePID)
 [IO.File]::WriteAllText((Join-Path $scope 'worker-exit'),'OWN_COOPERATIVE_EXIT')
 if(-not $originalWorker.WaitForExit(5000) -or $originalWorker.ExitCode -ne 0){throw 'OWN_TASK_INSTANCE_WORKER_EXIT_UNCONFIRMED'}
 $clock.Restart();while($task.GetInstances(0).Count -and $clock.Elapsed.TotalSeconds -lt 5){Start-Sleep -Milliseconds 10}
 if($task.GetInstances(0).Count){throw 'OWN_TASK_INSTANCE_NOT_FINISHED'}
 RemoveOwnActionTask;$created=$false
 [pscustomobject]@{kind='SCHEDULED_ACTION_NATIVE_IDENTITY';case=$Case;pass=$true;code=$code;instance_guid=$savedGuid;engine_pid=$savedEngine;worker_native=$binding;worker_native_exit=0;provider_calls=0;pipe_verified=$false;production_verified=$false;source_sha256=(Get-FileHash -LiteralPath (Join-Path $repo 'TOOLS\GmgnScheduledActionIdentity.cs')).Hash.ToLowerInvariant()}|ConvertTo-Json -Depth 4 -Compress
}finally{
 if($created){
  [IO.File]::WriteAllText((Join-Path $scope 'worker-exit'),'OWN_FAILURE_COOPERATIVE_EXIT')
  $clock=[Diagnostics.Stopwatch]::StartNew();while($task.GetInstances(0).Count -and $clock.Elapsed.TotalSeconds -lt 25){Start-Sleep -Milliseconds 10}
  if($task.GetInstances(0).Count){throw 'OWN_TASK_INSTANCE_RETAINED'}
  RemoveOwnActionTask
 }
 if($engine){$engine.Dispose()};if($originalWorker){$originalWorker.Dispose()};$pin.Dispose()
}
