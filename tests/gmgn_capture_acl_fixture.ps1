param([ValidateSet('Normal','MemberAcl','ManifestAcl','MemberBeforeCommit','ManifestBeforeCommit','ManifestFinalBeforeCommit','SystemOwner')][string]$Case='Normal')
$ErrorActionPreference='Stop';$repo=Split-Path -Parent $PSScriptRoot;$tools=Join-Path $repo 'TOOLS'
Add-Type -Path @((Join-Path $tools 'GmgnRepairNamespace.cs'),(Join-Path $tools 'GmgnLegacyLifetimePins.cs'),(Join-Path $tools 'GmgnLegacyCapture.cs'))
$scope=Join-Path (Join-Path $repo '.local-tools') ('legacy-capture-native-'+[guid]::NewGuid().ToString('N'))
$source=Join-Path $scope 'legacy';$target=Join-Path $scope 'capture'
foreach($p in @($source,$target)){$null=[IO.Directory]::CreateDirectory($p)}
[IO.File]::WriteAllText((Join-Path $source 'broker.ready'),'OWN_CAPTURE_ACL_BYTES',[Text.UTF8Encoding]::new($false))
$capture=[GmgnLegacyCapture]::new($source,$target,[string[]]@('broker.ready'),30)
$capture.BindValidatedReady($capture.ReadySha256);$code=''
if($Case -ceq 'SystemOwner'){
 [GmgnLegacyCapture].GetField('sid',[Reflection.BindingFlags]'Instance,NonPublic').SetValue($capture,'S-1-5-18')
 try{$capture.Copy()}catch{$e=$_.Exception;while($e.InnerException){$e=$e.InnerException};$code=$e.Message}
 if($code -cne 'GMGN_CAPTURE_SYSTEM_OWNER_FORBIDDEN' -or [IO.Directory]::GetFiles($target).Length -or -not $capture.Faulted -or $capture.Committed){throw 'OWN_CAPTURE_SYSTEM_CREATED_FILES'}
 [pscustomobject]@{pass=$true;case=$Case;code=$code;provider_calls=0;production_verified=$false}|ConvertTo-Json -Compress
 return
}
if($Case -clike '*BeforeCommit'){
 Add-Type -TypeDefinition @'
using System;using System.IO;using System.Security.AccessControl;using System.Security.Principal;
public static class OwnCaptureAclBoundary {
 public static Action<string,int> Make(string target,string phase,string name){return delegate(string actual,int index){
  if(actual!=phase||index!=0)return;
  string path=Path.Combine(target,name);var acl=File.GetAccessControl(path,AccessControlSections.Access);
  acl.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier("S-1-1-0"),FileSystemRights.Read,AccessControlType.Allow));File.SetAccessControl(path,acl);
 };}
}
'@
 $phase=if($Case -ceq 'MemberBeforeCommit'){'COPIED_MEMBER'}elseif($Case -ceq 'ManifestFinalBeforeCommit'){'BEFORE_COMMIT'}else{'MANIFEST_FLUSHED'}
 $name=if($Case -ceq 'MemberBeforeCommit'){'broker.ready'}else{'capture-manifest.json'}
 [GmgnLegacyCapture].GetField('fixtureProbe',[Reflection.BindingFlags]'Static,NonPublic').SetValue($null,[OwnCaptureAclBoundary]::Make($target,$phase,$name))
 try{$capture.Copy()}catch{$e=$_.Exception;while($e.InnerException){$e=$e.InnerException};$code=$e.Message}
 if($code -cne 'GMGN_CAPTURE_ACL' -or -not $capture.Faulted -or $capture.Committed){throw 'OWN_CAPTURE_PRECOMMIT_ACL_ACCEPTED'}
 [pscustomobject]@{pass=$true;case=$Case;code=$code;provider_calls=0;production_verified=$false}|ConvertTo-Json -Compress
 return
}
$capture.Copy();$capture.CheckCommitted()
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
foreach($name in @('broker.ready','capture-manifest.json')){
 $acl=[IO.File]::GetAccessControl((Join-Path $target $name),[Security.AccessControl.AccessControlSections]'Access,Owner')
 $rules=@($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
 if(-not $acl.AreAccessRulesProtected -or $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -cne $sid -or $rules.Count -ne 2){throw 'OWN_CAPTURE_ACL_SHAPE'}
 foreach($r in $rules){if($r.IdentityReference.Value -cnotin @($sid,'S-1-5-18') -or $r.IsInherited -or $r.AccessControlType -ne 'Allow' -or $r.FileSystemRights -ne 'FullControl'){throw 'OWN_CAPTURE_ACL_RULE'}}
}
if($Case -cne 'Normal'){
 $name=if($Case -ceq 'MemberAcl'){'broker.ready'}else{'capture-manifest.json'};$path=Join-Path $target $name
 $acl=[IO.File]::GetAccessControl($path,[Security.AccessControl.AccessControlSections]::Access)
 $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-1-0'),[Security.AccessControl.FileSystemRights]::Read,[Security.AccessControl.AccessControlType]::Allow));[IO.File]::SetAccessControl($path,$acl)
 try{$capture.CheckCommitted()}catch{$e=$_.Exception;while($e.InnerException){$e=$e.InnerException};$code=$e.Message}
 if($code -cne 'GMGN_CAPTURE_ACL' -or -not $capture.Faulted -or $capture.Committed){throw 'OWN_CAPTURE_ACL_FAULT_NOT_ABSORBING'}
}
[pscustomobject]@{pass=$true;case=$Case;code=$code;provider_calls=0;production_verified=$false}|ConvertTo-Json -Compress
