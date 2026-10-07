param([string]$Case='Normal')
$ErrorActionPreference='Stop';$repo=Split-Path -Parent $PSScriptRoot;$tools=Join-Path $repo 'TOOLS'
$files=@('GmgnRepairNamespace.cs','GmgnReadDirectoryIdentity.cs','GmgnImmutableSources.cs','GmgnReadLocalAuth.cs','GmgnReadOwnerProof.cs','GmgnReadProofPin.cs')|ForEach-Object{Join-Path $tools $_}
Add-Type -Path $files -ReferencedAssemblies @('System.dll','System.Core.dll','System.Web.Extensions.dll',[Management.Automation.PSObject].Assembly.Location)
$paths=[string[]]@((Join-Path $tools 'GmgnReadOwnerProof.cs'),(Join-Path $tools 'GMGN-ReadSupervisorCommands.ps1'))
$sources=[GmgnImmutableSources]::new('%USERPROFILE%','C:\unused-node.exe',$paths,[string[]]@($paths|ForEach-Object{(Get-FileHash $_).Hash.ToLowerInvariant()}))
$auth=[GmgnReadLocalAuth]::CreateNew((Join-Path (Join-Path $repo '.local-tools') ('gmgn-read-auth-'+[guid]::NewGuid().ToString('N'))),[Security.Principal.WindowsIdentity]::GetCurrent().User.Value,[Text.Encoding]::ASCII.GetBytes('f'*64))
$recovery=[GmgnReadProofPin]::CreateNewDirectory((Join-Path (Join-Path $repo '.local-tools') ('gmgn-read-recovery-'+[guid]::NewGuid().ToString('N'))))
$path=Join-Path $recovery.Path 'retire.request.json';$body='{"a":"PUBLIC","z":"DATA_ONLY"}'
switch($Case){'Duplicate'{$body='{"a":"PUBLIC","a":"DATA_ONLY"}'};'Type'{$body='{"a":true,"z":"DATA_ONLY"}'};'Noncanonical'{$body='{"z":"DATA_ONLY","a":"PUBLIC"}'};'Whitespace'{$body='{ "a":"PUBLIC","z":"DATA_ONLY"}'} }
$mac=$auth.Sign($body);if($Case -ceq 'Mac'){$mac='g'*64};if($Case -ceq 'WrongMac'){$mac='0'*64}
$packet=@{body=$body;mac=$mac}|ConvertTo-Json -Compress
if($Case -ceq 'EnvelopeDuplicate'){$packet=$packet.Substring(0,$packet.Length-1)+',"mac":"'+$mac+'"}'}
$bytes=[Text.UTF8Encoding]::new($false).GetBytes($packet);$writer=[IO.FileStream]::new($path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
try{$writer.Write($bytes,0,$bytes.Length);$writer.Flush($true)}finally{$writer.Dispose()}
$hash=(Get-FileHash $path).Hash.ToLowerInvariant();if($Case -ceq 'Hash'){$hash='0'*64}
function WeakAcl {$acl=[IO.Directory]::GetAccessControl($recovery.Path,[Security.AccessControl.AccessControlSections]::Access);$acl.SetAccessRuleProtection($false,$true);[IO.Directory]::SetAccessControl($recovery.Path,$acl)}
if($Case -ceq 'InitialAcl'){WeakAcl}
$flags=[Reflection.BindingFlags]'Static,NonPublic';$open=[GmgnReadOwnerProof].GetMethod('OpenRetirementRequest',$flags);$pin=$null;$code=''
function Cause($record){$e=$record.Exception;while($e.InnerException){$e=$e.InnerException};$e.Message}
try{
 if($Case -ceq 'PublicBypass'){$null=[GmgnReadOwnerProof]::new($sources,$auth,$path,$hash,[string[][]]@(@('1','2','b','e'),@('2','3','b','e')),$null)}
 else{
  $argsForMethod=[object[]]@($sources.PSObject.BaseObject,$auth.PSObject.BaseObject,$recovery.PSObject.BaseObject,$hash)
  $pin=$open.Invoke($null,$argsForMethod);$pin.Check()
  if($pin.State -cne 'PUBLIC_REQUEST_BOUND'){throw 'OWN_REQUEST_ROLE'}
  $instanceFlags=[Reflection.BindingFlags]'Instance,NonPublic'
  $matches=[GmgnReadOwnerProof].GetMethod('Matches',$instanceFlags)
  if($matches.Invoke($pin,[object[]]@($sources.PSObject.BaseObject,$auth.PSObject.BaseObject))){throw 'OWN_REQUEST_OWNER_CONFUSION'}
  $clockSnapshot=[GmgnReadOwnerProof].GetMethod('MatchesValidatedClock',$instanceFlags)
  if($clockSnapshot.Invoke($pin,[object[]]@($sources.PSObject.BaseObject,$auth.PSObject.BaseObject,[long]0,[long]0))){throw 'OWN_REQUEST_CLOCK_ROLE_CONFUSION'}
  $matchesRequest=[GmgnReadOwnerProof].GetMethod('MatchesRequest',$instanceFlags)
  if(-not $matchesRequest.Invoke($pin,[object[]]@($sources.PSObject.BaseObject,$auth.PSObject.BaseObject,$recovery.PSObject.BaseObject))){throw 'OWN_REQUEST_CONTEXT'}
  foreach($index in 0..2){$wrong=[object[]]@($sources.PSObject.BaseObject,$auth.PSObject.BaseObject,$recovery.PSObject.BaseObject);$wrong[$index]=$null;if($matchesRequest.Invoke($pin,$wrong)){throw 'OWN_REQUEST_FOREIGN_REFERENCE'}}
  $read=[GmgnReadOwnerProof].GetMethod('ReadRequestBody',$instanceFlags);if($read.Invoke($pin,$null) -cne $body){throw 'OWN_REQUEST_BODY'}
  switch($Case){'LateAcl'{WeakAcl;$pin.Check()};'RecoveryAbort'{$recovery.GetType().GetField('names',$instanceFlags).GetValue($recovery).Abort();$pin.Check()};'Abort'{$pin.Abort();$pin.Check()}}
 }
}catch{$code=Cause $_}
$expected=switch($Case){'Duplicate'{'GMGN_OWNER_PIN_DUPLICATE'};'EnvelopeDuplicate'{'GMGN_OWNER_PIN_DUPLICATE'};'Type'{'GMGN_OWNER_PIN_REQUEST_TYPE'};'Noncanonical'{'GMGN_OWNER_PIN_REQUEST_CANONICAL'};'Whitespace'{'GMGN_OWNER_PIN_REQUEST_CANONICAL'};'Mac'{'GMGN_OWNER_PIN_ENVELOPE'};'WrongMac'{'GMGN_OWNER_PIN_AUTH'};'Hash'{'GMGN_OWNER_PIN_HASH'};'InitialAcl'{'GMGN_OWNER_PIN_RECOVERY_ACL'};'LateAcl'{'GMGN_OWNER_PIN_RECOVERY_ACL'};'RecoveryAbort'{'GMGN_OWNER_PIN_OPERATION'};'Abort'{'GMGN_OWNER_PIN_FAULT'};'PublicBypass'{'GMGN_OWNER_PIN_INPUT'};default{''}}
if($code -cne $expected){throw ('OWN_REQUEST_CODE_'+$code)};$auth.Check()
if($pin -and $code){if($pin.State -cne 'FAULT'){throw 'OWN_REQUEST_FAULT_LOST'};$again='';try{$pin.Check()}catch{$again=Cause $_};if($again -cne 'GMGN_OWNER_PIN_FAULT'){throw 'OWN_REQUEST_REOPENED'}}
$blocked=$false;$deleteBlocked=$false;$renameBlocked=$false
if($Case -cnotin @('InitialAcl','PublicBypass')){
 try{$attempt=[IO.FileStream]::new($path,[IO.FileMode]::Open,[IO.FileAccess]::Write,[IO.FileShare]::Read);$attempt.Dispose()}catch{$blocked=$true}
 try{[IO.File]::Delete($path)}catch{$deleteBlocked=$true};try{[IO.File]::Move($path,$path+'.renamed')}catch{$renameBlocked=$true}
 if(-not ($blocked -and $deleteBlocked -and $renameBlocked)){throw 'OWN_REQUEST_HANDLE_RELEASED'}
}
[pscustomobject]@{pass=$true;case=$Case;code=$code;retained_write=$blocked;retained_delete=$deleteBlocked;retained_rename=$renameBlocked;provider_calls=0;process_actions=0;production_verified=$false}|ConvertTo-Json -Compress
