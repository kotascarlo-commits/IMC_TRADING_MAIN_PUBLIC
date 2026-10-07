param([ValidateSet('Normal','PeerCommandMismatch','PeerExit','Client','Restart','StaleBinding','ForeignExecutable','PlanTaskMismatch')][string]$Case='Normal')
$ErrorActionPreference='Stop';$repo=Split-Path -Parent $PSScriptRoot;$tools=Join-Path $repo 'TOOLS'
Add-Type -Path @('GmgnRepairNamespace.cs','GmgnImmutableSources.cs','GmgnReadDirectoryIdentity.cs','GmgnScheduledActionIdentity.cs','ImcAdvisorHostContext.cs'|ForEach-Object {Join-Path $tools $_})
. (Join-Path $tools 'GMGN-SupervisorTask.ps1')
$paths=@('GmgnRepairNamespace.cs','GmgnImmutableSources.cs','GmgnReadDirectoryIdentity.cs','GmgnScheduledActionIdentity.cs',
 'ImcAdvisorHostContext.cs','IMC-AdvisorBroker.ps1','IMC-AdvisorTaskBootstrap.ps1','GMGN-SupervisorTask.ps1')|ForEach-Object {Join-Path $tools $_}
$paths+=Join-Path $repo 'config\imc-advisor.json';$paths+=[GmgnImmutableSources]::AdvisorExecutable
$files=@($paths|ForEach-Object {@{path=$_;sha256=(Get-FileHash -LiteralPath $_).Hash.ToLowerInvariant()}})
$dir=Join-Path $env:LOCALAPPDATA ('Temp\imc-astra-independent-'+[guid]::NewGuid().ToString('N'))
$directory=[ImcAdvisorDirectory]::CreateNew($dir)
$name='IMC-READ-FIXTURE-'+[guid]::NewGuid().ToString('N')
$template=@'
$ErrorActionPreference='Stop'
$actionText=$MyInvocation.MyCommand.ScriptBlock.ToString()
try{
$expected='-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -EncodedCommand '+[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($actionText))
$files=ConvertFrom-Json $filesJson
$bootstrap=@($files|Where-Object {$_.path.EndsWith('\IMC-AdvisorTaskBootstrap.ps1')})[0]
$stream=[IO.FileStream]::new($bootstrap.path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
$sha=[Security.Cryptography.SHA256]::Create();try{$hash=([BitConverter]::ToString($sha.ComputeHash($stream))).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
if($hash -cne $bootstrap.sha256){throw 'OWN_ADVISOR_BOOTSTRAP_HASH'}
$stream.Position=0;$reader=[IO.StreamReader]::new($stream,[Text.UTF8Encoding]::new($false,$true),$true,4096,$true)
try{$text=$reader.ReadToEnd()}finally{$reader.Dispose()}
& ([scriptblock]::Create($text)) -ExpectedFiles $files -BrokerDir $brokerDir -TaskName $taskName -ExpectedArguments $expected
}catch{
 $cause=$_.Exception;while($cause.InnerException){$cause=$cause.InnerException}
 $code=if($cause.Message -cmatch '\A(?:GMGN|ASTRA|OWN)_[A-Z0-9_]+\z'){$cause.Message}else{'OWN_UNCLASSIFIED_BOOTSTRAP_ERROR'}
 [IO.File]::WriteAllText((Join-Path $brokerDir 'own-host-fault.json'),(@{code=$code;line=$_.InvocationInfo.ScriptLineNumber}|ConvertTo-Json -Compress))
 exit 125
}
'@
# Public fixed-path deployment data followed by an unchanged bootstrap body.
$prefix='$filesJson = '''+($files|ConvertTo-Json -Compress)+"'`n"+'$brokerDir = '''+$dir+"'`n"+'$taskName = '''+$name+"'`n"
$launch=$prefix+$template
$arguments='-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -EncodedCommand '+[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($launch))
$owner=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value;$exe=Join-Path $PSHOME 'powershell.exe'
$scope=Join-Path (Join-Path $repo '.local-tools') ('advisor-client-own-'+[guid]::NewGuid().ToString('N'));$null=[IO.Directory]::CreateDirectory($scope)
$plan=@{schema_version=1;task_name=$name;task_arguments=$arguments;executable=$exe;broker_dir=$dir;
 directory_id=$directory.Identity;owner_sid=$owner;sources=$files}
$planPath=Join-Path $scope 'own-plan.json';[IO.File]::WriteAllText($planPath,($plan|ConvertTo-Json -Compress -Depth 8),[Text.UTF8Encoding]::new($false))
$planHash=(Get-FileHash -LiteralPath $planPath).Hash.ToLowerInvariant()
function TestOwnClient([string]$ExpectedCode){
 $output=@(& (Join-Path $PSHOME 'powershell.exe') -NoProfile -NonInteractive -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'imc_advisor_client_probe.ps1') `
  -PlanPath $planPath -ExpectedHash $planHash -ExpectedCode $ExpectedCode)
 if($LASTEXITCODE -ne 0){throw 'OWN_CLIENT_PROBE_FAILED'}
 $client=($output -join [Environment]::NewLine)|ConvertFrom-Json
 if(-not $client.pass -or $client.provider_calls -ne 0 -or $client.production_verified){throw 'OWN_CLIENT_PROBE_INVALID'}
 return $client
}
$service=New-Object -ComObject Schedule.Service;$service.Connect();$folder=$service.GetFolder('\');$original=$null;$task=$null;$created=$false
try{
 $definition=New-GmgnSupervisorTaskDefinition $service $owner $exe $arguments $repo
 $task=Register-GmgnSupervisorTask $folder $name $definition $owner $exe $arguments $repo;$created=$true
 $earliest=[DateTime]::UtcNow.AddSeconds(-1).ToString('o');$instance=Start-GmgnSupervisorTask $task $owner $exe $arguments $repo;$guid=$instance.InstanceGuid
 $deadline=[Diagnostics.Stopwatch]::StartNew()
 do{$instance.Refresh();$engine=$instance.EnginePID;if($engine -gt 0){break};Start-Sleep -Milliseconds 20}while($deadline.Elapsed.TotalSeconds -lt 10)
 $original=Bind-GmgnSupervisorTaskAction $instance $task $instance.InstanceGuid $engine $owner $exe $arguments $repo $earliest
 $proofPath=Join-Path $dir 'own-host-proof.json'
 while(-not [IO.File]::Exists($proofPath) -and $deadline.Elapsed.TotalSeconds -lt 20){$original.Check();Start-Sleep -Milliseconds 20}
 $proof=Get-Content -LiteralPath $proofPath -Raw|ConvertFrom-Json
 if(($proof.native_self -join '|') -cne ($original.ReadNativeBinding() -join '|') -or $proof.directory_id -cne $directory.Identity -or
  -not $proof.pass -or $proof.provider_calls -ne 0 -or $proof.production_verified){throw 'OWN_ADVISOR_TASK_PROOF_MISMATCH'}
 $instance.Refresh()
 if($instance.InstanceGuid -cne $guid -or $instance.EnginePID -ne $engine){throw 'OWN_ADVISOR_ORIGINAL_INSTANCE_CHANGED'}
 $original.Check()
 $savedBinding=[IO.File]::ReadAllText((Join-Path $dir 'host.binding.json'))
 if($Case -cin @('Client','Restart')){
  $client=TestOwnClient 'NONE'
  if(($client.native_self -join '|') -cne ($original.ReadNativeBinding() -join '|')){throw 'OWN_CLIENT_NATIVE_BINDING_MISMATCH'}
 }
 if($Case -ceq 'ForeignExecutable'){
  $fake=$savedBinding|ConvertFrom-Json;$fake.native_self[3]='C:\Program Files\nodejs\node.exe'
  [IO.File]::WriteAllText((Join-Path $dir 'host.binding.json'),($fake|ConvertTo-Json -Compress))
  $null=TestOwnClient 'ASTRA_CLIENT_EXECUTABLE_MISMATCH'
 }
 if($Case -ceq 'PlanTaskMismatch'){
  $plan.task_arguments+=' -Unexpected';[IO.File]::WriteAllText($planPath,($plan|ConvertTo-Json -Compress -Depth 8));$planHash=(Get-FileHash $planPath).Hash.ToLowerInvariant()
  $null=TestOwnClient 'GMGN_ACTION_COMMAND_MISMATCH'
 }
 $peerArguments=if($Case -ceq 'PeerCommandMismatch'){$arguments+' -Unexpected'}else{$arguments}
 $peer=$null;$peerCode=''
 try{$peer=[GmgnScheduledActionIdentity]::BindExpectedPeerAction($original.ReadNativeBinding(),$owner,$directory,$directory.Identity,$peerArguments)}catch{
  $e=$_.Exception;while($e.InnerException){$e=$e.InnerException};$peerCode=$e.Message
 }
 if($Case -ceq 'PeerCommandMismatch'){
  if($peerCode -cne 'GMGN_ACTION_COMMAND_MISMATCH'){throw 'OWN_ADVISOR_PEER_COMMAND_NOT_BLOCKED'}
 }elseif(-not $peer -or $peerCode){throw 'OWN_ADVISOR_PEER_NOT_BOUND'}
 [IO.File]::WriteAllText((Join-Path $dir 'own-host-exit'),'OWN')
 while($original.ReadOriginalExitCode() -lt 0 -and $deadline.Elapsed.TotalSeconds -lt 45){Start-Sleep -Milliseconds 20}
 if($original.ReadOriginalExitCode() -ne 0){throw 'OWN_ADVISOR_TASK_EXIT_UNCONFIRMED'}
 if($Case -ceq 'StaleBinding'){$null=TestOwnClient 'GMGN_ACTION_ORIGINAL_ENDED'}
 if($Case -ceq 'Restart'){
  $oldNative=$original.ReadOriginalExitCode()
  [IO.File]::Delete((Join-Path $dir 'own-host-exit'));[IO.File]::Delete((Join-Path $dir 'own-host-proof.json'))
  $earliest=[DateTime]::UtcNow.AddSeconds(-1).ToString('o');$instance=Start-GmgnSupervisorTask $task $owner $exe $arguments $repo;$guid=$instance.InstanceGuid
  $instance.Refresh();$engine=$instance.EnginePID
  $original=Bind-GmgnSupervisorTaskAction $instance $task $guid $engine $owner $exe $arguments $repo $earliest
  $deadline.Restart();while(-not [IO.File]::Exists($proofPath) -and $deadline.Elapsed.TotalSeconds -lt 20){$original.Check();Start-Sleep -Milliseconds 20}
  $restartedBinding=[IO.File]::ReadAllText((Join-Path $dir 'host.binding.json'))
  if((Get-FileHash $planPath).Hash.ToLowerInvariant() -cne $planHash){throw 'OWN_RESTART_PLAN_CHANGED'}
  $client=TestOwnClient 'NONE'
  if(($client.native_self -join '|') -cne ($original.ReadNativeBinding() -join '|')){throw 'OWN_RESTART_CLIENT_BINDING_MISMATCH'}
  [IO.File]::WriteAllText((Join-Path $dir 'host.binding.json'),$savedBinding)
  $null=TestOwnClient 'GMGN_ACTION_ORIGINAL_ENDED'
  [IO.File]::WriteAllText((Join-Path $dir 'host.binding.json'),$restartedBinding)
  [IO.File]::WriteAllText((Join-Path $dir 'own-host-exit'),'OWN')
  while($original.ReadOriginalExitCode() -lt 0 -and $deadline.Elapsed.TotalSeconds -lt 45){Start-Sleep -Milliseconds 20}
  if($original.ReadOriginalExitCode() -ne 0 -or $oldNative -ne 0){throw 'OWN_RESTART_EXIT_UNCONFIRMED'}
 }
 if($Case -ceq 'PeerExit'){
  try{$peer.Check()}catch{$e=$_.Exception;while($e.InnerException){$e=$e.InnerException};$peerCode=$e.Message}
  if($peerCode -cne 'GMGN_ACTION_ORIGINAL_ENDED' -or $peer.State -cne 'FAULT'){throw 'OWN_ADVISOR_ENDED_PEER_ALLOWED'}
 }
 $proof|Add-Member case $Case;$proof|Add-Member peer_code $peerCode
 $proof|ConvertTo-Json -Compress
}catch{
 $cause=$_.Exception;while($cause.InnerException){$cause=$cause.InnerException}
 [Console]::Error.WriteLine('OWN_TASK_PRIMARY_ERROR: '+$cause.Message)
 $fault=Join-Path $dir 'own-host-fault.json';if([IO.File]::Exists($fault)){[Console]::Error.WriteLine([IO.File]::ReadAllText($fault))}
 $diagnostics=Join-Path $dir 'own-task-diagnostics.json';if([IO.File]::Exists($diagnostics)){[Console]::Error.WriteLine([IO.File]::ReadAllText($diagnostics))}
 [Console]::Error.WriteLine((@{root_guid=$instance.InstanceGuid;root_engine=$engine;direct_count=$task.GetInstances(0).Count;
  admin=([Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator))}|ConvertTo-Json -Compress))
 if(-not $original -and $engine -gt 0){
  $retained=[GmgnScheduledActionIdentity].GetField('retained',[Reflection.BindingFlags]'Static,NonPublic').GetValue($null)
  foreach($candidate in $retained){
   $flags=[Reflection.BindingFlags]'Instance,NonPublic'
   if($candidate.GetType().GetField('pid',$flags).GetValue($candidate) -eq $engine -and
    $candidate.GetType().GetField('arguments',$flags).GetValue($candidate) -ceq $arguments){$original=$candidate}
  }
 }
 throw
}finally{
 if($created){
  if(-not $original -or $original.ReadOriginalExitCode() -lt 0){throw 'OWN_ADVISOR_TASK_CLEANUP_UNCONFIRMED'}
  $current=$folder.GetTask($name);Assert-GmgnSupervisorTaskDefinition $current.Definition $owner $exe $arguments $repo
  if($current.GetInstances(0).Count -ne 0){throw 'OWN_ADVISOR_TASK_CLEANUP_ACTIVE'}
  $folder.DeleteTask($name,0)
  $absent=$false;try{$null=$folder.GetTask($name)}catch{$e=$_.Exception;while($e.InnerException){$e=$e.InnerException};$absent=($e.HResult -band 65535) -eq 2}
  if(-not $absent){throw 'OWN_ADVISOR_TASK_CLEANUP_FAILED'}
 }
}
