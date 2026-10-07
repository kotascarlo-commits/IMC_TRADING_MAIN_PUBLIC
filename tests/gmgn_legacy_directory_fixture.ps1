param([ValidateSet('Normal','Existing','WeakAcl','FactoryBypass')][string]$Case='Normal')
$ErrorActionPreference='Stop';$repo=Split-Path -Parent $PSScriptRoot;$tools=Join-Path $repo 'TOOLS'
$names=@('GmgnRepairNamespace','GmgnImmutableSources','GmgnRepairProcessBinding','GmgnReadDirectoryIdentity','GmgnScheduledActionIdentity','GmgnReadLocalAuth','GmgnReadInstallPlan','GmgnSupervisorPipe','GmgnSourcePinBinding','GmgnPinTransferHandshake','GmgnSupervisorSourceAdmission','GmgnHandoffCompletion','GmgnReadProofPin','GmgnLegacyLifetimePins','GmgnLegacyCapture','GmgnLegacyRetirement','GmgnReadOwnerProof')
$parts=@('GmgnReadInstallPlan.Warm.cs','GmgnReadInstallPlan.Root.cs','GmgnReadInstallPlan.Legacy.cs','GmgnReadInstallPlan.Retire.cs','GmgnLegacyRetirement.Auth.cs')
Add-Type -Path @(@($names|ForEach-Object {Join-Path $tools ($_+'.cs')})+@($parts|ForEach-Object {Join-Path $tools $_})) -ReferencedAssemblies @('System.dll','System.Core.dll','System.Web.Extensions.dll',[Management.Automation.PSObject].Assembly.Location)
$type=[GmgnReadInstallPlan+WarmValidationAdmission];$flags=[Reflection.BindingFlags]'Static,NonPublic'
$create=$type.GetMethod('CreateCaptureDirectory',$flags);$open=$type.GetMethod('OpenSecurity',$flags);$check=$type.GetMethod('CheckProtectedAcl',$flags)
$root=Join-Path (Join-Path $repo '.local-tools') ('gmgn-read-recovery-'+[guid]::NewGuid().ToString('N'))
$directory=[GmgnReadProofPin]::CreateNewDirectory($root);$target=Join-Path $root 'capture';$code=''
function Cause($record){$e=$record.Exception;while($e.InnerException){$e=$e.InnerException};return $e.Message}
function InvokeOwn([Reflection.MethodInfo]$Method,[object]$Value){
 $argsForMethod=[object[]]::new(1);$argsForMethod[0]=$Value.PSObject.BaseObject
 return $Method.Invoke($null,$argsForMethod)
}
if($Case -ceq 'FactoryBypass'){
 $entry=$type.GetMethod('CreateFromFactory',$flags)
 try{$null=$entry.Invoke($null,[object[]]@($null,[object]::new()))}catch{$code=Cause $_}
 if($code -cne 'GMGN_WARM_LEGACY_FACTORY'){throw 'OWN_LEGACY_FACTORY_BYPASS'}
}else{
 $rootSecurity=InvokeOwn $open $root;$null=InvokeOwn $check $rootSecurity;$directory.Check()
 if($Case -ceq 'Existing'){
  $null=[IO.Directory]::CreateDirectory($target);$marker=Join-Path $target 'own-marker';[IO.File]::WriteAllText($marker,'OWN_EXISTING_CAPTURE')
  try{$null=InvokeOwn $create $target}catch{$code=Cause $_}
  if($code -cne 'GMGN_WARM_LEGACY_CAPTURE_CREATE_NEW' -or [IO.File]::ReadAllText($marker) -cne 'OWN_EXISTING_CAPTURE'){throw 'OWN_LEGACY_EXISTING_ADOPTED'}
 }else{
  $null=InvokeOwn $create $target;$identity=[GmgnReadDirectoryIdentity]::new($target);$security=InvokeOwn $open $target;$null=InvokeOwn $check $security
  if($Case -ceq 'WeakAcl'){
   $acl=[IO.Directory]::GetAccessControl($target,[Security.AccessControl.AccessControlSections]::Access)
   $acl.SetAccessRuleProtection($false,$true);[IO.Directory]::SetAccessControl($target,$acl)
   try{$null=InvokeOwn $check $security}catch{$code=Cause $_}
   if($code -cne 'GMGN_WARM_LEGACY_ACL'){throw 'OWN_LEGACY_WEAK_ACL_ACCEPTED'}
  }else{$identity.Check();$directory.Check()}
 }
}
[pscustomobject]@{pass=$true;case=$Case;code=$code;provider_calls=0;production_verified=$false}|ConvertTo-Json -Compress
