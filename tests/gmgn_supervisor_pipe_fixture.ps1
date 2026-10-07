param([ValidateSet('Normal','NameCollision','ConnectTimeout','ReadTimeout','WriteTimeout','Disconnect','Oversize','WrongPeer','ChildSelfFault')][string]$Case='Normal')
$ErrorActionPreference='Stop';$repo=Split-Path -Parent $PSScriptRoot;$tools=Join-Path $repo 'TOOLS'
. (Join-Path $tools 'GMGN-SupervisorTask.ps1')
Add-Type -Path @((Join-Path $tools 'GmgnScheduledActionIdentity.cs'),(Join-Path $tools 'GmgnSupervisorPipe.cs'),(Join-Path $tools 'GmgnRepairNamespace.cs'),(Join-Path $tools 'GmgnReadDirectoryIdentity.cs'),(Join-Path $tools 'GmgnRepairProcessBinding.cs'))
$scope=Join-Path (Join-Path $repo '.local-tools') ('pipe-native-'+[guid]::NewGuid().ToString('N'));$null=[IO.Directory]::CreateDirectory($scope)
$pipeName='imc-read-bootstrap-'+[guid]::NewGuid().ToString('N')
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$server=$null;$task=$null;$action=$null;$created=$false;$code='';$workerNative=$null;$fakeCreated=$false;$fakeTask=$null;$fakeAction=$null
function Cause($ErrorRecord){$e=$ErrorRecord.Exception;while($e.InnerException){$e=$e.InnerException};return $e.Message}
function AwaitOwnOriginalExit($Identity){
 if(-not $Identity){throw 'OWN_PIPE_ORIGINAL_NOT_CAPTURED'}
 $clock=[Diagnostics.Stopwatch]::StartNew()
 while($Identity.ReadOriginalExitCode() -lt 0 -and $clock.Elapsed.TotalSeconds -lt 5){Start-Sleep -Milliseconds 10}
 if($Identity.ReadOriginalExitCode() -lt 0){throw 'OWN_PIPE_ORIGINAL_EXIT_UNCONFIRMED'}
}
function RemoveOwnPipeTask($OwnedTask,[string]$OwnedName,[string]$OwnedArguments){
 $current=$folder.GetTask($OwnedName)
 Assert-GmgnSupervisorTaskDefinition $current.Definition $sid $exe $OwnedArguments $repo
 if($current.GetInstances(0).Count){throw 'OWN_PIPE_TASK_CLEANUP_ACTIVE'}
 $folder.DeleteTask($OwnedName,0)
 $absent=$false;try{$null=$folder.GetTask($OwnedName)}catch{$cause=$_.Exception;while($cause.InnerException){$cause=$cause.InnerException};$absent=($cause.HResult -band 65535) -eq 2}
 if(-not $absent){throw 'OWN_PIPE_TASK_CLEANUP_UNCONFIRMED'}
}
try{
 $timeout=if($Case -ceq 'ConnectTimeout'){250}else{15000}
 $server=[GmgnSupervisorPipe]::new($pipeName,$sid,$true,$timeout)
 if($Case -ceq 'NameCollision'){
  try{$unexpected=[GmgnSupervisorPipe]::new($pipeName,$sid,$true,1000)}catch{$code=Cause $_}
  if($code -cne 'GMGN_PIPE_NATIVE_OPEN'){throw 'OWN_PIPE_COLLISION_NOT_BLOCKED'}
 }elseif($Case -ceq 'ConnectTimeout'){
  try{$server.Accept()}catch{$code=Cause $_}
  if($code -cne 'GMGN_PIPE_DEADLINE' -or $server.State -cne 'FAULT' -or $server.CleanupFault){throw 'OWN_PIPE_CONNECT_TIMEOUT_UNCONFIRMED'}
 }else{
  $worker=Join-Path $scope 'worker.ps1';$pipeSource=Join-Path $scope 'GmgnSupervisorPipe.cs'
  [IO.File]::Copy((Join-Path $tools 'GmgnSupervisorPipe.cs'),$pipeSource)
  $pipeHash=(Get-FileHash -LiteralPath $pipeSource).Hash.ToLowerInvariant()
  $helperHashes=@{}
  foreach($namePart in @('GmgnRepairNamespace','GmgnReadDirectoryIdentity','GmgnScheduledActionIdentity')){
   $path=Join-Path $scope ($namePart+'.cs');[IO.File]::Copy((Join-Path $tools ($namePart+'.cs')),$path)
   $helperHashes[$namePart]=(Get-FileHash -LiteralPath $path).Hash.ToLowerInvariant()
  }
  $rootNative=[GmgnRepairProcessBinding]::Read([Diagnostics.Process]::GetCurrentProcess())
  $rootDirectory=[GmgnReadDirectoryIdentity]::new($scope)
  $rootNativeEncoded=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($rootNative|ConvertTo-Json -Compress)))
  $helpersEncoded=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($helperHashes|ConvertTo-Json -Compress)))
  $workerText=@'
param([string]$Scope,[string]$PipeName,[string]$Sid,[int]$ExpectedRoot,[string]$Case,[string]$SourceHash,[string]$RootBinding,[string]$DirectoryId,[string]$HelperHashes)
$ErrorActionPreference='Stop';$stream=$null;$client=$null
try{
 $path=Join-Path $Scope 'GmgnSupervisorPipe.cs'
 $stream=[IO.FileStream]::new($path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
 $sha=[Security.Cryptography.SHA256]::Create();try{$hash=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
 if($hash -cne $SourceHash){throw 'OWN_PIPE_CHILD_SOURCE_HASH'}
 $stream.Position=0;$reader=[IO.StreamReader]::new($stream,[Text.UTF8Encoding]::new($false,$true),$true,4096,$true);try{$source=$reader.ReadToEnd()}finally{$reader.Dispose()}
 Add-Type -TypeDefinition $source
 $helperStreams=[Collections.Generic.List[object]]::new();$helperSource=[Collections.Generic.List[string]]::new()
 $hashes=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($HelperHashes))|ConvertFrom-Json
 foreach($namePart in @('GmgnRepairNamespace','GmgnReadDirectoryIdentity','GmgnScheduledActionIdentity')){
  $pin=[IO.FileStream]::new((Join-Path $Scope ($namePart+'.cs')),[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read);$helperStreams.Add($pin)
  $sha=[Security.Cryptography.SHA256]::Create();try{$hash=[BitConverter]::ToString($sha.ComputeHash($pin)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
  if($hash -cne $hashes.$namePart){throw 'OWN_PIPE_CHILD_HELPER_SOURCE_CHANGED'}
  $pin.Position=0;$r=[IO.StreamReader]::new($pin,[Text.UTF8Encoding]::new($false,$true),$true,4096,$true);try{$helperSource.Add($r.ReadToEnd())}finally{$r.Dispose()}
 }
 $compiler=[Microsoft.CSharp.CSharpCodeProvider]::new();$parameters=[CodeDom.Compiler.CompilerParameters]::new();$parameters.GenerateInMemory=$true
 foreach($reference in @('System.dll','System.Core.dll')){$null=$parameters.ReferencedAssemblies.Add($reference)}
 try{$compiled=$compiler.CompileAssemblyFromSource($parameters,$helperSource.ToArray())}finally{$compiler.Dispose()}
 if($compiled.Errors.HasErrors){throw 'OWN_PIPE_CHILD_HELPER_COMPILE'}
 $nativeRoot=[string[]]([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($RootBinding))|ConvertFrom-Json)
 $rootDirectory=[GmgnReadDirectoryIdentity]::new($Scope)
 $originalRoot=[GmgnScheduledActionIdentity]::BindExpectedPeer($nativeRoot,$Sid,$rootDirectory,$DirectoryId)
 if($Case -ceq 'WrongPeer'){
  [IO.File]::WriteAllText((Join-Path $Scope 'expected-peer-ready'),[string]$PID)
  $clock=[Diagnostics.Stopwatch]::StartNew();while(-not [IO.File]::Exists((Join-Path $Scope 'expected-peer-exit')) -and $clock.Elapsed.TotalSeconds -lt 20){Start-Sleep -Milliseconds 10}
  exit 0
 }
 $client=[GmgnSupervisorPipe]::new($PipeName,$Sid,$false,15000)
 $peer=$client.ReadKernelPeerPid();$originalRoot.CheckPeerPid($peer)
 if($peer -ne $ExpectedRoot -or [string]$peer -cne $nativeRoot[0]){throw 'OWN_PIPE_CHILD_ROOT_PID_MISMATCH'}
 [IO.File]::WriteAllText((Join-Path $Scope 'child-peer.json'),(@{peer=$peer;pid=$PID}|ConvertTo-Json -Compress))
 switch($Case){
  'ReadTimeout'{Start-Sleep -Milliseconds 16000}
  'WriteTimeout'{Start-Sleep -Milliseconds 16000}
  'Disconnect'{}
  'Oversize'{
   # OWN only: inject one invalid message through the private transport stream.
   $inner=$client.GetType().GetField('stream',[Reflection.BindingFlags]'Instance,NonPublic').GetValue($client)
   $bytes=[byte[]]::new(32769);$inner.Write($bytes,0,$bytes.Length)
  }
  'ChildSelfFault'{throw 'OWN_PIPE_CHILD_SELF_FAULT'}
  default{
   $client.WriteMessage([Text.Encoding]::UTF8.GetBytes('OWN_HELLO'))
   $answer=[Text.Encoding]::UTF8.GetString($client.ReadMessage())
   if($answer -cne 'OWN_ACK'){throw 'OWN_PIPE_ACK_INVALID'}
   $originalRoot.CheckPeerPid($client.ReadKernelPeerPid())
  }
 }
}catch{
 $e=$_.Exception;while($e.InnerException){$e=$e.InnerException};$code=$e.Message
 if($code -cnotmatch '\A(?:GMGN|OWN)_[A-Z0-9_]+\z'){$code='OWN_PIPE_CHILD_INTERNAL'}
 [IO.File]::WriteAllText((Join-Path $Scope 'child-error.json'),(@{code=$code}|ConvertTo-Json -Compress))
 if($Case -cne 'ChildSelfFault'){exit 1}
}finally{if($client){$client.Dispose()};if($stream){$stream.Dispose()};if($helperStreams){foreach($pin in $helperStreams){$pin.Dispose()}}}
exit 0
'@
  [IO.File]::WriteAllText($worker,$workerText,[Text.UTF8Encoding]::new($false))
  $pins=[Collections.Generic.List[object]]::new()
  foreach($p in @($worker,$pipeSource)+@($helperHashes.Keys|ForEach-Object {Join-Path $scope ($_+'.cs')})){$pins.Add([IO.FileStream]::new($p,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read))}
  $workerHash=(Get-FileHash -LiteralPath $worker).Hash.ToLowerInvariant()
  function BuildOwnBootstrap([string]$WorkerCase){
   return '$ErrorActionPreference="Stop";$s=[IO.FileStream]::new("'+$worker+'",[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read);$h=[Security.Cryptography.SHA256]::Create();try{$v=[BitConverter]::ToString($h.ComputeHash($s)).Replace("-","").ToLowerInvariant()}finally{$h.Dispose()};if($v -cne "'+$workerHash+'"){exit 125};$s.Position=0;$r=[IO.StreamReader]::new($s);& ([scriptblock]::Create($r.ReadToEnd())) -Scope "'+$scope+'" -PipeName '+$pipeName+' -Sid '+$sid+' -ExpectedRoot '+$PID+' -Case '+$WorkerCase+' -SourceHash '+$pipeHash+' -RootBinding '+$rootNativeEncoded+' -DirectoryId '+$rootDirectory.Identity+' -HelperHashes '+$helpersEncoded
  }
  $launch=BuildOwnBootstrap $Case
  $args='-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -EncodedCommand '+[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($launch))
  $service=New-Object -ComObject Schedule.Service;$service.Connect();$folder=$service.GetFolder('\')
  $name='IMC-READ-FIXTURE-'+[guid]::NewGuid().ToString('N');$exe=Join-Path $PSHOME 'powershell.exe'
  $definition=New-GmgnSupervisorTaskDefinition $service $sid $exe $args $repo
  try{$task=Register-GmgnSupervisorTask $folder $name $definition $sid $exe $args $repo;$created=$true}catch{if($_.Exception.Data.Contains('GmgnCreatedTask')){$task=$_.Exception.Data['GmgnCreatedTask'];$created=$true};throw}
  $earliest=[DateTime]::UtcNow.ToString('o');$instance=Start-GmgnSupervisorTask $task $sid $exe $args $repo
  $savedGuid=$instance.InstanceGuid;$instance.Refresh();$savedEngine=[int]$instance.EnginePID
  $action=Bind-GmgnSupervisorTaskAction $instance $task $savedGuid $savedEngine $sid $exe $args $repo $earliest
  $workerNative=$action.ReadNativeBinding()
  if($Case -ceq 'WrongPeer'){
   $fakeArgs='-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -EncodedCommand '+[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes((BuildOwnBootstrap 'Normal')))
   $fakeName='IMC-READ-FIXTURE-'+[guid]::NewGuid().ToString('N')
   $fakeDefinition=New-GmgnSupervisorTaskDefinition $service $sid $exe $fakeArgs $repo
   try{$fakeTask=Register-GmgnSupervisorTask $folder $fakeName $fakeDefinition $sid $exe $fakeArgs $repo;$fakeCreated=$true}catch{if($_.Exception.Data.Contains('GmgnCreatedTask')){$fakeTask=$_.Exception.Data['GmgnCreatedTask'];$fakeCreated=$true};throw}
   $fakeEarliest=[DateTime]::UtcNow.ToString('o');$fakeInstance=Start-GmgnSupervisorTask $fakeTask $sid $exe $fakeArgs $repo
   $fakeGuid=$fakeInstance.InstanceGuid;$fakeInstance.Refresh();$fakeEngine=[int]$fakeInstance.EnginePID
   $fakeAction=Bind-GmgnSupervisorTaskAction $fakeInstance $fakeTask $fakeGuid $fakeEngine $sid $exe $fakeArgs $repo $fakeEarliest
  }
  $server.Accept();$peer=$server.ReadKernelPeerPid()
  try{
   if($Case -ceq 'WrongPeer'){$fakeAction.CheckPeerPid($peer)}
   $action.CheckPeerPid($peer)
   if($Case -ceq 'ReadTimeout'){
    # Shared deadline applies to Accept and all subsequent message operations.
    $null=$server.ReadMessage()
   }elseif($Case -ceq 'WriteTimeout'){
    foreach($index in 1..4){$server.WriteMessage([byte[]]::new(32768))}
   }else{
    $message=[Text.Encoding]::UTF8.GetString($server.ReadMessage())
    if($message -cne 'OWN_HELLO'){throw 'OWN_PIPE_MESSAGE_WRONG'}
    $action.Check();$server.WriteMessage([Text.Encoding]::UTF8.GetBytes('OWN_ACK'))
   }
  }catch{$code=Cause $_;$server.Abort()}
  $expected=switch($Case){'Normal'{''};'WrongPeer'{'GMGN_ACTION_PIPE_PEER_MISMATCH'};'ReadTimeout'{'GMGN_PIPE_DEADLINE'};'WriteTimeout'{'GMGN_PIPE_DEADLINE'};'Oversize'{'GMGN_PIPE_MESSAGE_SIZE'};default{'GMGN_PIPE_DISCONNECTED'}}
  if($code -cne $expected){throw ('OWN_PIPE_WRONG_RESULT_'+$code)}
  [IO.File]::WriteAllText((Join-Path $scope 'expected-peer-exit'),'OWN_COOPERATIVE_EXIT')
  $clock=[Diagnostics.Stopwatch]::StartNew();while($task.GetInstances(0).Count -and $clock.Elapsed.TotalSeconds -lt 20){Start-Sleep -Milliseconds 10}
  if($task.GetInstances(0).Count){throw 'OWN_PIPE_CHILD_DID_NOT_FINISH'}
  AwaitOwnOriginalExit $action
  RemoveOwnPipeTask $task $name $args;$created=$false
 }
 $server.Dispose();if($server.CleanupFault){throw 'OWN_PIPE_CLEANUP_FAULT'}
 [pscustomobject]@{kind='OWN_NATIVE_PIPE_TRANSPORT';case=$Case;pass=$true;code=$code;worker_native=$workerNative;provider_calls=0;production_verified=$false;authenticated=$false;pin_transfer_verified=$false;pipe_sha256=(Get-FileHash -LiteralPath (Join-Path $tools 'GmgnSupervisorPipe.cs')).Hash.ToLowerInvariant()}|ConvertTo-Json -Compress
}catch{
 $causeCode=Cause $_
 if($causeCode -cnotmatch '\A(?:GMGN|OWN)_[A-Z0-9_]+\z'){$causeCode='OWN_PIPE_ROOT_INTERNAL'}
 [IO.File]::WriteAllText((Join-Path $scope 'root-fault.json'),(@{code=$causeCode}|ConvertTo-Json -Compress))
 throw
}finally{
 if($server){$server.Dispose()}
 if($fakeCreated){
  $clock=[Diagnostics.Stopwatch]::StartNew();while($fakeTask.GetInstances(0).Count -and $clock.Elapsed.TotalSeconds -lt 20){Start-Sleep -Milliseconds 10}
  if($fakeTask.GetInstances(0).Count){throw 'OWN_PIPE_FAKE_PEER_RETAINED'}
  AwaitOwnOriginalExit $fakeAction
  RemoveOwnPipeTask $fakeTask $fakeName $fakeArgs
 }
 if($created){
  [IO.File]::WriteAllText((Join-Path $scope 'expected-peer-exit'),'OWN_FAILURE_COOPERATIVE_EXIT')
  $clock=[Diagnostics.Stopwatch]::StartNew();while($task.GetInstances(0).Count -and $clock.Elapsed.TotalSeconds -lt 25){Start-Sleep -Milliseconds 10}
  if($task.GetInstances(0).Count){throw 'OWN_PIPE_TASK_RETAINED'}
  AwaitOwnOriginalExit $action
  RemoveOwnPipeTask $task $name $args
 }
 if($pins){foreach($pin in $pins){$pin.Dispose()}}
}
