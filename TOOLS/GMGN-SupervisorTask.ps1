# Task contract only. Does not authenticate a pipe peer or admit retirement.
function Get-GmgnTaskSid([string]$Value){
 if($Value -cmatch '\AS-[0-9-]+\z'){return $Value}
 return [Security.Principal.NTAccount]::new($Value).Translate([Security.Principal.SecurityIdentifier]).Value
}
function New-GmgnSupervisorTaskDefinition {
 param($Service,[string]$OwnerSid,[string]$Executable,[string]$Arguments,[string]$WorkingDirectory)
 if($OwnerSid -cne [Security.Principal.WindowsIdentity]::GetCurrent().User.Value -or
  $Executable -cne (Join-Path $PSHOME 'powershell.exe') -or
  $Arguments.Contains('$(') -or $Arguments.Contains([char]0) -or
  [IO.Path]::GetFullPath($WorkingDirectory) -cne $WorkingDirectory){throw 'GMGN_TASK_CONTRACT_INPUT'}
 $definition=$Service.NewTask(0)
 $definition.RegistrationInfo.Description='IMC source-closed read supervisor; no trading authority.'
 $definition.Principal.UserId=$OwnerSid;$definition.Principal.LogonType=3;$definition.Principal.RunLevel=0
 $settings=$definition.Settings
 $settings.Enabled=$true;$settings.Hidden=$true;$settings.AllowDemandStart=$true
 $settings.MultipleInstances=2;$settings.DisallowStartIfOnBatteries=$false;$settings.StopIfGoingOnBatteries=$false
 $settings.RunOnlyIfIdle=$false;$settings.RunOnlyIfNetworkAvailable=$false;$settings.StartWhenAvailable=$false
 $settings.AllowHardTerminate=$false;$settings.WakeToRun=$false;$settings.ExecutionTimeLimit='PT0S'
 # Leave RestartOnFailure absent: an explicit Count=0 is invalid Task XML.
 $settings.DeleteExpiredTaskAfter=''
 $settings.IdleSettings.IdleDuration='PT10M';$settings.IdleSettings.WaitTimeout='PT1H'
 $settings.IdleSettings.StopOnIdleEnd=$false;$settings.IdleSettings.RestartOnIdle=$false
 $trigger=$definition.Triggers.Create(9)
 $trigger.Id='OwnerLogon';$trigger.Enabled=$true;$trigger.UserId=$OwnerSid;$trigger.Delay='PT0S'
 $trigger.StartBoundary='';$trigger.EndBoundary='';$trigger.ExecutionTimeLimit='PT0S'
 $trigger.Repetition.Interval='';$trigger.Repetition.Duration='';$trigger.Repetition.StopAtDurationEnd=$false
 $action=$definition.Actions.Create(0);$action.Id='VerifiedBootstrap'
 $action.Path=$Executable;$action.Arguments=$Arguments;$action.WorkingDirectory=$WorkingDirectory
 return $definition
}
function Assert-GmgnSupervisorTaskDefinition {
 param($Definition,[string]$OwnerSid,[string]$Executable,[string]$Arguments,[string]$WorkingDirectory)
 if($Arguments.Contains('$(') -or $Arguments.Contains([char]0)){throw 'GMGN_TASK_ARGUMENT_SUBSTITUTION'}
 if((Get-GmgnTaskSid $Definition.Principal.UserId) -cne $OwnerSid -or
  $Definition.Principal.LogonType -ne 3 -or $Definition.Principal.RunLevel -ne 0 -or
  $Definition.Actions.Count -ne 1 -or $Definition.Triggers.Count -ne 1){throw 'GMGN_TASK_PRINCIPAL_OR_COUNT'}
 $action=$Definition.Actions.Item(1)
 if($action.Type -ne 0 -or $action.Id -cne 'VerifiedBootstrap' -or $action.Path -cne $Executable -or
  $action.Arguments -cne $Arguments -or $action.WorkingDirectory -cne $WorkingDirectory){throw 'GMGN_TASK_ACTION'}
 $trigger=$Definition.Triggers.Item(1)
 if($trigger.Type -ne 9 -or $trigger.Id -cne 'OwnerLogon' -or -not $trigger.Enabled -or
  (Get-GmgnTaskSid $trigger.UserId) -cne $OwnerSid -or ($trigger.Delay -and $trigger.Delay -cne 'PT0S') -or
  $trigger.StartBoundary -or $trigger.EndBoundary -or $trigger.ExecutionTimeLimit -cne 'PT0S' -or
  $trigger.Repetition.Interval -or $trigger.Repetition.Duration -or $trigger.Repetition.StopAtDurationEnd){throw 'GMGN_TASK_TRIGGER'}
 $s=$Definition.Settings
 $expected=@{Enabled=$true;Hidden=$true;AllowDemandStart=$true;MultipleInstances=2;
  DisallowStartIfOnBatteries=$false;StopIfGoingOnBatteries=$false;RunOnlyIfIdle=$false;
  RunOnlyIfNetworkAvailable=$false;StartWhenAvailable=$false;AllowHardTerminate=$false;
  WakeToRun=$false;ExecutionTimeLimit='PT0S';RestartCount=0;RestartInterval=$null;Priority=7}
 foreach($name in $expected.Keys){if($s.$name -cne $expected[$name]){throw 'GMGN_TASK_SETTINGS'}}
 if($s.IdleSettings.IdleDuration -cne 'PT10M' -or $s.IdleSettings.WaitTimeout -cne 'PT1H' -or
  $s.IdleSettings.StopOnIdleEnd -or $s.IdleSettings.RestartOnIdle){throw 'GMGN_TASK_IDLE_SETTINGS'}
 if($s.NetworkSettings.Id -or $s.NetworkSettings.Name){throw 'GMGN_TASK_NETWORK_SETTINGS'}
 if($s.DeleteExpiredTaskAfter){throw 'GMGN_TASK_DELETE_SETTINGS'}
 # Reject additional scheduler features rather than accepting unexamined
 # settings introduced through XML or a later task-schema version.
 $xml=[xml]$Definition.XmlText
 if($xml.DocumentElement.LocalName -cne 'Task' -or $xml.DocumentElement.GetAttribute('version') -cne '1.2'){
  throw 'GMGN_TASK_SCHEMA'
 }
 $allowed=@('MultipleInstancesPolicy','DisallowStartIfOnBatteries','StopIfGoingOnBatteries',
  'AllowHardTerminate','StartWhenAvailable','RunOnlyIfNetworkAvailable','IdleSettings','AllowStartOnDemand',
  'Enabled','Hidden','RunOnlyIfIdle','WakeToRun','ExecutionTimeLimit','Priority')
 foreach($node in $xml.DocumentElement.ChildNodes){
  if($node.LocalName -cnotin @('RegistrationInfo','Triggers','Principals','Settings','Actions')){throw 'GMGN_TASK_XML_FEATURE'}
  if($node.LocalName -ceq 'Settings'){
   foreach($setting in $node.ChildNodes){if($setting.LocalName -cnotin $allowed){throw 'GMGN_TASK_XML_FEATURE'}}
  }
 }
}
function Register-GmgnSupervisorTask {
 param($Folder,[string]$Name,$Definition,[string]$OwnerSid,[string]$Executable,[string]$Arguments,[string]$WorkingDirectory)
 if($Name -cnotin @('IMC-ReadSupervisor','IMC-AdvisorReadOnly') -and $Name -cnotmatch '\AIMC-READ-FIXTURE-[0-9a-f]{32}\z'){throw 'GMGN_TASK_NAME'}
 Assert-GmgnSupervisorTaskDefinition $Definition $OwnerSid $Executable $Arguments $WorkingDirectory
 $absent=$false
 try{$null=$Folder.GetTask($Name)}catch{
  $cause=$_.Exception;while($cause.InnerException){$cause=$cause.InnerException}
  $absent=($cause.HResult -band 65535) -eq 2
 }
 if(-not $absent){throw 'GMGN_TASK_EXISTS_OR_UNREADABLE'}
 # TASK_CREATE only. No password, elevation, existing-task update or ACL repair.
 $task=$Folder.RegisterTaskDefinition($Name,$Definition,2,$OwnerSid,$null,3,$null)
 try{Assert-GmgnSupervisorTaskDefinition $task.Definition $OwnerSid $Executable $Arguments $WorkingDirectory}catch{
  # Keep ownership of the successfully created object reviewable on failure.
  $_.Exception.Data['GmgnCreatedTask']=$task;throw
 }
 return $task
}
function Start-GmgnSupervisorTask {
 param($Task,[string]$OwnerSid,[string]$Executable,[string]$Arguments,[string]$WorkingDirectory)
 Assert-GmgnSupervisorTaskDefinition $Task.Definition $OwnerSid $Executable $Arguments $WorkingDirectory
 if($Task.GetInstances(0).Count -ne 0){throw 'GMGN_TASK_ALREADY_RUNNING'}
 # Zero may mean the running instance is hidden by Windows security context.
 # It never proves absence: IgnoreNew is scheduler-enforced, and callers must
 # bind the original Run result and native process before admitting any work.
 # Run arguments cannot substitute any action text; EnginePID is not peer proof.
 return $Task.Run($null)
}
function Bind-GmgnSupervisorTaskAction {
 param($Instance,$Task,[string]$OriginalInstanceGuid,[int]$OriginalEnginePid,
  [string]$OwnerSid,[string]$Executable,[string]$Arguments,[string]$WorkingDirectory,[string]$EarliestBirthUtc)
 $identity=$null
 try{
  if(-not [Runtime.InteropServices.Marshal]::IsComObject($Instance) -or
   -not [Runtime.InteropServices.Marshal]::IsComObject($Task) -or
   $OriginalEnginePid -le 0 -or $OriginalInstanceGuid -cnotmatch '\A\{[0-9A-Fa-f-]{36}\}\z'){
   throw 'GMGN_ACTION_SCHEDULER_INSTANCE_INVALID'
  }
  Assert-GmgnSupervisorTaskDefinition $Task.Definition $OwnerSid $Executable $Arguments $WorkingDirectory
  $Instance.Refresh()
  if($Instance.InstanceGuid -cne $OriginalInstanceGuid -or $Instance.EnginePID -ne $OriginalEnginePid){throw 'GMGN_ACTION_SCHEDULER_INSTANCE_CHANGED'}
  $nativeType='GmgnScheduledActionIdentity' -as [type]
  if(-not $nativeType -or -not $nativeType.IsSealed){throw 'GMGN_ACTION_NATIVE_TYPE_REQUIRED'}
  # This type must originate from the caller's held, verified compilation.
  # The bootstrap owns that provenance contract; no dynamic source loading.
  $identity=[Activator]::CreateInstance($nativeType,[object[]]@($OriginalEnginePid,$OwnerSid,$Executable,$Arguments,$EarliestBirthUtc))
  $Instance.Refresh()
  if($Instance.InstanceGuid -cne $OriginalInstanceGuid -or $Instance.EnginePID -ne $OriginalEnginePid){throw 'GMGN_ACTION_SCHEDULER_INSTANCE_CHANGED'}
  Assert-GmgnSupervisorTaskDefinition $Task.Definition $OwnerSid $Executable $Arguments $WorkingDirectory
  $identity.Check();return $identity
 }catch{if($identity){$identity.Abort()};throw}
}
