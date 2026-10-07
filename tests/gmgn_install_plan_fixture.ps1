param([ValidateSet('Normal','WrongInstallId','WrongAuth','WrongRecovery','WrongSource','Noncanonical','DuplicateField','ActorMismatch','SourceFault','AuthFault','PlanFault','ForeignPrimary','Bom','WrongJsonType','ExtraField','PrimaryAds',
 'PrimaryNormal','PrimaryMissingSdk','PrimaryMissingContract','PrimaryExtraPin','PrimaryWrongHash','PrimaryWrongId','PrimaryDuplicate','PrimaryBom','PrimarySourceFault','PrimaryParentFault','PrimaryBadType','PrimaryExtraField',
 'WarmBuilder','WarmOtherPlan','WarmChangedPayload','WarmChangedLoader','WarmWhitespace','WarmNonUtf16','WarmFile',
 'EffectAbortFirst','EffectWins','EffectUnderPlan','EffectFailure',
 'CheckpointEntry','CheckpointSourceFault','CheckpointPlanFault','CheckpointHashFinal')][string]$Case='Normal')
$ErrorActionPreference='Stop';$repo=Split-Path -Parent $PSScriptRoot;$tools=Join-Path $repo 'TOOLS'
Add-Type -AssemblyName System.Web.Extensions
$types=@('GmgnRepairNamespace','GmgnImmutableSources','GmgnReadDirectoryIdentity','GmgnScheduledActionIdentity','GmgnReadLocalAuth','GmgnReadInstallPlan')
Add-Type -Path @($types|ForEach-Object {Join-Path $tools ($_+'.cs')}) -ReferencedAssemblies @('System.dll','System.Core.dll','System.Web.Extensions.dll')
$scope=Join-Path (Join-Path $repo '.local-tools') ('install-plan-native-'+[guid]::NewGuid().ToString('N'));$null=[IO.Directory]::CreateDirectory($scope)
$s1dir=Join-Path $scope 'source1';$null=[IO.Directory]::CreateDirectory($s1dir);$s1path=Join-Path $s1dir 'package.json'
[IO.File]::WriteAllText($s1path,'{"own_source_only":true}',[Text.UTF8Encoding]::new($false))
$paths=[string[]]@(@($types|ForEach-Object {Join-Path $tools ($_+'.cs')})+@($s1path,(Join-Path $tools 'GMGN-ReadSupervisor.ps1')));$hashes=[string[]]@($paths|ForEach-Object {(Get-FileHash $_).Hash.ToLowerInvariant()})
$isPrimary=$Case.StartsWith('Primary') -and $Case -cne 'PrimaryAds'
if($isPrimary){
 $approved=Get-Content (Join-Path $repo '.local-tools\gmgn-approved-public-generation-23\package.json') -Raw|ConvertFrom-Json
 $sdk='%USERPROFILE%\AppData\Roaming\npm\node_modules\gmgn-cli\';$node='C:\Program Files\nodejs\node.exe'
 $base=@($approved.files|Where-Object {$_.path.StartsWith($sdk,[StringComparison]::Ordinal) -or $_.path -ceq $node -or
  $_.path -cin @((Join-Path $tools 'GMGN-ReadCliBootstrap.mjs'),(Join-Path $tools 'GMGN-SignedQuoteProbe.mjs'),(Join-Path $tools 'GMGN-SingleSwapTransport.mjs'))})
 if($base.Count -ne 169){throw 'OWN_PRIMARY_BASE'}
 $paths=[string[]](@($paths)+@($base|ForEach-Object {$_.path}));$hashes=[string[]](@($hashes)+@($base|ForEach-Object {$_.sha256}))
}
$source=[GmgnImmutableSources]::new('%USERPROFILE%','C:\Program Files\nodejs\node.exe',$paths,$hashes)
$s1hash=$source.HashForPath($s1path);$id=[guid]::NewGuid().ToString('N');$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$authDir=Join-Path (Join-Path $repo '.local-tools') ('gmgn-read-auth-'+[guid]::NewGuid().ToString('N'))
$auth=[GmgnReadLocalAuth]::CreateNew($authDir,$sid,[Text.Encoding]::ASCII.GetBytes('f'*64))
$recoveryPath=Join-Path $scope 'recovery';$null=[IO.Directory]::CreateDirectory($recoveryPath);$recovery=[GmgnReadDirectoryIdentity]::new($recoveryPath)
$authText=$auth.ReadPublicBinding() -join "`n";$sha=[Security.Cryptography.SHA256]::Create()
try{$authHash=([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($authText)))).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
$fields=[Collections.Generic.SortedDictionary[string,string]]::new([StringComparer]::Ordinal)
$values=@{schema_version='1';purpose='IMC_READ_INSTALL_PLAN_V1';installation_id=$id;source1_manifest=$s1path;source1_sha256=$s1hash;
 source1_fingerprint=$source.Fingerprint();auth_binding_sha256=$authHash;recovery_directory=$recovery.Path;recovery_identity=$recovery.Identity;
 owner_sid=$sid;primary_plan=(Join-Path (Join-Path $scope 'primary') 'package.json');primary_plan_sha256=('a'*64)}
$primaryPins=$null
if($isPrimary){
 $primaryFiles=[Collections.Generic.SortedDictionary[string,string]]::new([StringComparer]::Ordinal)
 foreach($file in $base){$primaryFiles.Add([string]$file.path,[string]$file.sha256)}
 foreach($name in @('GMGN-ReadBroker.ps1','GMGN-ReadProtocol.ps1','Invoke-GMGN-ReadBroker.ps1','GMGN-ReadClientBootstrap.ps1','GmgnRepairNamespace.cs','GmgnReadDirectoryIdentity.cs','GmgnRepairProcessBinding.cs','GmgnReadClientIdentity.cs')){
  $p=Join-Path $tools $name;$primaryFiles.Add($p,(Get-FileHash $p).Hash.ToLowerInvariant())
 }
 foreach($name in @('imc-execution.json','imc-gmgn-read-broker.json')){$p=Join-Path (Join-Path $repo 'config') $name;$primaryFiles.Add($p,(Get-FileHash $p).Hash.ToLowerInvariant())}
 if($Case -ceq 'PrimaryMissingSdk'){$missing=@($base|Where-Object {$_.path.StartsWith($sdk) -and $_.path -cne ($sdk+'dist\index.js')})[0].path;$null=$primaryFiles.Remove($missing)}
 if($Case -ceq 'PrimaryMissingContract'){$null=$primaryFiles.Remove((Join-Path $tools 'GMGN-ReadProtocol.ps1'))}
 $primaryFields=[Collections.Generic.SortedDictionary[string,object]]::new([StringComparer]::Ordinal)
 $primaryValues=@{schema_version='1';purpose='IMC_READ_PRIMARY_PLAN_V1';installation_id=$id;node_executable=$node;cli_entry=($sdk+'dist\index.js');read_bootstrap=(Join-Path $tools 'GMGN-ReadCliBootstrap.mjs');files=$primaryFiles}
 if($Case -ceq 'PrimaryWrongId'){$primaryValues.installation_id=[guid]::NewGuid().ToString('N')}
 if($Case -ceq 'PrimaryBadType'){$primaryValues.schema_version=1}
 if($Case -ceq 'PrimaryExtraField'){$primaryValues.extra='unused'}
 foreach($name in $primaryValues.Keys){$primaryFields.Add($name,$primaryValues[$name])}
 $primarySerializer=[System.Web.Script.Serialization.JavaScriptSerializer]::new();$primaryBody=$primarySerializer.Serialize($primaryFields)
 if($Case -ceq 'PrimaryDuplicate'){$primaryBody=$primaryBody.Substring(0,$primaryBody.Length-1)+',"schema_version":"1"}'}
 $null=[IO.Directory]::CreateDirectory((Split-Path $values.primary_plan));[IO.File]::WriteAllText($values.primary_plan,$primaryBody,[Text.UTF8Encoding]::new(($Case -ceq 'PrimaryBom')))
 $values.primary_plan_sha256=(Get-FileHash $values.primary_plan).Hash.ToLowerInvariant()
 $pPaths=[string[]](@($primaryFiles.Keys)+@($values.primary_plan));$pHashes=[string[]](@($primaryFiles.Values)+@($values.primary_plan_sha256))
 if($Case -ceq 'PrimaryExtraPin'){$pPaths=[string[]](@($pPaths)+@($s1path));$pHashes=[string[]](@($pHashes)+@($s1hash))}
 $primaryPins=[GmgnImmutableSources]::new('%USERPROFILE%',$node,$pPaths,$pHashes)
 if($Case -ceq 'PrimaryWrongHash'){$values.primary_plan_sha256='0'*64}
}
switch($Case){
 'WrongInstallId'{$values.installation_id=[guid]::NewGuid().ToString('N')}
 'WrongAuth'{$values.auth_binding_sha256='0'*64}
 'WrongRecovery'{$values.recovery_identity='0'*16+':'+('0'*32)}
 'WrongSource'{$values.source1_fingerprint='0'*64}
 'ForeignPrimary'{$values.primary_plan='\\foreign.invalid\share\package.json'}
 'PrimaryAds'{$values.primary_plan+=':extra'}
}
foreach($name in $values.Keys){$fields.Add($name,$values[$name])}
$serializer=[System.Web.Script.Serialization.JavaScriptSerializer]::new();$body=$serializer.Serialize($fields)
if($Case -ceq 'Noncanonical'){$body=' '+$body}
if($Case -ceq 'DuplicateField'){$body=$body.Substring(0,$body.Length-1)+',"schema_version":"1"}'}
if($Case -ceq 'ExtraField'){$body=$body.Substring(0,$body.Length-1)+',"extra":"ignored"}'}
if($Case -ceq 'WrongJsonType'){$body=$body.Replace('"schema_version":"1"','"schema_version":1')}
$planDir=Join-Path $scope 'plan';$null=[IO.Directory]::CreateDirectory($planDir);$planPath=Join-Path $planDir 'package.json'
[IO.File]::WriteAllText($planPath,$body,[Text.UTF8Encoding]::new(($Case -ceq 'Bom')));$planHash=(Get-FileHash $planPath).Hash.ToLowerInvariant()
$pins=[GmgnImmutableSources]::new('%USERPROFILE%','C:\Program Files\nodejs\node.exe',[string[]]@($planPath),[string[]]@($planHash))
$actionArguments='-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+$PSCommandPath+'" -Case '+$Case
if($Case -ceq 'ActorMismatch'){$actionArguments+=' -Unexpected'}
$plan=$null;$primary=$null;$code='';$checkpointPhase='NOT_ENTERED'
try{
 $plan=[GmgnReadInstallPlan]::new($source,$pins,$planPath,$planHash,$s1path,$s1hash,$auth,$recovery,$id,$actionArguments)
 switch($Case){'SourceFault'{$source.Abort()};'AuthFault'{$auth.Abort()};'PlanFault'{$plan.Abort()}}
 $plan.Check()
 if($Case.StartsWith('Checkpoint')){
  $f=[Reflection.BindingFlags]'Instance,NonPublic';$entry=$plan.GetType().GetMethod('CheckCheckpointEntry',$f)
  if($Case -ceq 'CheckpointSourceFault'){$source.Abort()}
  if($Case -ceq 'CheckpointPlanFault'){$plan.Abort()}
  if($Case -ceq 'CheckpointHashFinal'){
   $members=$source.GetType().GetField('members',$f).GetValue($source)
   $members[0].GetType().GetField('Hash',$f).SetValue($members[0],('0'*64))
  }
  $checkpointPhase='ENTRY';$null=$entry.Invoke($plan,$null)
  $checkpointPhase='FINAL'
  # Entry is no effect or immutable-inventory comparison authority.
  # A persisting private Hash mutation is rejected by the full FINAL.
  $plan.Check();$checkpointPhase='COMPLETE'
 }
 if($Case.StartsWith('Effect')){
  Add-Type -TypeDefinition @'
using System;using System.Reflection;using System.Threading;
public static class OwnPlanEffectBoundary {
 static string Call(object plan,Action effect){try{plan.GetType().GetMethod("InRetirementEffect",BindingFlags.Instance|BindingFlags.NonPublic).Invoke(plan,new object[]{effect});return "";}
  catch(Exception error){while(error.InnerException!=null)error=error.InnerException;return error.Message;}}
 static void Abort(object plan){plan.GetType().GetMethod("Abort").Invoke(plan,null);}
 public static void Run(object plan,string mode){
  int effects=0;
  if(mode=="EffectAbortFirst"){Abort(plan);if(Call(plan,delegate{effects++;})!="GMGN_INSTALL_RETIRE_CANCELLED"||effects!=0)throw new InvalidOperationException("OWN_EFFECT_AFTER_ABORT");return;}
  if(mode=="EffectUnderPlan"){
   object gate=plan.GetType().GetField("sync",BindingFlags.Instance|BindingFlags.NonPublic).GetValue(plan);string code=null;Thread worker=null;bool joined=false;
   Monitor.Enter(gate);try{worker=new Thread(delegate(){code=Call(plan,delegate{effects++;});});worker.IsBackground=true;worker.Start();joined=worker.Join(3000);}
   finally{Monitor.Exit(gate);if(worker!=null&&!joined&&!worker.Join(3000))throw new InvalidOperationException("OWN_EFFECT_WORKER_CLEANUP");}
   if(!joined||code!=""||effects!=1)throw new InvalidOperationException("OWN_EFFECT_PLAN_BACK_EDGE");return;
  }
  if(mode=="EffectFailure"){
   if(Call(plan,delegate{effects++;throw new InvalidOperationException("OWN_EFFECT_FAILURE");})!="OWN_EFFECT_FAILURE")throw new InvalidOperationException("OWN_EFFECT_FAILURE_LOST");
   Abort(plan);if(Call(plan,delegate{effects++;})!="GMGN_INSTALL_RETIRE_CANCELLED"||effects!=1)throw new InvalidOperationException("OWN_EFFECT_FAILURE_RETRY");return;
  }
  using(var entered=new ManualResetEvent(false))using(var release=new ManualResetEvent(false))using(var abortStarted=new ManualResetEvent(false))using(var abortDone=new ManualResetEvent(false)){
   string code=null,abortCode=null;
   var effectWorker=new Thread(delegate(){code=Call(plan,delegate{entered.Set();if(!release.WaitOne(3000))throw new InvalidOperationException("OWN_EFFECT_RELEASE_TIMEOUT");effects++;});});
   var abortWorker=new Thread(delegate(){abortStarted.Set();try{Abort(plan);abortCode="";}catch{abortCode="OWN_EFFECT_ABORT_FAILED";}finally{abortDone.Set();}});
   effectWorker.IsBackground=true;abortWorker.IsBackground=true;bool abortLaunched=false;
   try{
    effectWorker.Start();if(!entered.WaitOne(3000))throw new InvalidOperationException("OWN_EFFECT_NOT_ENTERED");
    abortWorker.Start();abortLaunched=true;if(!abortStarted.WaitOne(1000)||abortDone.WaitOne(30))throw new InvalidOperationException("OWN_EFFECT_ABORT_NOT_SERIALIZED");
   }finally{
    release.Set();if(!effectWorker.Join(3000)||abortLaunched&&!abortWorker.Join(3000))throw new InvalidOperationException("OWN_EFFECT_THREAD_CLEANUP");
   }
   if(code!=""||abortCode!=""||effects!=1||Call(plan,delegate{effects++;})!="GMGN_INSTALL_RETIRE_CANCELLED"||effects!=1)throw new InvalidOperationException("OWN_EFFECT_ONE_SHOT_CANCEL");
  }
 }
}
'@
  [OwnPlanEffectBoundary]::Run($plan,$Case)
 }
 if($Case.StartsWith('Warm')){
  if($Case -ceq 'WarmFile'){$plan.RequireWarmTaskAction()}else{
   $warmAction=$plan.BuildWarmTaskAction();$prefix='-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -EncodedCommand '
   if($Case -ceq 'WarmBuilder' -and $warmAction -cne $plan.BuildWarmTaskAction()){throw 'OWN_WARM_ACTION_NOT_DETERMINISTIC'}
   if($Case -ceq 'WarmOtherPlan'){
    $otherDir=Join-Path $scope 'other-plan';$null=[IO.Directory]::CreateDirectory($otherDir);$otherPath=Join-Path $otherDir 'package.json'
    [IO.File]::WriteAllText($otherPath,$body,[Text.UTF8Encoding]::new($false));$otherHash=(Get-FileHash $otherPath).Hash.ToLowerInvariant()
    $otherPin=[GmgnImmutableSources]::new('%USERPROFILE%','C:\Program Files\nodejs\node.exe',[string[]]@($otherPath),[string[]]@($otherHash))
    $other=[GmgnReadInstallPlan]::new($source,$otherPin,$otherPath,$otherHash,$s1path,$s1hash,$auth,$recovery,$id,$actionArguments)
    $warmAction=$other.BuildWarmTaskAction()
   }
   if($Case -cin @('WarmChangedPayload','WarmChangedLoader')){
    $decoded=[Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($warmAction.Substring($prefix.Length)))
    if($Case -ceq 'WarmChangedLoader'){$decoded=$decoded.Replace('GMGN_WARM_ACTION_HASH','GMGN_MUTATED_HASH')}else{
     $marker="`$imcWarmActionData='";$finish=$decoded.IndexOf("'",$marker.Length);$encoded=$decoded.Substring($marker.Length,$finish-$marker.Length)
     $data=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($encoded));$mutated=$data.Replace($id,('0'*32))
     if($mutated -ceq $data){throw 'OWN_WARM_ACTION_PAYLOAD_NOT_CHANGED'}
     $newData=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($mutated));$decoded=$decoded.Remove($marker.Length,$encoded.Length).Insert($marker.Length,$newData)
    }
    $warmAction=$prefix+[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($decoded))
   }
   if($Case -ceq 'WarmWhitespace'){$warmAction+=' '}
   if($Case -ceq 'WarmNonUtf16'){$warmAction=$prefix+'/w=='}
   $plan.ValidateWarmTaskAction($warmAction)
  }
 }
 if($isPrimary){
  $primary=$plan.BindPrimary($primaryPins)
  switch($Case){'PrimarySourceFault'{$primaryPins.Abort()};'PrimaryParentFault'{$plan.Abort()}}
  $primary.Check()
 }
}catch{$e=$_.Exception;while($e.InnerException){$e=$e.InnerException};$code=$e.Message}
$expected=switch($Case){'Normal'{''};'ActorMismatch'{'GMGN_ACTION_COMMAND_MISMATCH'};'Noncanonical'{'GMGN_INSTALL_PLAN_NONCANONICAL'};
 'DuplicateField'{'GMGN_INSTALL_PLAN_NONCANONICAL'};'Bom'{'GMGN_INSTALL_PLAN_NONCANONICAL'};'WrongJsonType'{'GMGN_INSTALL_PLAN_NONCANONICAL'};'ExtraField'{'GMGN_INSTALL_PLAN_FIELDS'};
 'SourceFault'{'GMGN_SOURCE_LOCKED'};'AuthFault'{'GMGN_AUTH_FAULT'};'PlanFault'{'GMGN_INSTALL_PLAN_FAULT'};'ForeignPrimary'{'GMGN_INSTALL_PLAN_PRIMARY'};'PrimaryAds'{'GMGN_INSTALL_PLAN_PRIMARY'};
 'PrimaryNormal'{''};'PrimaryMissingSdk'{'GMGN_PRIMARY_SDK_INCOMPLETE'};'PrimaryMissingContract'{'GMGN_PRIMARY_CONTRACT_INCOMPLETE'};
 'PrimaryExtraPin'{'GMGN_PRIMARY_PLAN_INVENTORY'};'PrimaryWrongHash'{'GMGN_PRIMARY_PLAN_BINDING'};'PrimaryWrongId'{'GMGN_PRIMARY_PLAN_CONTEXT'};
 'PrimaryDuplicate'{'GMGN_PRIMARY_PLAN_NONCANONICAL'};'PrimaryBom'{'GMGN_PRIMARY_PLAN_NONCANONICAL'};'PrimarySourceFault'{'GMGN_SOURCE_LOCKED'};
 'PrimaryParentFault'{'GMGN_INSTALL_PLAN_FAULT'};'PrimaryBadType'{'GMGN_PRIMARY_PLAN_FIELDS'};'PrimaryExtraField'{'GMGN_PRIMARY_PLAN_FIELDS'};
 'WarmBuilder'{''};'WarmOtherPlan'{'GMGN_WARM_ACTION_BINDING'};'WarmChangedPayload'{'GMGN_WARM_ACTION_BINDING'};'WarmChangedLoader'{'GMGN_WARM_ACTION_BINDING'};
 'WarmWhitespace'{'GMGN_WARM_ACTION_ENCODING'};'WarmNonUtf16'{'GMGN_WARM_ACTION_ENCODING'};'WarmFile'{'GMGN_WARM_ACTION_BINDING'};
 'EffectAbortFirst'{''};'EffectWins'{''};'EffectUnderPlan'{''};'EffectFailure'{''};
 'CheckpointEntry'{''};'CheckpointSourceFault'{'GMGN_SOURCE_LOCKED'};'CheckpointPlanFault'{'GMGN_INSTALL_PLAN_FAULT'};'CheckpointHashFinal'{'GMGN_INSTALL_PLAN_CHANGED'};
 default{'GMGN_INSTALL_PLAN_CONTEXT'}}
if($code -cne $expected){throw ('OWN_INSTALL_PLAN_WRONG_ERROR_'+$code)}
if($Case.StartsWith('Checkpoint')){
 $expectedPhase=switch($Case){'CheckpointEntry'{'COMPLETE'};'CheckpointHashFinal'{'FINAL'};default{'ENTRY'}}
 if($checkpointPhase -cne $expectedPhase){throw ('OWN_CHECKPOINT_WRONG_BOUNDARY_'+$checkpointPhase)}
}
if($Case -ceq 'Normal'){
 $snapshot=$plan.ReadBinding();if($plan.State -cne 'PLAN_BOUND' -or $snapshot[1] -cne $id -or $snapshot[3] -cne $planHash){throw 'OWN_PLAN_BINDING_FAILED'}
 $snapshot[1]='MUTATED';if($plan.ReadBinding()[1] -cne $id){throw 'OWN_PLAN_MUTABLE_BINDING'}
}
if($Case -ceq 'PrimaryNormal'){
 $snapshot=$primary.ReadBinding();if($primary.State -cne 'PRIMARY_BOUND' -or $snapshot[1] -cne $id -or $snapshot[4] -cne $values.primary_plan_sha256){throw 'OWN_PRIMARY_BINDING_FAILED'}
 $snapshot[1]='MUTATED';if($primary.ReadBinding()[1] -cne $id){throw 'OWN_PRIMARY_MUTABLE_BINDING'}
}
if($isPrimary -and $Case -cne 'PrimaryNormal'){
 if($plan.State -cne 'FAULT'){throw 'OWN_PRIMARY_PLAN_NOT_FAULTED'}
 if($primary){$repeat='';try{$null=$primary.ReadBinding()}catch{$e=$_.Exception;while($e.InnerException){$e=$e.InnerException};$repeat=$e.Message}
  if($primary.State -cne 'FAULT' -or $repeat -cne 'GMGN_PRIMARY_PLAN_FAULT'){throw 'OWN_PRIMARY_FAULT_RESET'}
 }
}
if($plan -and $Case -cin @('SourceFault','AuthFault','PlanFault')){
 $repeat='';try{$null=$plan.ReadBinding()}catch{$e=$_.Exception;while($e.InnerException){$e=$e.InnerException};$repeat=$e.Message}
 if($plan.State -cne 'FAULT' -or $repeat -cne 'GMGN_INSTALL_PLAN_FAULT'){throw 'OWN_PLAN_FAULT_RESET'}
}
if($Case.StartsWith('Warm') -and $Case -cne 'WarmBuilder'){
 if($plan.State -cne 'FAULT'){throw 'OWN_WARM_ACTION_FAULT_RESET'}
 $repeat='';try{$null=$plan.BuildWarmTaskAction()}catch{$e=$_.Exception;while($e.InnerException){$e=$e.InnerException};$repeat=$e.Message}
 if($repeat -cne 'GMGN_INSTALL_PLAN_FAULT'){throw 'OWN_WARM_ACTION_REBUILT_AFTER_FAULT'}
}
[pscustomobject]@{case=$Case;pass=$true;code=$code;provider_calls=0;dispatch_granted=$false;production_verified=$false}|ConvertTo-Json -Compress
