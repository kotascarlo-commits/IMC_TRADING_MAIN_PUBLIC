$ErrorActionPreference='Stop';$repo=Split-Path -Parent $PSScriptRoot
. (Join-Path $repo 'TOOLS\GMGN-SupervisorTask.ps1')
$service=New-Object -ComObject Schedule.Service;$service.Connect();$folder=$service.GetFolder('\')
$scope=Join-Path (Join-Path $repo '.local-tools') ('task-contract-native-'+[guid]::NewGuid().ToString('N'));$null=[IO.Directory]::CreateDirectory($scope)
$worker=Join-Path $scope 'worker.ps1'
[IO.File]::WriteAllText($worker,'param([string]$Scope);[IO.File]::WriteAllText((Join-Path $Scope "worker-ran"),"OWN_NO_PROVIDER");exit 0',[Text.UTF8Encoding]::new($false))
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value;$exe=Join-Path $PSHOME 'powershell.exe'
$arguments='-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+$worker+'" -Scope "'+$scope+'"'
$name='IMC-READ-FIXTURE-'+[guid]::NewGuid().ToString('N');$created=$false;$task=$null
function RemoveOwnTask {
 $current=$folder.GetTask($name)
 Assert-GmgnSupervisorTaskDefinition $current.Definition $sid $exe $arguments $repo
 if($current.GetInstances(0).Count){throw 'OWN_TASK_CLEANUP_CONTRACT_CHANGED'}
 $folder.DeleteTask($name,0)
 $absent=$false;try{$null=$folder.GetTask($name)}catch{$cause=$_.Exception;while($cause.InnerException){$cause=$cause.InnerException};$absent=($cause.HResult -band 65535) -eq 2}
 if(-not $absent){throw 'OWN_TASK_CLEANUP_ABSENCE_UNCONFIRMED'}
}
try{
 $definition=New-GmgnSupervisorTaskDefinition $service $sid $exe $arguments $repo
 Assert-GmgnSupervisorTaskDefinition $definition $sid $exe $arguments $repo
$fields=@('Action','Trigger','Principal','Settings','Substitution','ExtraAction','ExtraTrigger','Schema','Restart')
 foreach($field in $fields){
  $bad=New-GmgnSupervisorTaskDefinition $service $sid $exe $arguments $repo
  switch($field){
   'Action'{$bad.Actions.Item(1).Arguments+=' -OWN_TAMPERED'}
   'Trigger'{$bad.Triggers.Item(1).Delay='PT1M'}
   'Principal'{$bad.Principal.RunLevel=1}
   'Settings'{$bad.Settings.AllowHardTerminate=$true}
   'ExtraAction'{$null=$bad.Actions.Create(0)}
   'ExtraTrigger'{$null=$bad.Triggers.Create(9)}
   'Schema'{$bad.Settings.Compatibility=4}
   'Restart'{$bad.Settings.RestartInterval='PT1M';$bad.Settings.RestartCount=1}
  }
  $checkArgs=if($field -ceq 'Substitution'){'$(Arg0)'}else{$arguments}
  $expected=switch($field){'Action'{'GMGN_TASK_ACTION'};'Trigger'{'GMGN_TASK_TRIGGER'};'Principal'{'GMGN_TASK_PRINCIPAL_OR_COUNT'};'Settings'{'GMGN_TASK_SETTINGS'};'Substitution'{'GMGN_TASK_ARGUMENT_SUBSTITUTION'};'ExtraAction'{'GMGN_TASK_PRINCIPAL_OR_COUNT'};'ExtraTrigger'{'GMGN_TASK_PRINCIPAL_OR_COUNT'};'Schema'{'GMGN_TASK_SCHEMA'};'Restart'{'GMGN_TASK_SETTINGS'}}
  $code='';try{Assert-GmgnSupervisorTaskDefinition $bad $sid $exe $checkArgs $repo}catch{$code=$_.Exception.Message}
  if($code -cne $expected){throw ('OWN_TASK_WRONG_TAMPER_REJECTION_'+$field+'_'+$code)}
 }
 try{$task=Register-GmgnSupervisorTask $folder $name $definition $sid $exe $arguments $repo;$created=$true}catch{
  if($_.Exception.Data.Contains('GmgnCreatedTask')){$task=$_.Exception.Data['GmgnCreatedTask'];$created=$true};throw
 }
 Assert-GmgnSupervisorTaskDefinition $folder.GetTask($name).Definition $sid $exe $arguments $repo
 $collision=$false;try{$null=Register-GmgnSupervisorTask $folder $name $definition $sid $exe $arguments $repo}catch{$collision=$_.Exception.Message -like '*GMGN_TASK_EXISTS_OR_UNREADABLE*'}
 if(-not $collision){throw 'OWN_TASK_CREATE_COLLISION_ADMITTED'}
 $instance=Start-GmgnSupervisorTask $task $sid $exe $arguments $repo
 $clock=[Diagnostics.Stopwatch]::StartNew()
 while($clock.Elapsed.TotalSeconds -lt 10){
  $task=$folder.GetTask($name)
  if([IO.File]::Exists((Join-Path $scope 'worker-ran')) -and $task.GetInstances(0).Count -eq 0 -and $task.LastTaskResult -eq 0){break}
  Start-Sleep -Milliseconds 10
 }
 if(-not [IO.File]::Exists((Join-Path $scope 'worker-ran')) -or $task.GetInstances(0).Count -or $task.LastTaskResult -ne 0){
  [pscustomobject]@{own_task=$name;result=$task.LastTaskResult;instances=$task.GetInstances(0).Count;marker=[IO.File]::Exists((Join-Path $scope 'worker-ran'))}|ConvertTo-Json -Compress
  throw 'OWN_TASK_COMPLETION_FAILED'
 }
 RemoveOwnTask;$created=$false
 [pscustomobject]@{kind='EXACT_TASK_CONTRACT_FIXTURE';pass=$true;roundtrip=$true;tamper_cases=$fields;collision_rejected=$true;provider_calls=0;production_verified=$false;helper_sha256=(Get-FileHash -LiteralPath (Join-Path $repo 'TOOLS\GMGN-SupervisorTask.ps1')).Hash.ToLowerInvariant()}|ConvertTo-Json -Compress
}finally{
 if($created){
  $clock=[Diagnostics.Stopwatch]::StartNew();while($task.GetInstances(0).Count -and $clock.Elapsed.TotalSeconds -lt 10){Start-Sleep -Milliseconds 10}
  if($task.GetInstances(0).Count){throw 'OWN_TASK_STILL_RUNNING_RETAINED'}
  RemoveOwnTask
 }
}
