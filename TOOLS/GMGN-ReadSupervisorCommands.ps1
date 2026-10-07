# Source-pinned, one-shot lifecycle commands. No trading or provider requests.
function Read-GmgnStageAuthenticatedFile {
 param($Stage,[string]$Path,[int]$MaxBytes=32768)
 $stream=[IO.FileStream]::new($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read);$Stage.Streams.Add($stream)
 if($stream.Length -le 0 -or $stream.Length -gt $MaxBytes){throw 'GMGN_STAGE_COMMAND_SIZE'}
 $bytes=[byte[]]::new([int]$stream.Length);$offset=0
 while($offset -lt $bytes.Length){$count=$stream.Read($bytes,$offset,$bytes.Length-$offset);if($count -le 0){throw 'GMGN_STAGE_COMMAND_SHORT'};$offset+=$count}
 try{$packet=[Text.UTF8Encoding]::new($false,$true).GetString($bytes)|ConvertFrom-Json}catch{throw 'GMGN_STAGE_COMMAND_JSON'}
 if(@($packet.PSObject.Properties).Count -ne 2 -or @($packet.PSObject.Properties.Name|Where-Object {$_ -cnotin @('body','mac')}).Count -or
  $packet.body -isnot [string] -or $packet.mac -isnot [string] -or $packet.mac -cnotmatch '\A[0-9a-f]{64}\z'){throw 'GMGN_STAGE_COMMAND_ENVELOPE'}
 if($Stage.WarmMode){
  $expected=$Stage.Sources.GetType().Assembly.GetType('GmgnReadLocalAuth',$false,$false)
  if($null -eq $Stage.Authentication -or $null -eq $expected -or -not [object]::ReferenceEquals($Stage.Authentication.GetType(),$expected)){throw 'GMGN_STAGE_COMMAND_AUTH_UNAVAILABLE'}
  $Stage.InstallPlan.Check();$Stage.Authentication.Check()
  if(-not $Stage.Authentication.Verify($packet.body,$packet.mac)){throw 'GMGN_STAGE_COMMAND_AUTH'}
  $Stage.Authentication.Check();$Stage.InstallPlan.Check()
 }else{
  $token=$env:IMC_GMGN_IPC_TOKEN;if($token -cnotmatch '\A[0-9a-f]{64}\z'){throw 'GMGN_STAGE_COMMAND_AUTH_UNAVAILABLE'}
  $hmac=[Security.Cryptography.HMACSHA256]::new([Text.Encoding]::ASCII.GetBytes($token))
  try{$mac=$hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes($packet.body))}finally{$hmac.Dispose()}
  $difference=0;foreach($i in 0..31){$difference=$difference -bor ($mac[$i] -bxor [Convert]::ToByte($packet.mac.Substring($i*2,2),16))}
  if($difference -ne 0){throw 'GMGN_STAGE_COMMAND_AUTH'}
 }
 try{$body=$packet.body|ConvertFrom-Json}catch{throw 'GMGN_STAGE_COMMAND_BODY'}
 $sha=[Security.Cryptography.SHA256]::Create();try{$digest=[BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
 return [pscustomobject]@{Body=$body;Sha256=$digest}
}
function Assert-GmgnStageCommandFields($Body,[string[]]$Fields){
 if(@($Body.PSObject.Properties).Count -ne $Fields.Count -or @($Body.PSObject.Properties.Name|Where-Object {$_ -cnotin $Fields}).Count){throw 'GMGN_STAGE_COMMAND_FIELDS'}
}
function Assert-GmgnStageStringFields($Body,[string[]]$Fields){
 foreach($field in $Fields){if($Body.$field -isnot [string]){throw 'GMGN_STAGE_COMMAND_SCALAR_TYPE'}}
}
function Assert-GmgnOwnerProofFreshness {
 param($Owner,[long]$Now)
 $observed=0L;$frequency=0L
 if($Owner.observed_qpc -isnot [string] -or $Owner.qpc_frequency -isnot [string] -or
  -not [long]::TryParse($Owner.observed_qpc,[ref]$observed) -or -not [long]::TryParse($Owner.qpc_frequency,[ref]$frequency) -or
  $frequency -ne [Diagnostics.Stopwatch]::Frequency -or $observed -le 0 -or $observed -gt $Now -or ($Now-$observed)/[double]$frequency -gt 15){throw 'GMGN_STAGE_OWNER_PROOF_STALE'}
 [pscustomobject]@{Observed=$observed;Frequency=$frequency}
}
function Assert-GmgnStageOwnerProofBody {
 param($Owner,$LegacyBindings,[string]$FixtureScope)
 # Validation only. Caller-provided facts never create retirement authority.
 Assert-GmgnStageCommandFields $Owner @('schema_version','kind','owner_pid','owner_birth','broker_pid','broker_birth','broker_directory','loaded_source_sha256','loaded_cleanup_sha256','original_script','original_line','original_broker_handle_verified','variables_changed','provider_calls','observed_qpc','qpc_frequency')
 Assert-GmgnStageStringFields $Owner @('kind','owner_birth','broker_birth','broker_directory','loaded_source_sha256','loaded_cleanup_sha256','original_script','observed_qpc','qpc_frequency')
 if($Owner.schema_version -isnot [int] -or $Owner.schema_version -ne 1 -or $Owner.kind -cne 'ORIGINAL_OWNER_RETIREMENT_EVIDENCE' -or
  $Owner.owner_pid -isnot [int] -or $Owner.owner_pid -ne [int]$LegacyBindings[1][0] -or $Owner.owner_birth -cne $LegacyBindings[1][2] -or
  $Owner.broker_pid -isnot [int] -or $Owner.broker_pid -ne [int]$LegacyBindings[0][0] -or $Owner.broker_birth -cne $LegacyBindings[0][2] -or
  $Owner.original_broker_handle_verified -isnot [bool] -or -not $Owner.original_broker_handle_verified -or
  $Owner.variables_changed -isnot [bool] -or $Owner.variables_changed -or $Owner.provider_calls -isnot [int] -or $Owner.provider_calls -ne 0 -or
  $Owner.original_line -isnot [int] -or $Owner.loaded_source_sha256 -cnotmatch '\A[0-9a-f]{64}\z' -or $Owner.loaded_cleanup_sha256 -cnotmatch '\A[0-9a-f]{64}\z'){throw 'GMGN_STAGE_OWNER_PROOF_BINDING'}
 if($FixtureScope){
  if($FixtureScope -cnotmatch '\\[.]local-tools\\legacy-capture-native-[0-9a-f]{32}$' -or $Owner.original_script -cne (Join-Path $FixtureScope 'parent.ps1') -or
   $Owner.broker_directory -cne (Join-Path $FixtureScope 'legacy')){throw 'GMGN_STAGE_OWNER_PROOF_FIXTURE'}
 }elseif($Owner.loaded_source_sha256 -cne '2243b01e0c24c60aa3bd6906486424a66007d33e063100fe949968ec01500ed6' -or
  $Owner.loaded_cleanup_sha256 -cne 'b16f53d6cc9d6e679c11d210196a4a84b945e419753c383d6ec67be5a5d6be50' -or
  $Owner.original_script -cne (Join-Path $PSScriptRoot 'Start-IMC-Work.ps1') -or $Owner.original_line -isnot [int] -or $Owner.original_line -ne 215 -or
  $Owner.broker_directory -cne '%USERPROFILE%\AppData\Local\Temp\imc-gmgn-broker-3904'){throw 'GMGN_STAGE_OWNER_LOADED_SOURCE'}
 Assert-GmgnOwnerProofFreshness $Owner ([Diagnostics.Stopwatch]::GetTimestamp())
}
function Invoke-GmgnStageRetireCommand {
 param($Stage,[string]$RecoveryDirectory,[string]$GenerationSha256,[string]$FixtureScope)
 $path=Join-Path $RecoveryDirectory 'retire.request.json'
 if(-not [IO.File]::Exists($path)){return}
 # The production handover is not admitted until the independent task,
 # bootstrap and complete native pin-transfer contracts are implemented.
 # Authenticated retirement requests cannot bypass that missing contract.
 if(-not $FixtureScope){throw 'GMGN_STAGE_PRODUCTION_HANDOVER_NOT_ADMITTED'}
 if($Stage.CommandId -or $Stage.Phase -cne 'WAIT_CODE'){throw 'GMGN_STAGE_COMMAND_USED'}
 foreach($proofPin in $Stage.Proofs){$proofPin.Check()}
 $Stage.Sources.Check();$Stage.Directory.Check();$Stage.Capture.CheckCommitted()
 $request=(Read-GmgnStageAuthenticatedFile $Stage $path).Body
 Assert-GmgnStageCommandFields $request @('schema_version','command','command_id','generation_sha256','source_fingerprint','supervisor','directory','directory_identity','legacy_instance_id','legacy_bindings','owner_proof_path','owner_proof_sha256')
 Assert-GmgnStageStringFields $request @('command','command_id','generation_sha256','source_fingerprint','directory','directory_identity','legacy_instance_id','owner_proof_path','owner_proof_sha256')
 if($request.schema_version -isnot [int] -or $request.schema_version -ne 1 -or $request.command -cne 'RETIRE_LEGACY' -or
  $request.command_id -isnot [string] -or $request.command_id -cnotmatch '\A[0-9a-f]{32}\z' -or $request.generation_sha256 -cne $GenerationSha256 -or
  $request.source_fingerprint -cne $Stage.Retirement.GenerationFingerprint -or $request.directory -cne $RecoveryDirectory -or
  $request.directory_identity -cne $Stage.Directory.Identity -or $request.legacy_instance_id -cne $Stage.InstanceId -or
  @($request.supervisor).Count -ne 4 -or @($request.legacy_bindings).Count -ne 2 -or $request.owner_proof_path -isnot [string] -or
  $request.owner_proof_sha256 -isnot [string] -or $request.owner_proof_sha256 -cnotmatch '\A[0-9a-f]{64}\z'){throw 'GMGN_STAGE_COMMAND_CONTEXT'}
 foreach($i in 0..3){if($request.supervisor[$i] -isnot [string] -or $request.supervisor[$i] -cne $Stage.NativeSelf[$i]){throw 'GMGN_STAGE_COMMAND_SUPERVISOR'}}
 foreach($role in 0..1){if(@($request.legacy_bindings[$role]).Count -ne 4){throw 'GMGN_STAGE_COMMAND_LEGACY'};foreach($i in 0..3){
  if($request.legacy_bindings[$role][$i] -isnot [string] -or $request.legacy_bindings[$role][$i] -cne $Stage.LegacyBindings[$role][$i]){throw 'GMGN_STAGE_COMMAND_LEGACY'}}}
 $proofPath=[IO.Path]::GetFullPath($request.owner_proof_path)
 $repo=Split-Path -Parent $PSScriptRoot
 if($proofPath -cne $request.owner_proof_path -or $proofPath -cnotmatch '\\[.]local-tools\\gmgn-owner-proof-[0-9a-f]{32}[.]json$' -or
  (Split-Path -Parent $proofPath) -cne (Join-Path $repo '.local-tools')){throw 'GMGN_STAGE_OWNER_PROOF_PATH'}
 # Namespace/native identities remain held by the stage; public proof FD also
 # stays READ/share-READ after authentication and digest verification.
 $proof=Read-GmgnStageAuthenticatedFile $Stage $proofPath
 if($proof.Sha256 -cne $request.owner_proof_sha256){throw 'GMGN_STAGE_OWNER_PROOF_DIGEST'}
 $owner=$proof.Body
 $ownerClock=Assert-GmgnStageOwnerProofBody $owner $Stage.LegacyBindings $FixtureScope
 $observed=$ownerClock.Observed;$frequency=$ownerClock.Frequency
 $Stage.CommandId=$request.command_id;$Stage.Phase='RETIRE_REQUESTED'
 Write-StageProof 'retire-intent.json' @{schema_version=1;command_id=$Stage.CommandId;generation_sha256=$GenerationSha256;
  supervisor=$Stage.NativeSelf;directory_identity=$Stage.Directory.Identity;legacy_instance_id=$Stage.InstanceId;owner_proof_sha256=$proof.Sha256;phase=$Stage.Phase}
 foreach($proofPin in $Stage.Proofs){$proofPin.Check()}
 $Stage.Sources.Check();$Stage.Directory.Check();$Stage.Retirement.Retire($observed,$frequency);$Stage.Capture.CheckCommitted();$Stage.Sources.Check();$Stage.Directory.Check()
 if($Stage.Retirement.Phase -cne 'RETIRED' -or -not $Stage.Lifetime.ObserveOriginalExits()[0]){throw 'GMGN_STAGE_RETIRE_UNCONFIRMED'}
 $Stage.Phase='RETIRED_WAIT_PRIMARY'
 Write-StageProof 'retire-completed.json' @{schema_version=1;command_id=$Stage.CommandId;generation_sha256=$GenerationSha256;
  supervisor=$Stage.NativeSelf;directory_identity=$Stage.Directory.Identity;legacy_instance_id=$Stage.InstanceId;phase=$Stage.Phase;
  native_broker_exit_confirmed=$true;read_address_published=$false;provider_calls=0}
}
