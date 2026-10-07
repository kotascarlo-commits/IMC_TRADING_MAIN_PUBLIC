param([ValidateSet('Normal','WrongProof','ReplayChallenge','MissingChallenge','WrongAuth',
 'SourceNormal','SourceReplay','SourceWrongRootArguments','SourceWrongRootBinding','SourceWrongSession','SourceNullContract','SourceExtraField','SourceDuplicateField','SourceWrongType',
 'SourceTaskNormal','SourceTaskReplay','SourceTaskGuid','SourceTaskEngine','SourceTaskPath','SourceTaskNotCom','SourceTaskDefinition','SourceTaskPlanFault','SourceTaskAuthFault','SourceTaskSourceFault','SourceTaskChildFault','SourceTaskNullCompletion','SourceTaskIncomplete','SourceTaskLateCheck','SourceTaskTransferFault',
 'SourceTaskWeakActor','SourceTaskActionMismatch','SourceTaskStrongSid','SourceTaskStrongExe','SourceTaskStrongAbort','SourceTaskLateDefinition','SourceTaskRegistryAbsent','SourceTaskRegistryLate')][string]$Case='Normal',
 [ValidateSet('None','Normal','Replay','ExistingTarget','WrongMac','ParentExit','AuthFault','RecoveryAcl','Concurrent')][string]$LegacyCase='None',
 [ValidateSet('None','Normal','Replay','FactoryReplay','ExistingRequest','Stale','WrongHash','WrongMac','AuthFault','TaskFault','PostPublication','GrantBoundaries')][string]$SenderCase='None',
 [ValidateSet('None','Normal','Replay','PlanAbort','Expired','PinAbortCommon','AdmissionAbortCommon','PinAbortCaptured','AdmissionAbortCaptured',
 'PinAbortCommonSecond','AdmissionAbortCommonSecond','PinAbortCapturedSecond','AdmissionAbortCapturedSecond','ProbeGuards','PinAbortAtomic','AdmissionAbortAtomic')][string]$RetireCase='None')
$ErrorActionPreference='Stop';$repo=Split-Path -Parent $PSScriptRoot;$tools=Join-Path $repo 'TOOLS'
Add-Type -AssemblyName System.Web.Extensions
. (Join-Path $tools 'GMGN-SupervisorTask.ps1')
$types=@('GmgnRepairNamespace','GmgnImmutableSources','GmgnRepairProcessBinding','GmgnReadDirectoryIdentity','GmgnScheduledActionIdentity','GmgnReadLocalAuth','GmgnReadInstallPlan','GmgnReadProofPin',
 'GmgnSupervisorPipe','GmgnSourcePinBinding','GmgnPinTransferHandshake','GmgnSupervisorSourceAdmission','GmgnHandoffCompletion','GmgnLegacyLifetimePins','GmgnLegacyCapture','GmgnLegacyRetirement','GmgnReadOwnerProof')
Add-Type -Path @(@($types|ForEach-Object {Join-Path $tools ($_+'.cs')})+@('GmgnReadInstallPlan.Warm.cs','GmgnReadInstallPlan.Root.cs','GmgnReadInstallPlan.Legacy.cs','GmgnReadInstallPlan.Sender.cs','GmgnReadInstallPlan.Retire.cs','GmgnLegacyRetirement.Auth.cs'|ForEach-Object {Join-Path $tools $_})) -ReferencedAssemblies @('System.dll','System.Core.dll','System.Web.Extensions.dll',[Management.Automation.PSObject].Assembly.Location)
$legacyPair=$null;$legacyProof=$null;$senderProof=$null
try{
if($LegacyCase -cne 'None' -or $SenderCase -cne 'None'){
 if($Case -cne 'SourceTaskNormal'){throw 'OWN_WARM_LEGACY_ROOT_CASE_REQUIRED'}
 . (Join-Path $PSScriptRoot 'gmgn_warm_legacy_pair.ps1')
 $pairCase=if($RetireCase -cne 'None'){'RetireNaturalExit'}else{$LegacyCase}
 $legacyPair=Start-OwnWarmLegacyPair $repo $pairCase
}
$scope=Join-Path (Join-Path $repo '.local-tools') ('warm-task-native-'+[guid]::NewGuid().ToString('N'));$null=[IO.Directory]::CreateDirectory($scope)
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value;$id=[guid]::NewGuid().ToString('N')
$recoveryPath=Join-Path (Join-Path $repo '.local-tools') ('gmgn-read-recovery-'+[guid]::NewGuid().ToString('N'));$recovery=[GmgnReadProofPin]::CreateNewDirectory($recoveryPath)
$authPath=Join-Path (Join-Path $repo '.local-tools') ('gmgn-read-auth-'+[guid]::NewGuid().ToString('N'));$auth=[GmgnReadLocalAuth]::CreateNew($authPath,$sid,[Text.Encoding]::ASCII.GetBytes('f'*64))
$bytes=[byte[]]::new(32);$rng=[Security.Cryptography.RandomNumberGenerator]::Create();try{$rng.GetBytes($bytes)}finally{$rng.Dispose()};$challenge=([BitConverter]::ToString($bytes)).Replace('-','').ToLowerInvariant()
$generationArguments=@{ApprovedSdkManifestPath=(Join-Path $repo '.local-tools\gmgn-approved-public-generation-23\package.json')}
if($Case -cne 'MissingChallenge'){$generationArguments.WarmActionChallenge=$challenge}
$sourceCase=$Case.StartsWith('Source');$server=$null;$rootTransfer=$null;$sourceVerified=$false;$rootReceiptVerified=$false;$rootArguments='-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+$PSCommandPath+'" -Case '+$Case
if($LegacyCase -cne 'None'){$rootArguments+=' -LegacyCase '+$LegacyCase}
if($SenderCase -cne 'None'){$rootArguments+=' -SenderCase '+$SenderCase}
if($RetireCase -cne 'None'){$rootArguments+=' -RetireCase '+$RetireCase}
# Fixture completion includes independently validating both protected proofs.
# This is not the immutable 30s pipe deadline or the 15s owner grant.
$proofDeadline=if($sourceCase){65}else{15}
$rootCase=$Case.StartsWith('SourceTask');$rootTaskVerified=$false;$taskDefinitionChanged=$false;$ownTaskDeleted=$false;$registryFailureVerified=$false
$serializer=[System.Web.Script.Serialization.JavaScriptSerializer]::new()
if($sourceCase){
 $rootNative=[GmgnRepairProcessBinding]::Read([Diagnostics.Process]::GetCurrentProcess());$wantedRoot=[string[]]$rootNative.Clone()
 if($Case -ceq 'SourceWrongRootBinding'){$wantedRoot[1]=([int]$wantedRoot[1]+1).ToString()}
 $pipeName='imc-read-bootstrap-'+[guid]::NewGuid().ToString('N');$session=[guid]::NewGuid().ToString('N')
 $handoff=[Collections.Generic.SortedDictionary[string,string]]::new([StringComparer]::Ordinal)
 $wantedArguments=if($Case -ceq 'SourceWrongRootArguments'){$rootArguments+' -Unexpected'}else{$rootArguments}
 $handoff.Add('owner_sid',$sid);$handoff.Add('pipe_name',$pipeName);$handoff.Add('root_arguments',$wantedArguments);$handoff.Add('root_native',$serializer.Serialize($wantedRoot));$handoff.Add('session_id',$session)
 if($Case -ceq 'SourceExtraField'){$handoff.Add('extra','rejected')}
 $contract=$serializer.Serialize($handoff)
 if($Case -ceq 'SourceDuplicateField'){$contract=$contract.Substring(0,$contract.Length-1)+',"owner_sid":"'+$sid+'"}'}
 if($Case -ceq 'SourceWrongType'){$contract=$contract.Replace(('"owner_sid":"'+$sid+'"'),'"owner_sid":false')}
 if($Case -ceq 'SourceNullContract'){$contract='null'}
 $generationArguments.WarmHandoff=$contract
}
if($legacyPair){
 $legacyContract=[Collections.Generic.SortedDictionary[string,string]]::new([StringComparer]::Ordinal)
 $legacyContract.Add('broker_native',$serializer.Serialize([string[]]$legacyPair.Bindings.broker));$legacyContract.Add('parent_native',$serializer.Serialize([string[]]$legacyPair.Bindings.parent))
 $legacyContract.Add('scope',$legacyPair.Scope);$legacyContract.Add('source_directory',(Join-Path $legacyPair.Scope 'legacy'))
 $generationArguments.FixtureScope=$legacyPair.Scope;$generationArguments.WarmLegacyFixture=$serializer.Serialize($legacyContract)
}
$generation=& (Join-Path $tools 'New-GMGN-ReadGeneration.ps1') @generationArguments
$manifest=Get-Content $generation.manifest -Raw|ConvertFrom-Json
$paths=[string[]](@($manifest.files|ForEach-Object {$_.path})+@($generation.manifest));$hashes=[string[]](@($manifest.files|ForEach-Object {$_.sha256})+@($generation.sha256))
$first=[GmgnImmutableSources]::new('%USERPROFILE%','C:\Program Files\nodejs\node.exe',$paths,$hashes)
function Digest([string]$value){$sha=[Security.Cryptography.SHA256]::Create();try{([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($value)))).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}}
$serializer=[System.Web.Script.Serialization.JavaScriptSerializer]::new();$fields=[Collections.Generic.SortedDictionary[string,string]]::new([StringComparer]::Ordinal)
$values=@{schema_version='1';purpose='IMC_READ_INSTALL_PLAN_V1';installation_id=$id;source1_manifest=$generation.manifest;source1_sha256=$generation.sha256;
 source1_fingerprint=$first.Fingerprint();auth_binding_sha256=(Digest ($auth.ReadPublicBinding() -join "`n"));recovery_directory=$recovery.Path;recovery_identity=$recovery.Identity;
 owner_sid=$sid;primary_plan=(Join-Path (Join-Path $scope 'primary') 'package.json');primary_plan_sha256=('a'*64)}
foreach($name in $values.Keys){$fields.Add($name,$values[$name])}
$planDir=Join-Path $scope 'plan';$null=[IO.Directory]::CreateDirectory($planDir);$planPath=Join-Path $planDir 'package.json'
[IO.File]::WriteAllText($planPath,$serializer.Serialize($fields),[Text.UTF8Encoding]::new($false));$planHash=(Get-FileHash $planPath).Hash.ToLowerInvariant()
$planPin=[GmgnImmutableSources]::new('%USERPROFILE%','C:\Program Files\nodejs\node.exe',[string[]]@($planPath),[string[]]@($planHash))
$rootArguments='-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+$PSCommandPath+'" -Case '+$Case
if($LegacyCase -cne 'None'){$rootArguments+=' -LegacyCase '+$LegacyCase}
if($SenderCase -cne 'None'){$rootArguments+=' -SenderCase '+$SenderCase}
if($RetireCase -cne 'None'){$rootArguments+=' -RetireCase '+$RetireCase}
$plan=[GmgnReadInstallPlan]::new($first,$planPin,$planPath,$planHash,$generation.manifest,$generation.sha256,$auth,$recovery,$id,$rootArguments)
$actionArguments=$plan.BuildWarmTaskAction();$proofAuth=$auth
if($Case -ceq 'WrongAuth'){
 $otherAuthPath=Join-Path (Join-Path $repo '.local-tools') ('gmgn-read-auth-'+[guid]::NewGuid().ToString('N'));$proofAuth=[GmgnReadLocalAuth]::CreateNew($otherAuthPath,$sid,[Text.Encoding]::ASCII.GetBytes('e'*64))
 $prefix='-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -EncodedCommand ';$body=[Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($actionArguments.Substring($prefix.Length)))
 $marker="`$imcWarmActionData='";$finish=$body.IndexOf("'",$marker.Length);$encoded=$body.Substring($marker.Length,$finish-$marker.Length)
 $data=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($encoded))|ConvertFrom-Json;$data.auth_binding=$serializer.Serialize($proofAuth.ReadPublicBinding())
 $changed=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($data|ConvertTo-Json -Compress)))
 $body=$body.Remove($marker.Length,$encoded.Length).Insert($marker.Length,$changed);$actionArguments=$prefix+[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($body))
}
$exe=Join-Path $PSHOME 'powershell.exe';$actionHash=Digest $actionArguments
$service=New-Object -ComObject Schedule.Service;$service.Connect();$folder=$service.GetFolder('\');$name='IMC-READ-FIXTURE-'+[guid]::NewGuid().ToString('N')
$task=$null;$action=$null;$originalProcess=$null;$originalBinding=$null;$created=$false;$cleanup=$false;$verified=$false;$code=''
function Cause($record){$e=$record.Exception;while($e.InnerException){$e=$e.InnerException};$e.Message}
function ReadOwnProtectedPacket([string]$Path){
 $stream=$null
 while(-not $stream){
  $action.Check();$first.Check();$auth.Check()
  if($clock.Elapsed.TotalSeconds -ge $proofDeadline){throw 'OWN_SOURCE_PROOF_DEADLINE'}
  try{$stream=[IO.FileStream]::new($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)}catch{
   $e=$_.Exception;while($e.InnerException){$e=$e.InnerException};if(($e.HResult -band 65535) -ne 32){throw};Start-Sleep -Milliseconds 10
  }
 }
 try{
  if($stream.Length -le 0 -or $stream.Length -gt 32768){throw 'OWN_SOURCE_PROOF_SIZE'}
  $reader=[IO.StreamReader]::new($stream,[Text.UTF8Encoding]::new($false,$true),$false,4096,$true)
  try{return ($reader.ReadToEnd()|ConvertFrom-Json)}finally{$reader.Dispose()}
 }finally{$stream.Dispose()}
}
try{
 $definition=New-GmgnSupervisorTaskDefinition $service $sid $exe $actionArguments $repo
 $task=Register-GmgnSupervisorTask $folder $name $definition $sid $exe $actionArguments $repo;$created=$true
 # The immutable transport deadline covers startup and the handoff itself.
 # Prepare generation, native pins and the registered action before starting
 # its clock; a failed first-instance pipe prevents the original Run call.
 if($sourceCase){$server=[GmgnSupervisorPipe]::new($pipeName,$sid,$true,30000)}
 $earliest=[DateTime]::UtcNow.AddSeconds(-1).ToString('o');$instance=Start-GmgnSupervisorTask $task $sid $exe $actionArguments $repo;$instanceGuid=$instance.InstanceGuid
 $clock=[Diagnostics.Stopwatch]::StartNew();do{$instance.Refresh();$engine=$instance.EnginePID;if($engine -gt 0){break};Start-Sleep -Milliseconds 10}while($clock.Elapsed.TotalSeconds -lt 10)
 $action=Bind-GmgnSupervisorTaskAction $instance $task $instanceGuid $engine $sid $exe $actionArguments $repo $earliest
 $originalBinding=$action.ReadNativeBinding();$originalProcess=[Diagnostics.Process]::GetProcessById([int]$originalBinding[0]);$null=$originalProcess.Handle
 if(([GmgnRepairProcessBinding]::Read($originalProcess) -join '|') -cne ($originalBinding -join '|')){throw 'OWN_WARM_PROCESS_IDENTITY'}
 if($Case -cin @('SourceNormal','SourceReplay','SourceWrongSession') -or $rootCase){
  $sourcePins=[GmgnSourcePinBinding]::new($first);$server.Accept();$action.CheckPeerPid($server.ReadKernelPeerPid())
  $rootSession=if($Case -ceq 'SourceWrongSession'){'0'*32}else{$session}
  $handoffActor=$action
  if($Case -cin @('SourceTaskWeakActor','SourceTaskActionMismatch')){$handoffActor=[GmgnScheduledActionIdentity]::BindExpectedPeer($originalBinding,$sid,$recovery,$recovery.Identity)}
  $rootTransfer=[GmgnPinTransferHandshake]::new($server,$sourcePins,$handoffActor,'ROOT',$generation.sha256,$rootSession,$challenge,$rootNative,$originalBinding)
  try{
   $rootTransfer.Execute();$rootCompletion=[GmgnHandoffCompletion]::new($server,$rootTransfer,$sourcePins,$handoffActor,$auth,'ROOT');$rootCompletion.WaitChildReady()
  }catch{
   $protocolCause=Cause $_
   if($Case -ceq 'SourceWrongSession' -and $protocolCause -cnotin @('GMGN_PIN_ACK_MESSAGE','GMGN_PIPE_DISCONNECTED','GMGN_PIPE_DEADLINE')){throw}
   if($Case -cin @('SourceNormal','SourceReplay') -or $rootCase){throw}
  }
  if($Case -cin @('SourceNormal','SourceReplay') -or $rootCase){
   if($rootCompletion.State -cne 'COMPLETE'){throw 'OWN_SOURCE_ROOT_RECEIPT_INCOMPLETE'}
   $rootReceiptVerified=$true
  }
 }
 $proofPath=Join-Path $recovery.Path 'stage-ready.json';$faultPath=Join-Path $recovery.Path 'stage-fault.json'
 $sourceProofPath=Join-Path $recovery.Path 'warm-source.json'
 $targetProofPath=if($sourceCase){$sourceProofPath}else{$proofPath}
 while(-not [IO.File]::Exists($targetProofPath) -and -not [IO.File]::Exists($faultPath) -and $clock.Elapsed.TotalSeconds -lt $proofDeadline){$action.Check();$first.Check();$auth.Check();Start-Sleep -Milliseconds 20}
 if([IO.File]::Exists($faultPath)){
  $fault=ReadOwnProtectedPacket $faultPath;$data=$fault.body|ConvertFrom-Json
  if($data.code -cnotmatch '\AGMGN_[A-Z0-9_]+\z' -or -not $proofAuth.Verify($fault.body,$fault.mac)){throw 'OWN_WARM_UNSAFE_FAULT'}
  $expectedFault=switch($Case){'MissingChallenge'{'GMGN_STAGE_WARM_CHALLENGE'};'WrongAuth'{'GMGN_INSTALL_PLAN_CONTEXT'};'SourceWrongRootArguments'{'GMGN_ACTION_COMMAND_MISMATCH'};'SourceWrongRootBinding'{'GMGN_ACTION_ORIGINAL_CHANGED'};'SourceWrongSession'{'GMGN_PIN_ACK_MESSAGE'};'SourceNullContract'{'GMGN_WARM_HANDOFF_FIELDS'};'SourceExtraField'{'GMGN_WARM_HANDOFF_FIELDS'};'SourceDuplicateField'{'GMGN_WARM_HANDOFF_NONCANONICAL'};'SourceWrongType'{'GMGN_WARM_HANDOFF_NONCANONICAL'};default{''}}
  if($data.code -cne $expectedFault){throw ('OWN_WARM_STAGE_FAULT_'+$data.code)}
  $code=$data.code
 }
 if(-not $code -and (-not [IO.File]::Exists($proofPath) -or $sourceCase -and -not [IO.File]::Exists($sourceProofPath))){
  # Read only the known stage status from this independently bound OWN child.
  $connection=[Management.Automation.Runspaces.NamedPipeConnectionInfo]::new([int]$originalBinding[0]);$connection.OpenTimeout=5000
  $peer=[Management.Automation.Runspaces.RunspaceFactory]::CreateRunspace($connection);$peer.Open();$inspection=[Management.Automation.PowerShell]::Create();$inspection.Runspace=$peer
  try{
   $null=$inspection.AddScript({
    $f=[Reflection.BindingFlags]'Instance,NonPublic,Public';$r=Get-Runspace -Id 1;$engine=$r.GetType().GetField('_engine',$f).GetValue($r)
    $context=$engine.GetType().GetField('_context',$f).GetValue($engine);$session=$context.GetType().GetProperty('EngineSessionState',$f).GetValue($context,$null)
    $scope=$session.GetType().GetProperty('GlobalScope',$f).GetValue($session,$null);$vars=$scope.GetType().GetProperty('Variables',$f).GetValue($scope,$null)
    $stage=$vars['IMCGmgnSupervisorStage'].Value
    if(-not $stage){return 'OWN_WARM_STAGE_ABSENT'}
    if($stage.ErrorCode -cnotmatch '\AGMGN_[A-Z0-9_]+\z'){
     if($stage.InstallPlan){
      $t=$stage.InstallPlan.GetType();$x=$t.GetField('warmTransfer',$f).GetValue($stage.InstallPlan);$y=$t.GetField('warmCompletion',$f).GetValue($stage.InstallPlan)
      $xs=if($x){$x.GetType().GetField('state',$f).GetValue($x)}else{'ABSENT'};$ys=if($y){$y.GetType().GetField('state',$f).GetValue($y)}else{'ABSENT'}
      if($xs -cmatch '\A[A-Z_]+\z' -and $ys -cmatch '\A[A-Z_]+\z'){return ('OWN_WARM_STEP_'+$xs+'_'+$ys)}
     }
     if($stage.Phase -cmatch '\A[A-Z_]+\z'){return ('OWN_WARM_PHASE_'+$stage.Phase)}
     return 'OWN_WARM_STAGE_UNKNOWN'
    }
    return ('OWN_WARM_STAGE_'+$stage.ErrorCode)
   });$diagnostic=@($inspection.Invoke());if($inspection.HadErrors -or $diagnostic.Count -ne 1){throw 'OWN_WARM_DIAGNOSTIC_FAILED'}
   throw ([string]$diagnostic[0])
  }finally{$inspection.Dispose();$peer.Dispose()}
 }
 if(-not $code){
 $proofStream=$null
 while(-not $proofStream){
  $action.Check();$first.Check();$auth.Check()
  if($clock.Elapsed.TotalSeconds -ge $proofDeadline){throw 'OWN_WARM_PROOF_OPEN_DEADLINE'}
  try{$proofStream=[IO.FileStream]::new($proofPath,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)}catch{
   $cause=$_.Exception;while($cause.InnerException){$cause=$cause.InnerException}
   if(($cause.HResult -band 65535) -ne 32){throw};Start-Sleep -Milliseconds 10
  }
 }
 if($proofStream.Length -le 0 -or $proofStream.Length -gt 32768){$proofStream.Dispose();throw 'OWN_WARM_PROOF_SIZE'}
 $reader=[IO.StreamReader]::new($proofStream,[Text.UTF8Encoding]::new($false,$true),$true,4096,$true);try{$packet=$reader.ReadToEnd()|ConvertFrom-Json}finally{$reader.Dispose()}
 try{
  if($Case -ceq 'WrongProof'){$packet.mac='0'*64}
  if(-not $auth.Verify($packet.body,$packet.mac)){throw 'OWN_WARM_PROOF_AUTH'}
  $proof=$packet.body|ConvertFrom-Json;$expectedChallenge=if($Case -ceq 'ReplayChallenge'){'0'*64}else{$challenge}
  if($proof.kind -cne 'WARM_ACTION_PROBE' -or $proof.phase -cne 'WARM_PLAN_BOUND' -or $proof.challenge -cne $expectedChallenge -or
   $proof.action_sha256 -cne $actionHash -or $proof.installation_id -cne $id -or $proof.install_plan_sha256 -cne $planHash -or
   $proof.generation_sha256 -cne $generation.sha256 -or $proof.auth_binding_sha256 -cne $values.auth_binding_sha256 -or
   $proof.directory -cne $recovery.Path -or $proof.directory_identity -cne $recovery.Identity -or
   ($proof.self -join '|') -cne ($originalBinding -join '|') -or $proof.read_address_published -ne $false -or $proof.legacy_touched -ne $false -or
   $proof.sdk_granted -ne $false -or $proof.provider_calls -ne 0){throw 'OWN_WARM_PROOF_BINDING'}
  $instance.Refresh();if($instance.InstanceGuid -cne $instanceGuid -or $instance.EnginePID -ne $engine){throw 'OWN_WARM_INSTANCE_CHANGED'}
  $action.Check();$first.Check();$auth.Check();$plan.Check();$recovery.Check();$verified=$true
  if($Case -cin @('SourceNormal','SourceReplay') -or $rootCase){
   $sourcePacket=ReadOwnProtectedPacket $sourceProofPath
   if(-not $auth.Verify($sourcePacket.body,$sourcePacket.mac)){throw 'OWN_SOURCE_PROOF_AUTH'}
   $sourceProof=$sourcePacket.body|ConvertFrom-Json
   if($sourceProof.kind -cne 'WARM_SOURCE_PROBE' -or $sourceProof.phase -cne 'SOURCE_BOUND' -or $sourceProof.installation_id -cne $id -or
    $sourceProof.generation_sha256 -cne $generation.sha256 -or $sourceProof.install_plan_sha256 -cne $planHash -or $sourceProof.challenge -cne $challenge -or
    @($sourceProof.origin).Count -ne 14 -or $sourceProof.origin[1] -cne $generation.sha256 -or $sourceProof.origin[2] -cne $session -or $sourceProof.origin[3] -cne $challenge -or
    ($sourceProof.origin[6..9] -join '|') -cne ($originalBinding -join '|') -or ($sourceProof.origin[10..13] -join '|') -cne ($rootNative -join '|') -or
    $sourceProof.read_address_published -ne $false -or $sourceProof.legacy_touched -ne $false -or $sourceProof.sdk_granted -ne $false -or $sourceProof.provider_calls -ne 0){throw 'OWN_SOURCE_PROOF_BINDING'}
   $sourceVerified=$true
   if($rootCase){
    $wantedGuid=$instanceGuid;$wantedEngine=$engine;$wantedName=$name;$wantedTask=$task;$wantedCompletion=$rootCompletion
    switch($Case){
     'SourceTaskGuid'{$wantedGuid='{00000000-0000-0000-0000-000000000000}'}
     'SourceTaskEngine'{$wantedEngine=$engine+1}
     'SourceTaskPath'{$wantedName='IMC-READ-FIXTURE-'+[guid]::NewGuid().ToString('N')}
     'SourceTaskNotCom'{$wantedTask=[pscustomobject]@{Path=('\'+$name)}}
     'SourceTaskNullCompletion'{$wantedCompletion=$null}
     'SourceTaskIncomplete'{$rootCompletion.GetType().GetField('state',[Reflection.BindingFlags]'Instance,NonPublic').SetValue($rootCompletion,'NEW')}
     'SourceTaskDefinition'{
      $changed=$service.NewTask(0);$changed.XmlText=$definition.XmlText;$changed.Actions.Item(1).Arguments=$actionArguments+' -Unexpected'
      $null=$folder.RegisterTaskDefinition($name,$changed,4,$sid,$null,3,$null);$taskDefinitionChanged=$true
     }
     'SourceTaskPlanFault'{$plan.Abort()}
     'SourceTaskAuthFault'{$auth.Abort()}
     'SourceTaskSourceFault'{$first.Abort()}
     'SourceTaskChildFault'{$action.Abort()}
     'SourceTaskTransferFault'{$rootTransfer.Abort()}
     'SourceTaskRegistryAbsent'{$folder.DeleteTask($name,0);$ownTaskDeleted=$true}
     'SourceTaskActionMismatch'{
      # A same-content plan at another held path produces different expected
      # action arguments. The actual Child still runs the original action.
      $cloneDir=Join-Path $scope 'clone-plan';$null=[IO.Directory]::CreateDirectory($cloneDir);$clonePath=Join-Path $cloneDir 'package.json'
      [IO.File]::Copy($planPath,$clonePath)
      $clonePin=[GmgnImmutableSources]::new('%USERPROFILE%','C:\Program Files\nodejs\node.exe',[string[]]@($clonePath),[string[]]@($planHash))
      $plan=[GmgnReadInstallPlan]::new($first,$clonePin,$clonePath,$planHash,$generation.manifest,$generation.sha256,$auth,$recovery,$id,$rootArguments)
      $changed=$service.NewTask(0);$changed.XmlText=$definition.XmlText;$changed.Actions.Item(1).Arguments=$plan.BuildWarmTaskAction()
      $null=$folder.RegisterTaskDefinition($name,$changed,4,$sid,$null,3,$null);$taskDefinitionChanged=$true
     }
    }
    try{
     $boundTask=$plan.BindOriginalRootTask($wantedCompletion,$handoffActor,$wantedTask,$instance,$wantedGuid,$wantedEngine,$wantedName)
     $boundTask.Check();$bound=$boundTask.ReadBinding()
     if($boundTask.State -cne 'ROOT_TASK_BOUND' -or $bound.Count -ne 14 -or
      ($bound[6..9] -join '|') -cne ($rootNative -join '|') -or ($bound[10..13] -join '|') -cne ($originalBinding -join '|')){throw 'OWN_ROOT_TASK_BINDING'}
     $rootTaskVerified=$true
     if($Case -cin @('SourceTaskStrongSid','SourceTaskStrongExe','SourceTaskStrongAbort')){
      $f=[Reflection.BindingFlags]'Instance,NonPublic';$strict=$boundTask.GetType().GetField('strictChild',$f).GetValue($boundTask)
      switch($Case){
       'SourceTaskStrongSid'{$strict.GetType().GetField('sid',$f).SetValue($strict,'S-1-5-18')}
       'SourceTaskStrongExe'{$strict.GetType().GetField('exe',$f).SetValue($strict,'C:\Windows\System32\cmd.exe')}
       'SourceTaskStrongAbort'{$strict.Abort()}
      }
      $boundTask.Check()
     }
     if($Case -ceq 'SourceTaskLateDefinition'){
      $changed=$service.NewTask(0);$changed.XmlText=$definition.XmlText;$changed.Actions.Item(1).Arguments=$actionArguments+' -Unexpected'
      $null=$folder.RegisterTaskDefinition($name,$changed,4,$sid,$null,3,$null);$taskDefinitionChanged=$true;$boundTask.Check()
     }
     if($Case -ceq 'SourceTaskRegistryLate'){
      $folder.DeleteTask($name,0);$ownTaskDeleted=$true
      # Independently exercise the fresh lookup, before the public Check also
      # rejects the now-disappeared original IRunningTask.
      $lookup='';try{$null=$boundTask.GetType().GetMethod('CheckDefinition',[Reflection.BindingFlags]'Instance,NonPublic').Invoke($boundTask,$null)}catch{$lookup=Cause $_}
      if($lookup -cne 'GMGN_ROOT_TASK_REGISTRY_QUERY'){throw 'OWN_ROOT_REGISTRY_ABSENCE_NOT_BLOCKED'}
      $registryFailureVerified=$true;$boundTask.Check()
     }
     if($Case -ceq 'SourceTaskReplay'){$null=$plan.BindOriginalRootTask($rootCompletion,$action,$task,$instance,$instanceGuid,$engine,$name)}
     if($Case -ceq 'SourceTaskLateCheck'){
      while($clock.Elapsed.TotalSeconds -lt 31){Start-Sleep -Milliseconds 10}
      $expired='';try{$null=$server.ReadKernelPeerPid()}catch{$expired=Cause $_}
      if($expired -cne 'GMGN_PIPE_DEADLINE'){throw 'OWN_ROOT_PIPE_NOT_EXPIRED'}
      $boundTask.Check();if($boundTask.State -cne 'ROOT_TASK_BOUND'){throw 'OWN_ROOT_TASK_DEPENDS_ON_EXPIRED_PIPE'}
     }
    }catch{
     $code=Cause $_
     if($boundTask){
      $next='';try{$boundTask.Check()}catch{$next=Cause $_}
      $expectedNext=if($Case -ceq 'SourceTaskReplay'){'GMGN_INSTALL_PLAN_FAULT'}else{'GMGN_ROOT_TASK_FAULT'}
      if($next -cne $expectedNext -or $boundTask.State -cne 'FAULT'){throw 'OWN_ROOT_TASK_FAILURE_NOT_ABSORBING'}
      $again='';try{$boundTask.Check()}catch{$again=Cause $_};if($again -cne 'GMGN_ROOT_TASK_FAULT'){throw 'OWN_ROOT_TASK_FAILURE_NOT_ABSORBING'}
     }
     if($plan.State -cne 'FAULT'){throw 'OWN_ROOT_PLAN_FAILURE_NOT_ABSORBING'}
    }
   }
   if($SenderCase -cne 'None'){
    if(-not $rootTaskVerified -or -not $legacyPair){throw 'OWN_SENDER_ROOT_REQUIRED'}
    $sender=$plan.CreateRootRetirementSender();if($sender.State -cne 'BOUND'){throw 'OWN_SENDER_NOT_BOUND'}
    if($SenderCase -ceq 'GrantBoundaries'){
     # OWN retained Sender only, no publisher call/effect. Restore private
     # state after testing exact reference and one-shot admission boundaries.
     $f=[Reflection.BindingFlags]'Instance,NonPublic';$senderType=$sender.GetType();$stateField=$senderType.GetField('state',$f);$attemptField=$senderType.GetField('attempted',$f);$grantField=$senderType.GetField('publicationGrant',$f);$fixtureField=$senderType.GetField('fixture',$f)
     $admit=$senderType.GetMethod('AdmitOwnPublicationProbe',$f);$grantType=$senderType.GetNestedType('PublicationGrant',[Reflection.BindingFlags]'NonPublic');$grant=[Activator]::CreateInstance($grantType,$true);$otherGrant=[Activator]::CreateInstance($grantType,$true)
     $otherRecovery=[GmgnReadProofPin]::CreateNewDirectory((Join-Path (Join-Path $repo '.local-tools') ('gmgn-read-recovery-'+[guid]::NewGuid().ToString('N'))));$savedFixture=$fixtureField.GetValue($sender)
     $stateField.SetValue($sender,'SENDING');$attemptField.SetValue($sender,$true);$grantField.SetValue($sender,$grant)
     try{
      foreach($argsForMethod in @([object[]]@($recovery.PSObject.BaseObject,$otherGrant),[object[]]@($otherRecovery.PSObject.BaseObject,$grant))){if($admit.Invoke($sender,$argsForMethod)){throw 'OWN_SENDER_FOREIGN_GRANT_ADMITTED'}}
      $fixtureField.SetValue($sender,$null);if($admit.Invoke($sender,[object[]]@($recovery.PSObject.BaseObject,$grant))){throw 'OWN_SENDER_NO_FIXTURE_ADMITTED'};$fixtureField.SetValue($sender,$savedFixture)
      if(-not $admit.Invoke($sender,[object[]]@($recovery.PSObject.BaseObject,$grant)) -or $admit.Invoke($sender,[object[]]@($recovery.PSObject.BaseObject,$grant))){throw 'OWN_SENDER_GRANT_CONSUMPTION'}
     }finally{$fixtureField.SetValue($sender,$savedFixture);$stateField.SetValue($sender,'BOUND');$attemptField.SetValue($sender,$false);$grantField.SetValue($sender,$null)}
    }
    if($RetireCase -cne 'None'){
     if($SenderCase -cne 'Normal' -or $LegacyCase -cne 'None'){throw 'OWN_RETIRE_CASE_CONTRACT'}
     . (Join-Path $PSScriptRoot 'gmgn_warm_retire_child.ps1')
     $prepareProof=Invoke-OwnWarmRetireChild $originalBinding 'Prepare' ''
     if($RetireCase -ceq 'ProbeGuards'){
      $guardProof=Invoke-OwnWarmRetireChild $originalBinding 'GuardOnly' ''
      if(-not $guardProof.guards_verified -or $guardProof.guard_count -ne 5 -or $guardProof.callbacks -ne 0){throw 'OWN_RETIRE_GUARDS_NOT_PROVEN'}
     }
    }
    $broker=[string[]]$legacyPair.Bindings.broker;$parent=[string[]]$legacyPair.Bindings.parent
    $observed=[Diagnostics.Stopwatch]::GetTimestamp();$frequency=[Diagnostics.Stopwatch]::Frequency
    if($SenderCase -ceq 'Stale'){$observed-=16*$frequency}
    $owner=[ordered]@{schema_version=1;kind='ORIGINAL_OWNER_RETIREMENT_EVIDENCE';owner_pid=[int]$parent[0];owner_birth=$parent[2];broker_pid=[int]$broker[0];broker_birth=$broker[2];broker_directory=(Join-Path $legacyPair.Scope 'legacy');loaded_source_sha256=('a'*64);loaded_cleanup_sha256=('b'*64);original_script=(Join-Path $legacyPair.Scope 'parent.ps1');original_line=1;original_broker_handle_verified=$true;variables_changed=$false;provider_calls=0;observed_qpc=$observed.ToString();qpc_frequency=$frequency.ToString()}
    $body=$owner|ConvertTo-Json -Compress;$mac=$auth.Sign($body);if($SenderCase -ceq 'WrongMac'){$mac='0'*64}
    $ownerPath=Join-Path (Join-Path $repo '.local-tools') ('gmgn-owner-proof-'+[guid]::NewGuid().ToString('N')+'.json')
    $bytes=[Text.UTF8Encoding]::new($false).GetBytes((@{body=$body;mac=$mac}|ConvertTo-Json -Compress));$write=[IO.FileStream]::new($ownerPath,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
    try{$write.Write($bytes,0,$bytes.Length);$write.Flush($true)}finally{$write.Dispose()}
    $ownerHash=(Get-FileHash $ownerPath).Hash.ToLowerInvariant();if($SenderCase -ceq 'WrongHash'){$ownerHash='0'*64}
    $requestPath=Join-Path $recovery.Path 'retire.request.json'
    if($SenderCase -ceq 'ExistingRequest'){[IO.File]::WriteAllText($requestPath,'OWN_COLLISION',[Text.UTF8Encoding]::new($false))}
    if($SenderCase -ceq 'AuthFault'){$auth.Abort()};if($SenderCase -ceq 'TaskFault'){$boundTask.Abort()}
    $probeField=$null;$probeReached=$false
    if($SenderCase -ceq 'PostPublication'){
     Add-Type -TypeDefinition 'using System;public static class OwnSenderPublicationProbe{public static bool Reached;public static Action<string> Make(){return delegate(string phase){if(phase!="AFTER_FINAL_READBACK")throw new InvalidOperationException("OWN_PUBLICATION_WRONG_PHASE");Reached=true;throw new InvalidOperationException("SYNTHETIC_PRIVATE_PUBLICATION");};}}'
     $probeField=[GmgnReadProofPin].GetField('fixtureProbe',[Reflection.BindingFlags]'Static,NonPublic');$probeField.SetValue($null,[OwnSenderPublicationProbe]::Make())
    }
    $sendClock=[Diagnostics.Stopwatch]::StartNew()
    $sendCode='';try{$sender.Send($ownerPath,$ownerHash);if($SenderCase -ceq 'FactoryReplay'){$null=$plan.CreateRootRetirementSender()};if($SenderCase -ceq 'Replay'){$sender.Send($ownerPath,$ownerHash)}}catch{$sendCode=Cause $_}finally{if($probeField){$probeField.SetValue($null,$null);$probeReached=[OwnSenderPublicationProbe]::Reached}}
    $sendElapsed=$sendClock.ElapsedMilliseconds
    $wanted=switch($SenderCase){'PostPublication'{'GMGN_ROOT_SENDER_OPERATION'};'Replay'{'GMGN_ROOT_SENDER_USED'};'FactoryReplay'{'GMGN_ROOT_SENDER_USED'};'ExistingRequest'{'GMGN_PROOF_OPEN'};'Stale'{'GMGN_STAGE_OWNER_PROOF_STALE'};'WrongHash'{'GMGN_OWNER_PIN_HASH'};'WrongMac'{'GMGN_OWNER_PIN_AUTH'};'AuthFault'{'GMGN_AUTH_FAULT'};'TaskFault'{'GMGN_ROOT_TASK_FAULT'};default{''}}
    if($sendCode -cne $wanted){throw ('OWN_SENDER_WRONG_CODE_'+$sendCode)}
    $wantedState=if($SenderCase -cin @('ExistingRequest','PostPublication')){'SEND_UNKNOWN'}elseif($SenderCase -cin @('Normal','Replay','FactoryReplay','GrantBoundaries')){'SENT'}else{'FAULT'}
    if($sender.State -cne $wantedState){throw 'OWN_SENDER_WRONG_STATE'}
    $again='';try{$sender.Send($ownerPath,$ownerHash)}catch{$again=Cause $_};if($again -cne 'GMGN_ROOT_SENDER_USED' -or $sender.State -cne $wantedState){throw 'OWN_SENDER_RETRY_ALLOWED'}
    $published=[IO.File]::Exists($requestPath)
    if($SenderCase -cin @('Normal','Replay','FactoryReplay','PostPublication','GrantBoundaries')){
     $packet=Get-Content $requestPath -Raw|ConvertFrom-Json;$command=$packet.body|ConvertFrom-Json
     $h=[Security.Cryptography.HMACSHA256]::new([Text.Encoding]::ASCII.GetBytes('f'*64));try{$signed=[BitConverter]::ToString($h.ComputeHash([Text.Encoding]::UTF8.GetBytes($packet.body))).Replace('-','').ToLowerInvariant()}finally{$h.Dispose()}
     if($packet.mac -cne $signed -or $command.purpose -cne 'IMC_WARM_RETIRE_V1' -or $command.installation_id -cne $id -or $command.origin -cne $serializer.Serialize([string[]]$bound) -or $command.supervisor -cne $serializer.Serialize([string[]]$originalBinding) -or $command.owner_observed_qpc -cne $observed.ToString()){throw 'OWN_SENDER_COMMAND_BINDING'}
     $blocked=$false;try{$w=[IO.FileStream]::new($requestPath,[IO.FileMode]::Open,[IO.FileAccess]::Write,[IO.FileShare]::Read);$w.Dispose()}catch{$blocked=$true};if(-not $blocked){throw 'OWN_SENDER_REQUEST_RELEASED'}
     if($SenderCase -ceq 'PostPublication'){
      if(-not $probeReached -or $plan.State -cne 'FAULT' -or $sender.GetType().GetField('request',[Reflection.BindingFlags]'Instance,NonPublic').GetValue($sender)){throw 'OWN_SENDER_POSTPUBLICATION_NOT_REACHED'}
      $retained=[GmgnReadProofPin].GetField('retained',[Reflection.BindingFlags]'Static,NonPublic').GetValue($null)
      $actual=@($retained|Where-Object {$_.GetType().GetField('path',[Reflection.BindingFlags]'Instance,NonPublic').GetValue($_) -ceq $requestPath})
      if($actual.Count -ne 1 -or -not $actual[0].Faulted){throw 'OWN_SENDER_PROOF_NOT_RETAINED'}
      $deleted=$false;try{[IO.File]::Delete($requestPath)}catch{$deleted=$true};if(-not $deleted){throw 'OWN_SENDER_PROOF_DELETE_ALLOWED'}
     }
    }elseif($SenderCase -ceq 'ExistingRequest'){if([IO.File]::ReadAllText($requestPath) -cne 'OWN_COLLISION'){throw 'OWN_SENDER_COLLISION_OVERWRITTEN'}}elseif($published){throw 'OWN_SENDER_EARLY_PUBLISHED'}
    if($legacyPair.Broker.HasExited -or $legacyPair.Parent.HasExited){throw 'OWN_SENDER_RETIRED_PROCESS'}
    $senderProof=[pscustomobject]@{case=$SenderCase;code=$sendCode;state=$sender.State;request_published=$published;retry_blocked=$true;probe_reached=$probeReached;retirement_performed=$false}
    if($RetireCase -cne 'None'){
     $requestHash=(Get-FileHash -LiteralPath $requestPath -Algorithm SHA256).Hash.ToLowerInvariant()
     try{$retireProof=Invoke-OwnWarmRetireChild $originalBinding $RetireCase $requestHash}
     catch{$failure=Cause $_;throw ($failure+'_SEND_MS_'+$sendElapsed+'_CONTEXT_MS_'+$prepareProof.context_ms)}
     $senderProof|Add-Member -NotePropertyName retire_proof -NotePropertyValue $retireProof
     if($RetireCase -ceq 'ProbeGuards'){$retireProof.guards_verified=[bool]$guardProof.guards_verified}
     if($RetireCase -cin @('Normal','Replay','ProbeGuards')){
      if(-not $legacyPair.Broker.WaitForExit(5000) -or -not $legacyPair.Parent.WaitForExit(5000)){throw 'OWN_RETIRE_NATURAL_EXIT_NOT_CONFIRMED'}
     }elseif($legacyPair.Broker.HasExited -or $legacyPair.Parent.HasExited){throw 'OWN_RETIRE_FAILED_EFFECT_OCCURRED'}
    }
   }
   if($Case -ceq 'SourceReplay'){
    $connection=[Management.Automation.Runspaces.NamedPipeConnectionInfo]::new([int]$originalBinding[0]);$connection.OpenTimeout=5000
    $peer=[Management.Automation.Runspaces.RunspaceFactory]::CreateRunspace($connection);$peer.Open();$inspection=[Management.Automation.PowerShell]::Create();$inspection.Runspace=$peer
    try{
     $null=$inspection.AddScript({
      $f=[Reflection.BindingFlags]'Instance,NonPublic,Public';$r=Get-Runspace -Id 1;$engine=$r.GetType().GetField('_engine',$f).GetValue($r)
      $context=$engine.GetType().GetField('_context',$f).GetValue($engine);$session=$context.GetType().GetProperty('EngineSessionState',$f).GetValue($context,$null)
      $scope=$session.GetType().GetProperty('GlobalScope',$f).GetValue($session,$null);$vars=$scope.GetType().GetProperty('Variables',$f).GetValue($scope,$null)
      $stage=$vars['IMCGmgnSupervisorStage'].Value
      if(-not $stage -or $stage.Phase -cne 'SOURCE_BOUND'){throw 'OWN_REPLAY_NOT_SOURCE_BOUND'}
      $caught='';try{$null=$stage.InstallPlan.CompleteWarmSourceHandoff()}catch{$e=$_.Exception;while($e.InnerException){$e=$e.InnerException};$caught=$e.Message}
      if($caught -cne 'GMGN_WARM_HANDOFF_USED' -or $stage.InstallPlan.State -cne 'FAULT'){throw 'OWN_REPLAY_NOT_BLOCKED'}
      return $caught
     })
     $replay=@($inspection.Invoke());if($inspection.HadErrors -or $replay.Count -ne 1){throw 'OWN_REPLAY_NOT_BLOCKED'}
     if([string]$replay[0] -cne 'GMGN_WARM_HANDOFF_USED'){throw 'OWN_REPLAY_NOT_BLOCKED'};$code=[string]$replay[0]
    }finally{$inspection.Dispose();$peer.Dispose()}
   }
   if($legacyPair -and $LegacyCase -cne 'None'){
    if(-not $rootTaskVerified){throw 'OWN_WARM_LEGACY_ROOT_NOT_VERIFIED'}
    if($LegacyCase -ceq 'ParentExit'){Stop-OwnWarmLegacyPair $legacyPair}
    $action.Check();$boundTask.Check()
    $connection=[Management.Automation.Runspaces.NamedPipeConnectionInfo]::new([int]$originalBinding[0]);$connection.OpenTimeout=5000
    $peer=[Management.Automation.Runspaces.RunspaceFactory]::CreateRunspace($connection);$peer.Open();$inspection=[Management.Automation.PowerShell]::Create();$inspection.Runspace=$peer
    try{
     $null=$inspection.AddScript({param($case)
      $ErrorActionPreference='Stop';$f=[Reflection.BindingFlags]'Instance,NonPublic,Public';$r=Get-Runspace -Id 1;$engine=$r.GetType().GetField('_engine',$f).GetValue($r)
      $context=$engine.GetType().GetField('_context',$f).GetValue($engine);$session=$context.GetType().GetProperty('EngineSessionState',$f).GetValue($context,$null)
      $scope=$session.GetType().GetProperty('GlobalScope',$f).GetValue($session,$null);$vars=$scope.GetType().GetProperty('Variables',$f).GetValue($scope,$null);$stage=$vars['IMCGmgnSupervisorStage'].Value
      if(-not $stage -or $stage.Phase -cne 'SOURCE_BOUND' -or -not $stage.WarmOrigin){throw 'OWN_WARM_LEGACY_SOURCE_REQUIRED'}
      $stage.WarmOrigin.Check();$plan=$stage.InstallPlan;$plan.Check();$type=$plan.GetType();$before=[int]$type.GetNestedType('WarmValidationAdmission').GetField('retained',[Reflection.BindingFlags]'Static,NonPublic').GetValue($null).Count
      switch($case){
       'ExistingTarget'{$null=[IO.Directory]::CreateDirectory((Join-Path $stage.Directory.Path 'capture'))}
       'AuthFault'{$stage.Authentication.Abort()}
       'RecoveryAcl'{$path=$stage.Directory.Path;$acl=[IO.Directory]::GetAccessControl($path,[Security.AccessControl.AccessControlSections]::Access);$acl.SetAccessRuleProtection($false,$true);[IO.Directory]::SetAccessControl($path,$acl)}
      }
      $admitted=$null;$caught='';$concurrentReached=$false;$concurrentJoined=$false;$concurrentCode='';$probeField=$null
      if($case -ceq 'Concurrent'){
       Add-Type -TypeDefinition @'
using System;using System.Reflection;using System.Threading;
public static class OwnWarmFactoryRace {
 public static bool Reached,Joined;public static string Code;
 public static Action<string,int> Make(object plan){return delegate(string phase,int index){
  if(phase!="NAMESPACE_PINNED"||index!=0)return;Reached=true;
  var worker=new Thread(delegate(){try{plan.GetType().GetMethod("CreateWarmValidationAdmission").Invoke(plan,null);Code="OWN_RACE_UNEXPECTED_SUCCESS";}
   catch(Exception error){while(error.InnerException!=null)error=error.InnerException;Code=error.Message;}});
  worker.IsBackground=true;worker.Start();Joined=worker.Join(3000);if(!Joined)throw new InvalidOperationException("OWN_WARM_RACE_JOIN_TIMEOUT");
 };}
}
'@
       $captureType=$type.Assembly.GetType('GmgnLegacyCapture');$probeField=$captureType.GetField('fixtureProbe',[Reflection.BindingFlags]'Static,NonPublic')
       $probeField.SetValue($null,[OwnWarmFactoryRace]::Make($plan))
      }
      try{$admitted=$plan.CreateWarmValidationAdmission();$admitted.Check();if($case -ceq 'Replay'){$null=$plan.CreateWarmValidationAdmission()}}
      catch{$e=$_.Exception;while($e.InnerException){$e=$e.InnerException};$caught=$e.Message}
      finally{if($probeField){$probeField.SetValue($null,$null)}}
      $objects=$type.GetNestedType('WarmValidationAdmission').GetField('retained',[Reflection.BindingFlags]'Static,NonPublic').GetValue($null)
      $actual=@($objects|Where-Object {[object]::ReferenceEquals($_.GetType().GetField('plan',$f).GetValue($_),$plan)})
      $expectedCount=if($case -ceq 'AuthFault'){0}else{1}
      if($actual.Count -ne $expectedCount){throw 'OWN_WARM_LEGACY_ADMISSION_RETENTION'}
      $capture=$null;$retire=$null;$life=$null
      if($actual.Count){$object=$actual[0];$capture=$object.GetType().GetField('capture',$f).GetValue($object);$retire=$object.GetType().GetField('retirement',$f).GetValue($object);$life=$object.GetType().GetField('lifetime',$f).GetValue($object)}
      if($case -ceq 'Concurrent'){
       $concurrentReached=[OwnWarmFactoryRace]::Reached;$concurrentJoined=[OwnWarmFactoryRace]::Joined;$concurrentCode=[OwnWarmFactoryRace]::Code
       # Inspect before admission.Check() can itself invalidate anything.
       if(-not $concurrentReached -or -not $concurrentJoined -or $concurrentCode -cne 'GMGN_WARM_LEGACY_USED' -or
        $caught -cne 'GMGN_INSTALL_PLAN_FAULT' -or $actual.Count -ne 1 -or -not $capture -or -not $capture.Faulted -or $capture.Committed -or
        $type.GetField('legacyAdmission',$f).GetValue($plan) -or $plan.State -cne 'FAULT'){throw 'OWN_WARM_RACE_NOT_PROVEN'}
      }
      if($caught){
       if($plan.State -cne 'FAULT'){throw 'OWN_WARM_LEGACY_PLAN_NOT_FAULTED'}
       if($actual.Count){foreach($i in 1..2){try{$actual[0].Check();throw 'OWN_WARM_LEGACY_FAULT_RESET'}catch{$e=$_.Exception;while($e.InnerException){$e=$e.InnerException};if($e.Message -cne 'GMGN_WARM_LEGACY_FAULT' -and $i -eq 2){throw 'OWN_WARM_LEGACY_FAULT_NOT_ABSORBING'}}}}
       if($capture -and ($capture.Committed -or -not $capture.Faulted)){throw 'OWN_WARM_LEGACY_CAPTURE_NOT_FAULTED'}
      }else{
       if($actual.Count -ne 1 -or -not [object]::ReferenceEquals($admitted,$actual[0]) -or $admitted.State -cne 'LEGACY_CAPTURED' -or -not $capture.Committed -or $retire.Phase -cne 'ADMITTED'){throw 'OWN_WARM_LEGACY_NOT_CAPTURED'}
       $admitted.Check();$life.CheckAlive()
      }
      if($life -and $life.Retired -or $retire -and $retire.Phase -cin @('RETIRED','RETIRE_REQUESTED')){throw 'OWN_WARM_LEGACY_RETIREMENT_OCCURRED'}
      if($stage.Retirement -or $stage.Lifetime -or $stage.Capture -or $stage.RoleValidated){throw 'OWN_WARM_LEGACY_STAGE_FALLTHROUGH'}
      $assembly=$type.Assembly;$factory=$assembly.GetType('GmgnReadProcessLifetime').GetField('retained',[Reflection.BindingFlags]'Static,NonPublic').GetValue($null)
      if($factory.Count){throw 'OWN_WARM_LEGACY_SDK_FACTORY_CREATED'}
      if([IO.File]::Exists((Join-Path $stage.Directory.Path 'retire-intent.json')) -or [IO.File]::Exists((Join-Path $stage.Directory.Path 'retire-completed.json'))){throw 'OWN_WARM_LEGACY_RETIRE_PROOF_CREATED'}
      [pscustomobject]@{case=$case;code=$caught;admission_count=$actual.Count;capture_committed=($capture -and $capture.Committed);sdk_factory_count=$factory.Count;retired=($life -and $life.Retired);plan_state=$plan.State;concurrent_reached=$concurrentReached;concurrent_joined=$concurrentJoined;concurrent_code=$concurrentCode}|ConvertTo-Json -Compress
     }).AddArgument($LegacyCase)
     $response=@($inspection.Invoke());if($inspection.HadErrors -or $response.Count -ne 1){throw 'OWN_WARM_LEGACY_CHILD_FAILED'}
     $legacyProof=[string]$response[0]|ConvertFrom-Json
    }finally{$inspection.Dispose();$peer.Dispose()}
    $action.Check();$boundTask.Check()
    $wanted=switch($LegacyCase){'Replay'{'GMGN_WARM_LEGACY_USED'};'ExistingTarget'{'GMGN_WARM_LEGACY_CAPTURE_CREATE_NEW'};'WrongMac'{'GMGN_LEGACY_RETIRE_ROLE_INVALID'};'ParentExit'{'GMGN_LEGACY_PIN_BINDING'};'AuthFault'{'GMGN_AUTH_FAULT'};'RecoveryAcl'{'GMGN_WARM_LEGACY_ACL'};'Concurrent'{'GMGN_INSTALL_PLAN_FAULT'};default{''}}
    if($legacyProof.code -cne $wanted -or $legacyProof.sdk_factory_count -ne 0 -or $legacyProof.retired -or $legacyProof.capture_committed -ne ($LegacyCase -ceq 'Normal')){throw ('OWN_WARM_LEGACY_WRONG_RESULT_'+$legacyProof.code)}
   }
  }
 }catch{$code=Cause $_}finally{$proofStream.Dispose()}
 }
 $expected=switch($Case){'SourceTaskReplay'{'GMGN_ROOT_TASK_USED'};'SourceTaskGuid'{'GMGN_ROOT_TASK_INSTANCE_CHANGED'};'SourceTaskEngine'{'GMGN_ROOT_TASK_BINDING'};'SourceTaskPath'{'GMGN_ROOT_TASK_INSTANCE_CHANGED'};'SourceTaskNotCom'{'GMGN_ROOT_TASK_INPUT'};'SourceTaskNullCompletion'{'GMGN_ROOT_TASK_INPUT'};'SourceTaskIncomplete'{'GMGN_ROOT_TASK_CONTEXT'};'SourceTaskDefinition'{'GMGN_ROOT_TASK_DEFINITION'};'SourceTaskPlanFault'{'GMGN_INSTALL_PLAN_FAULT'};'SourceTaskAuthFault'{'GMGN_AUTH_FAULT'};'SourceTaskSourceFault'{'GMGN_SOURCE_LOCKED'};'SourceTaskChildFault'{'GMGN_ACTION_IDENTITY_FAULT'};'WrongProof'{'OWN_WARM_PROOF_AUTH'};'ReplayChallenge'{'OWN_WARM_PROOF_BINDING'};'MissingChallenge'{'GMGN_STAGE_WARM_CHALLENGE'};'WrongAuth'{'GMGN_INSTALL_PLAN_CONTEXT'};'SourceReplay'{'GMGN_WARM_HANDOFF_USED'};'SourceWrongRootArguments'{'GMGN_ACTION_COMMAND_MISMATCH'};'SourceWrongRootBinding'{'GMGN_ACTION_ORIGINAL_CHANGED'};'SourceWrongSession'{'GMGN_PIN_ACK_MESSAGE'};'SourceNullContract'{'GMGN_WARM_HANDOFF_FIELDS'};'SourceExtraField'{'GMGN_WARM_HANDOFF_FIELDS'};'SourceDuplicateField'{'GMGN_WARM_HANDOFF_NONCANONICAL'};'SourceWrongType'{'GMGN_WARM_HANDOFF_NONCANONICAL'};default{''}}
 if($Case -ceq 'SourceTaskTransferFault'){$expected='GMGN_ROOT_TASK_TRANSFER_FAULT'}
 if($Case -ceq 'SourceTaskActionMismatch'){$expected='GMGN_ACTION_COMMAND_MISMATCH'}
 if($Case -ceq 'SourceTaskStrongSid'){$expected='GMGN_ACTION_OWNER_MISMATCH'}
 if($Case -ceq 'SourceTaskStrongExe'){$expected='GMGN_ACTION_EXECUTABLE'}
 if($Case -ceq 'SourceTaskStrongAbort'){$expected='GMGN_ACTION_IDENTITY_FAULT'}
 if($Case -cin @('SourceTaskDefinition','SourceTaskLateDefinition')){$expected='GMGN_TASK_ACTION'}
 if($Case -cin @('SourceTaskRegistryAbsent','SourceTaskRegistryLate')){$expected='GMGN_ROOT_TASK_INSTANCE_QUERY'}
 if($code -cne $expected -or ($Case -ceq 'Normal' -and -not $verified)){throw ('OWN_WARM_UNEXPECTED_RESULT_'+$code)}
}finally{
 try{if($server){$server.Dispose()}}
 finally{
 try{if($legacyPair -and -not $legacyPair.Cleanup){Stop-OwnWarmLegacyPair $legacyPair}}
 finally{
 if($taskDefinitionChanged){$null=$folder.RegisterTaskDefinition($name,$definition,4,$sid,$null,3,$null)}
 if($created){
  if(-not $originalProcess -or -not $action){throw 'OWN_WARM_CLEANUP_IDENTITY_MISSING'}
  Assert-GmgnSupervisorTaskDefinition $task.Definition $sid $exe $actionArguments $repo
  if(([GmgnRepairProcessBinding]::Read($originalProcess) -join '|') -cne ($originalBinding -join '|')){throw 'OWN_WARM_CLEANUP_IDENTITY_CHANGED'}
  if(-not $originalProcess.HasExited){$originalProcess.Kill()}
  if(-not $originalProcess.WaitForExit(5000) -or $action.ReadOriginalExitCode() -lt 0){throw 'OWN_WARM_EXIT_NOT_CONFIRMED'}
  if(-not $ownTaskDeleted){
   $current=$folder.GetTask($name);Assert-GmgnSupervisorTaskDefinition $current.Definition $sid $exe $actionArguments $repo
   $folder.DeleteTask($name,0)
  }
  $absent=$false;try{$null=$folder.GetTask($name)}catch{$e=$_.Exception;while($e.InnerException){$e=$e.InnerException};$absent=($e.HResult -band 65535) -eq 2}
  if(-not $absent){throw 'OWN_WARM_TASK_DELETE_UNCONFIRMED'};$cleanup=$true
 }
 }
 }
}
[pscustomobject]@{pass=$true;case=$Case;code=$code;warm_action_verified=$verified;original_native_exit=$action.ReadOriginalExitCode();native_cleanup_confirmed=$cleanup;
 source_handoff_verified=$sourceVerified;root_receipt_verified=$rootReceiptVerified;root_task_verified=$rootTaskVerified;registry_failure_verified=$registryFailureVerified;provider_calls=0;legacy_touched=($null -ne $legacyProof);legacy_proof=$legacyProof;sender_proof=$senderProof;legacy_cleanup=($legacyPair -and $legacyPair.Cleanup);sdk_granted=$false;production_verified=$false}|ConvertTo-Json -Depth 5 -Compress
}finally{
 if($legacyPair -and -not $legacyPair.Cleanup){Stop-OwnWarmLegacyPair $legacyPair}
}
