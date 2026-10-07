# Stage only: source/forensic/process retention. No read locator or SDK dispatch.
# A separately reviewed primary Broker/Protocol generation is required later.
param([Parameter(Mandatory=$true)][string]$ManifestPath,
 [Parameter(Mandatory=$true)][ValidatePattern('\A[0-9a-f]{64}\z')][string]$ExpectedGenerationSha256,
 [Parameter(Mandatory=$true)][ValidatePattern('\A[0-9a-f]{64}\z')][string]$ExpectedSelfSha256,
 [Parameter(Mandatory=$true)][string]$RecoveryDirectory,[string]$FixtureScope,
 [ValidateSet('None','NAMESPACE_PINNED','SOURCE_PINNED','COPIED_MEMBER','MANIFEST_FLUSHED')][string]$FixtureBoundary='None',
 [string]$WarmInstallContract,[string]$ExpectedWarmArguments)
$ErrorActionPreference='Stop';$repo=Split-Path -Parent $PSScriptRoot;$tools=$PSScriptRoot
$global:IMCGmgnSupervisorStage=[pscustomobject]@{Phase='STARTING';Faulted=$false;ErrorCode='';ProofPublished=$false;Streams=[Collections.Generic.List[object]]::new();Proofs=[Collections.Generic.List[object]]::new();Snapshot=$null;Sources=$null;Lifetime=$null;Capture=$null;RoleValidated=$false;ValidatedCapture=$null;Directory=$null;Retirement=$null;CommandId='';LegacyBindings=$null;InstanceId='';NativeSelf=$null}
$stage=$global:IMCGmgnSupervisorStage
$stage|Add-Member -NotePropertyName WarmMode -NotePropertyValue ($PSBoundParameters.ContainsKey('WarmInstallContract') -or $PSBoundParameters.ContainsKey('ExpectedWarmArguments'))
foreach($name in @('Authentication','InstallPlan','PlanPins','WarmOrigin')){$stage|Add-Member -NotePropertyName $name -NotePropertyValue $null}
function Read-StageBootstrap([string]$Path,[string]$Expected){
 $stream=[IO.FileStream]::new($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read);$stage.Streams.Add($stream)
 if($stream.Length -gt 2097152){throw 'GMGN_STAGE_BOOTSTRAP_SIZE'}
 $sha=[Security.Cryptography.SHA256]::Create();try{$hash=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
 if($hash -cne $Expected){throw 'GMGN_STAGE_BOOTSTRAP_HASH'}
 $stream.Position=0;$reader=[IO.StreamReader]::new($stream,[Text.UTF8Encoding]::new($false,$true),$true,4096,$true)
 try{$text=$reader.ReadToEnd()}finally{$reader.Dispose()}
 $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($text,$Path,[ref]$tokens,[ref]$errors)
 if($errors.Count){throw 'GMGN_STAGE_BOOTSTRAP_PARSE'};return $ast.GetScriptBlock()
}
function Write-StageProof([string]$Name,$Body){
 $text=$Body|ConvertTo-Json -Depth 8 -Compress
 if($stage.WarmMode){
  if(-not $stage.Authentication){throw 'GMGN_STAGE_AUTH_UNAVAILABLE'}
  if($stage.Phase -cne 'FAULT'){$stage.InstallPlan.Check();$stage.Sources.Check()}
  $stage.Authentication.Check();$signature=$stage.Authentication.Sign($text);$stage.Authentication.Check()
 }else{
  $token=$env:IMC_GMGN_IPC_TOKEN;if($token -cnotmatch '\A[0-9a-f]{64}\z'){throw 'GMGN_STAGE_AUTH_UNAVAILABLE'}
  $mac=[Security.Cryptography.HMACSHA256]::new([Text.Encoding]::ASCII.GetBytes($token))
  try{$signature=[BitConverter]::ToString($mac.ComputeHash([Text.Encoding]::UTF8.GetBytes($text))).Replace('-','').ToLowerInvariant()}finally{$mac.Dispose()}
 }
 $bytes=[Text.UTF8Encoding]::new($false).GetBytes((@{body=$text;mac=$signature}|ConvertTo-Json -Compress))
 # Return only after a checked writer close, final READ/shareREAD pin and
 # full native/ACL/content revalidation. Keep the final proof pin for life.
 $proof=[GmgnReadProofPin]::new($stage.Directory,$Name,$bytes);$proof.Check();$stage.Proofs.Add($proof)
 if($stage.WarmMode -and $stage.Phase -cne 'FAULT'){$stage.InstallPlan.Check();$stage.Authentication.Check();$stage.Sources.Check()}
}
try{
 $null=Read-StageBootstrap $PSCommandPath $ExpectedSelfSha256
 $entry=Read-StageBootstrap (Join-Path $tools 'GMGN-TrustedGeneration.ps1') '350f7a69845a226fe8a9f712fd7bc76eb934922080d11d6998405a0f39df1cfd'
 $stage.Snapshot=& $entry $ManifestPath $ExpectedGenerationSha256
 if(-not $stage.Snapshot.Complete -or $stage.Snapshot.Faulted){throw 'GMGN_STAGE_SNAPSHOT'}
 $manifest=[Text.UTF8Encoding]::new($false,$true).GetString($stage.Snapshot.Bytes[$ManifestPath])|ConvertFrom-Json
 if($manifest.stage_contract -cne 'BOUNDED_READ_RECOVERY_WAIT_CODE' -or $manifest.primary_code_included -ne $false -or
  @($manifest.capture_inventory).Count -lt 1 -or @($manifest.capture_inventory).Count -gt 512){throw 'GMGN_STAGE_MANIFEST_CONTRACT'}
 $types=@('GmgnRepairNamespace','GmgnImmutableSources','GmgnRepairProcessBinding','GmgnLegacyCapture','GmgnLegacyLifetimePins','GmgnReadDirectoryIdentity','GmgnReadProcessLifetime','GmgnLegacyRetirement','GmgnSupervisorReadContext','GmgnScheduledActionIdentity','GmgnSupervisorPipe','GmgnSourcePinBinding','GmgnPinTransferHandshake','GmgnSupervisorSourceAdmission','GmgnReadLocalAuth','GmgnHandoffCompletion','GmgnReadProofPin')
 $types+=@('GmgnReadInstallPlan','GmgnReadOwnerProof')
 foreach($name in $types){if($name -as [type]){throw 'GMGN_STAGE_NATIVE_TYPE_PRESENT'}}
 foreach($file in $stage.Snapshot.Files){if($file.path -cin @((Join-Path $PSScriptRoot 'GMGN-ReadProtocol.ps1'),(Join-Path $PSScriptRoot 'GMGN-ReadBroker.ps1'))){throw 'GMGN_STAGE_PRIMARY_CODE_PINNED'}}
 $sources=[string[]]@($types|ForEach-Object {
  $path=Join-Path $tools ($_+'.cs');if(-not $stage.Snapshot.Bytes.ContainsKey($path)){throw 'GMGN_STAGE_NATIVE_UNPINNED'}
  [Text.UTF8Encoding]::new($false,$true).GetString($stage.Snapshot.Bytes[$path]).TrimStart([char]0xfeff)
 })
 $authPartial=Join-Path $tools 'GmgnLegacyRetirement.Auth.cs'
 if(-not $stage.Snapshot.Bytes.ContainsKey($authPartial)){throw 'GMGN_STAGE_NATIVE_UNPINNED'}
 $sources=[string[]](@($sources)+@([Text.UTF8Encoding]::new($false,$true).GetString($stage.Snapshot.Bytes[$authPartial]).TrimStart([char]0xfeff)))
 $warmPartial=Join-Path $tools 'GmgnReadInstallPlan.Warm.cs'
 if(-not $stage.Snapshot.Bytes.ContainsKey($warmPartial)){throw 'GMGN_STAGE_NATIVE_UNPINNED'}
 $sources=[string[]](@($sources)+@([Text.UTF8Encoding]::new($false,$true).GetString($stage.Snapshot.Bytes[$warmPartial]).TrimStart([char]0xfeff)))
 $rootPartial=Join-Path $tools 'GmgnReadInstallPlan.Root.cs'
 if(-not $stage.Snapshot.Bytes.ContainsKey($rootPartial)){throw 'GMGN_STAGE_NATIVE_UNPINNED'}
 $sources=[string[]](@($sources)+@([Text.UTF8Encoding]::new($false,$true).GetString($stage.Snapshot.Bytes[$rootPartial]).TrimStart([char]0xfeff)))
 $legacyPartial=Join-Path $tools 'GmgnReadInstallPlan.Legacy.cs'
 if(-not $stage.Snapshot.Bytes.ContainsKey($legacyPartial)){throw 'GMGN_STAGE_NATIVE_UNPINNED'}
 $sources=[string[]](@($sources)+@([Text.UTF8Encoding]::new($false,$true).GetString($stage.Snapshot.Bytes[$legacyPartial]).TrimStart([char]0xfeff)))
 $senderPartial=Join-Path $tools 'GmgnReadInstallPlan.Sender.cs'
 if(-not $stage.Snapshot.Bytes.ContainsKey($senderPartial)){throw 'GMGN_STAGE_NATIVE_UNPINNED'}
 $sources=[string[]](@($sources)+@([Text.UTF8Encoding]::new($false,$true).GetString($stage.Snapshot.Bytes[$senderPartial]).TrimStart([char]0xfeff)))
 $retirePartial=Join-Path $tools 'GmgnReadInstallPlan.Retire.cs'
 if(-not $stage.Snapshot.Bytes.ContainsKey($retirePartial)){throw 'GMGN_STAGE_NATIVE_UNPINNED'}
 $sources=[string[]](@($sources)+@([Text.UTF8Encoding]::new($false,$true).GetString($stage.Snapshot.Bytes[$retirePartial]).TrimStart([char]0xfeff)))
 $compiler=[Microsoft.CSharp.CSharpCodeProvider]::new();$parameters=[CodeDom.Compiler.CompilerParameters]::new();$parameters.GenerateInMemory=$true
 $null=$parameters.ReferencedAssemblies.Add('System.dll');$null=$parameters.ReferencedAssemblies.Add('System.Core.dll')
 $null=$parameters.ReferencedAssemblies.Add('System.Web.Extensions.dll')
 $null=$parameters.ReferencedAssemblies.Add([Management.Automation.PSObject].Assembly.Location)
 try{$compiled=$compiler.CompileAssemblyFromSource($parameters,$sources)}finally{$compiler.Dispose()}
 if($compiled.Errors.HasErrors){throw 'GMGN_STAGE_NATIVE_COMPILE'}
 foreach($name in $types){$type=$name -as [type];if(-not $type -or -not [object]::ReferenceEquals($type.Assembly,$compiled.CompiledAssembly)){throw 'GMGN_STAGE_NATIVE_PROVENANCE'}}
 # The manifest's external hash is an additional native member. It is not
 # inserted into manifest.files, which would create a self-hash cycle.
 $stage.Sources=[GmgnImmutableSources]::new('%USERPROFILE%','C:\Program Files\nodejs\node.exe',[string[]](@($stage.Snapshot.Files|ForEach-Object {$_.path})+@($ManifestPath)),[string[]](@($stage.Snapshot.Files|ForEach-Object {$_.sha256})+@($ExpectedGenerationSha256)))
 $recovery=[IO.Path]::GetFullPath($RecoveryDirectory)
 if($recovery -cne $RecoveryDirectory -or $recovery -cnotmatch '\\[.]local-tools\\gmgn-read-recovery-[0-9a-f]{32}$'){throw 'GMGN_STAGE_RECOVERY_PATH'}
 $stage.Directory=[GmgnReadDirectoryIdentity]::new($recovery)
 if($stage.WarmMode){
  if(-not $PSBoundParameters.ContainsKey('WarmInstallContract') -or -not $PSBoundParameters.ContainsKey('ExpectedWarmArguments') -or
   -not $WarmInstallContract -or $WarmInstallContract.Length -gt 65536 -or -not $ExpectedWarmArguments -or $FixtureScope -or $FixtureBoundary -cne 'None'){throw 'GMGN_STAGE_WARM_CONTRACT'}
  try{$warm=[Text.UTF8Encoding]::new($false,$true).GetString([Convert]::FromBase64String($WarmInstallContract))|ConvertFrom-Json -ErrorAction Stop}catch{throw 'GMGN_STAGE_WARM_CONTRACT'}
  $fields=@('schema_version','purpose','installation_id','install_plan','install_plan_sha256','source1_manifest','source1_sha256','source1_fingerprint','auth_binding_sha256','auth_binding','recovery_directory','recovery_identity','primary_plan','primary_plan_sha256','bootstrap','bootstrap_sha256')
  if(@($warm.PSObject.Properties).Count -ne $fields.Count -or @($warm.PSObject.Properties.Name|Where-Object {$_ -cnotin $fields}).Count){throw 'GMGN_STAGE_WARM_FIELDS'}
  foreach($name in $fields){if($warm.$name -isnot [string] -or -not $warm.$name){throw 'GMGN_STAGE_WARM_FIELDS'}}
  if($warm.schema_version -cne '1' -or $warm.purpose -cne 'IMC_WARM_TASK_ACTION_V1' -or $warm.source1_manifest -cne $ManifestPath -or
   $warm.source1_sha256 -cne $ExpectedGenerationSha256 -or $warm.recovery_directory -cne $recovery -or $warm.recovery_identity -cne $stage.Directory.Identity -or
   $warm.bootstrap -cne $PSCommandPath -or $warm.bootstrap_sha256 -cne $ExpectedSelfSha256 -or
   $warm.install_plan_sha256 -cnotmatch '\A[0-9a-f]{64}\z' -or $warm.installation_id -cnotmatch '\A[0-9a-f]{32}\z' -or
   -not $warm.install_plan.StartsWith((Join-Path $repo '.local-tools')+'\',[StringComparison]::Ordinal) -or $warm.install_plan.IndexOf(':',2) -ge 0 -or
   $warm.install_plan.IndexOf('/') -ge 0 -or [IO.Path]::GetFullPath($warm.install_plan) -cne $warm.install_plan -or [IO.Path]::GetFileName($warm.install_plan) -cne 'package.json'){throw 'GMGN_STAGE_WARM_CONTEXT'}
  try{$parsedAuth=$warm.auth_binding|ConvertFrom-Json -ErrorAction Stop;$authBinding=@($parsedAuth)}catch{throw 'GMGN_STAGE_WARM_AUTH'}
  if($authBinding.Count -ne 6 -or $authBinding[0] -cne '1'){throw 'GMGN_STAGE_WARM_AUTH'}
  foreach($value in $authBinding){if($value -isnot [string] -or -not $value){throw 'GMGN_STAGE_WARM_AUTH'}}
  $stage.Authentication=[GmgnReadLocalAuth]::OpenExisting($authBinding[1],$authBinding[2],$authBinding[3],$authBinding[4],$authBinding[5])
  $stage.PlanPins=[GmgnImmutableSources]::new('%USERPROFILE%','C:\Program Files\nodejs\node.exe',[string[]]@($warm.install_plan),[string[]]@($warm.install_plan_sha256))
  $stage.InstallPlan=[GmgnReadInstallPlan]::new($stage.Sources,$stage.PlanPins,$warm.install_plan,$warm.install_plan_sha256,$ManifestPath,$ExpectedGenerationSha256,$stage.Authentication,$stage.Directory,$warm.installation_id,$ExpectedWarmArguments)
  $stage.InstallPlan.RequireWarmTaskAction()
  if($manifest.warm_action_challenge -isnot [string] -or $manifest.warm_action_challenge -cnotmatch '\A[0-9a-f]{64}\z'){throw 'GMGN_STAGE_WARM_CHALLENGE'}
  $stage.Phase='WARM_PLAN_BOUND';$stage.NativeSelf=[GmgnRepairProcessBinding]::Read([Diagnostics.Process]::GetCurrentProcess())
  $sha=[Security.Cryptography.SHA256]::Create();try{$actionHash=([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($ExpectedWarmArguments)))).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
  Write-StageProof 'stage-ready.json' @{schema_version=1;kind='WARM_ACTION_PROBE';phase=$stage.Phase;self=$stage.NativeSelf;installation_id=$warm.installation_id;
   generation_sha256=$ExpectedGenerationSha256;install_plan_sha256=$warm.install_plan_sha256;auth_binding_sha256=$warm.auth_binding_sha256;
   action_sha256=$actionHash;challenge=$manifest.warm_action_challenge;directory=$recovery;directory_identity=$stage.Directory.Identity;
   read_address_published=$false;legacy_touched=$false;sdk_granted=$false;provider_calls=0}
  $stage.ProofPublished=$true
  if($stage.InstallPlan.HasWarmSourceHandoff()){
   $stage.WarmOrigin=$stage.InstallPlan.CompleteWarmSourceHandoff();$stage.WarmOrigin.Check();$stage.InstallPlan.Check()
   $stage.Phase='SOURCE_BOUND'
   Write-StageProof 'warm-source.json' @{schema_version=1;kind='WARM_SOURCE_PROBE';phase=$stage.Phase;self=$stage.NativeSelf;installation_id=$warm.installation_id;
    generation_sha256=$ExpectedGenerationSha256;install_plan_sha256=$warm.install_plan_sha256;challenge=$manifest.warm_action_challenge;
    origin=$stage.WarmOrigin.ReadBinding();read_address_published=$false;legacy_touched=$false;sdk_granted=$false;provider_calls=0}
  }
  # Every Warm invocation is isolated here. There is no fallthrough to the
  # legacy lifetime/capture, retirement command loop or SDK process factory.
  while($true){$stage.InstallPlan.Check();$stage.Authentication.Check();$stage.Directory.Check();$stage.Sources.Check();if($stage.WarmOrigin){$stage.WarmOrigin.Check()};foreach($proof in $stage.Proofs){$proof.Check()};Start-Sleep -Milliseconds 500}
 }
 $source='%USERPROFILE%\AppData\Local\Temp\imc-gmgn-broker-3904'
 if($FixtureScope){
  if($FixtureScope -cnotmatch '\\[.]local-tools\\legacy-capture-native-[0-9a-f]{32}$'){throw 'GMGN_STAGE_FIXTURE_PATH'}
  $source=Join-Path $FixtureScope 'legacy'
  $fixture=Get-Content -LiteralPath (Join-Path $FixtureScope 'pair-ready.json') -Raw|ConvertFrom-Json
  $stage.Lifetime=[GmgnLegacyLifetimePins]::new([int]$fixture.broker[0],$fixture.broker[2],[int]$fixture.parent[0],$fixture.parent[2],$fixture.parent[3])
 }else{$stage.Lifetime=[GmgnLegacyLifetimePins]::new(14552,'2026-10-02T15:14:56.6594833Z',3904,'2026-10-02T15:14:56.3325126Z',(Join-Path $PSHOME 'powershell.exe'))}
 if($FixtureBoundary -cne 'None'){
  if(-not $FixtureScope){throw 'GMGN_STAGE_FIXTURE_BOUNDARY_FORBIDDEN'}
  # Own tests pause a specific boundary, then force actual native owner exit.
  $probeCode='using System;using System.IO;using System.Threading;using System.Diagnostics;public static class OwnCaptureBoundary{public static Action<string,int> Create(string scope,string wanted){return delegate(string phase,int index){if(phase!=wanted||index!=0)return;File.WriteAllText(Path.Combine(scope,"capture-boundary"),phase);var clock=Stopwatch.StartNew();while(!File.Exists(Path.Combine(scope,"release-capture-boundary"))&&clock.Elapsed.TotalSeconds<8)Thread.Sleep(5);if(!File.Exists(Path.Combine(scope,"release-capture-boundary")))throw new InvalidOperationException("GMGN_OWN_CAPTURE_BOUNDARY_TIMEOUT");};}}'
  Add-Type -TypeDefinition $probeCode
  [GmgnLegacyCapture].GetField('fixtureProbe',[Reflection.BindingFlags]'Static,NonPublic').SetValue($null,[OwnCaptureBoundary]::Create($FixtureScope,$FixtureBoundary))
 }
 $capturePath=Join-Path $recovery 'capture';$null=[IO.Directory]::CreateDirectory($capturePath)
 $stage.Capture=[GmgnLegacyCapture]::new($source,$capturePath,[string[]]$manifest.capture_inventory,60,$stage.Lifetime)
 $stage.Retirement=[GmgnLegacyRetirement]::new($stage.Sources,$stage.Lifetime,$stage.Capture,$source,$FixtureScope)
 $instance=$stage.Retirement.InstanceId
 $stage.RoleValidated=$true;$stage.ValidatedCapture=$stage.Capture
 $stage.Lifetime.CheckAlive();$stage.Sources.Check();$stage.Capture.Copy();$stage.Lifetime.CheckAlive();$stage.Sources.Check();$stage.Capture.CheckCommitted()
 if(-not $stage.Capture.Committed -or -not $stage.RoleValidated -or -not [object]::ReferenceEquals($stage.Capture,$stage.ValidatedCapture)){throw 'GMGN_STAGE_CAPTURE_ADMISSION'}
 $stage.Phase='WAIT_CODE'
 $nativeSelf=[GmgnRepairProcessBinding]::Read([Diagnostics.Process]::GetCurrentProcess())
 Write-StageProof 'stage-ready.json' @{schema_version=1;phase=$stage.Phase;generation_sha256=$ExpectedGenerationSha256;self=$nativeSelf;
  directory=$recovery;directory_identity=$stage.Directory.Identity;legacy_instance_id=$instance;legacy_bindings=$stage.Lifetime.Bindings();
  bounded_capture_committed=$true;legacy_role_validated=$true;sdk_sources=$stage.Sources.Count;source_fingerprint=$stage.Retirement.GenerationFingerprint;provider_calls=0;read_address_published=$false}
 $stage.ProofPublished=$true
 $stage.NativeSelf=$nativeSelf;$stage.LegacyBindings=$stage.Lifetime.Bindings();$stage.InstanceId=$instance
 $commandPath=Join-Path $tools 'GMGN-ReadSupervisorCommands.ps1';$tokens=$null;$errors=$null
 $commandAst=[Management.Automation.Language.Parser]::ParseInput($stage.Sources.ReadText($commandPath),$commandPath,[ref]$tokens,[ref]$errors)
 if($errors.Count){throw 'GMGN_STAGE_COMMAND_SOURCE_PARSE'};. ($commandAst.GetScriptBlock())
 while($true){
  foreach($proof in $stage.Proofs){$proof.Check()}
  $stage.Sources.Check();$stage.Directory.Check();$stage.Capture.CheckCommitted();$null=$stage.Lifetime.ObserveOriginalExits()
  if($stage.Phase -ceq 'WAIT_CODE'){Invoke-GmgnStageRetireCommand $stage $recovery $ExpectedGenerationSha256 $FixtureScope}
  # No implicit primary code acceptance, SDK launch, locator or real gate.
  Start-Sleep -Milliseconds 500
 }
}catch{
 if($stage.Capture){$stage.Capture.Invalidate()}
 $stage.Faulted=$true;$stage.Phase='FAULT'
 $stage.ErrorCode=[string]$_.Exception.Message
 $cause=$_.Exception
 while($cause -and $stage.ErrorCode -cnotmatch '\AGMGN_[A-Z0-9_]+\z'){$stage.ErrorCode=[string]$cause.Message;$cause=$cause.InnerException}
 if($stage.ErrorCode -cnotmatch '\AGMGN_[A-Z0-9_]+\z'){$stage.ErrorCode='GMGN_STAGE_INTERNAL_ERROR'}
 if($stage.Directory){try{Write-StageProof 'stage-fault.json' @{schema_version=1;phase='FAULT';code=$stage.ErrorCode;provider_calls=0;read_address_published=$false}}catch{}}
 # Keep all acquired handles after FAULT until the process itself ends.
 while($true){Start-Sleep -Milliseconds 500}
}
