param([ValidateSet('Normal','RequestName','ProbeForbidden','ProbeSwitch','ProbeNullSnapshot','SizeLimit','NameMismatch','Collision','WriteBlocked','DirectoryAclMismatch','FileAclMismatch','DirectoryCollision')][string]$Case='Normal')
$ErrorActionPreference='Stop';$repo=Split-Path -Parent $PSScriptRoot;$tools=Join-Path $repo 'TOOLS'
Add-Type -Path @((Join-Path $tools 'GmgnRepairNamespace.cs'),(Join-Path $tools 'GmgnReadDirectoryIdentity.cs'),(Join-Path $tools 'GmgnReadProofPin.cs'))
$path=Join-Path (Join-Path $repo '.local-tools') ('gmgn-read-recovery-'+[guid]::NewGuid().ToString('N'))
$directory=[GmgnReadProofPin]::CreateNewDirectory($path);$bytes=[Text.Encoding]::UTF8.GetBytes('{"fixture":"public proof only"}')
$proof=$null;$code=''
try{
 if($Case -cin @('ProbeSwitch','ProbeNullSnapshot')){
  Add-Type -TypeDefinition @'
using System;using System.Reflection;
public static class OwnProofSwitch {
 public static int OriginalCalls,ReplacementCalls,AdmissionCalls;
 public static Action<string> Original(){return delegate(string phase){OriginalCalls++;};}
 public static Action<string> Replacement(){return delegate(string phase){ReplacementCalls++;};}
 public static Func<bool> Admission(Type type){return delegate(){AdmissionCalls++;type.GetField("fixtureProbe",BindingFlags.Static|BindingFlags.NonPublic).SetValue(null,Replacement());return true;};}
}
'@
  $field=[GmgnReadProofPin].GetField('fixtureProbe',[Reflection.BindingFlags]'Static,NonPublic')
  if($Case -ceq 'ProbeSwitch'){$field.SetValue($null,[OwnProofSwitch]::Original())}
  $publish=[GmgnReadProofPin].GetMethod('PublishRetirementRequest',[Reflection.BindingFlags]'Static,NonPublic')
  try{$proof=$publish.Invoke($null,[object[]]@($directory.PSObject.BaseObject,$bytes,[OwnProofSwitch]::Admission([GmgnReadProofPin])))}finally{$field.SetValue($null,$null)}
  $proof.Check()
  if($Case -ceq 'ProbeSwitch'){
   if([OwnProofSwitch]::OriginalCalls -ne 1 -or [OwnProofSwitch]::ReplacementCalls -ne 0 -or [OwnProofSwitch]::AdmissionCalls -ne 1){throw 'OWN_PROOF_PROBE_REPLACED'}
  }elseif([OwnProofSwitch]::OriginalCalls -ne 0 -or [OwnProofSwitch]::ReplacementCalls -ne 0 -or [OwnProofSwitch]::AdmissionCalls -ne 0){throw 'OWN_PROOF_NULL_PROBE_NOT_FIXED'}
 }
 elseif($Case -ceq 'DirectoryCollision'){$null=[GmgnReadProofPin]::CreateNewDirectory($path)}
 elseif($Case -ceq 'SizeLimit'){$null=[GmgnReadProofPin]::new($directory,'stage-ready.json',[byte[]]::new(32769))}
 elseif($Case -ceq 'NameMismatch'){$null=[GmgnReadProofPin]::new($directory,'unknown.json',$bytes)}
 elseif($Case -ceq 'ProbeForbidden'){
  Add-Type -TypeDefinition 'using System;public static class OwnProofForbiddenProbe{public static Action<string> Make(){return delegate(string phase){throw new InvalidOperationException("SYNTHETIC_PRIVATE_PUBLICATION");};}}'
  $field=[GmgnReadProofPin].GetField('fixtureProbe',[Reflection.BindingFlags]'Static,NonPublic');$field.SetValue($null,[OwnProofForbiddenProbe]::Make())
  try{$null=[GmgnReadProofPin]::new($directory,'retire.request.json',$bytes)}finally{$field.SetValue($null,$null)}
 }
 else{
  if($Case -ceq 'DirectoryAclMismatch'){
   $acl=[IO.Directory]::GetAccessControl($path);$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-1-0'),[Security.AccessControl.FileSystemRights]::Read,[Security.AccessControl.AccessControlType]::Allow));[IO.Directory]::SetAccessControl($path,$acl)
  }
  $name=if($Case -ceq 'RequestName'){'retire.request.json'}else{'stage-ready.json'}
  $proof=[GmgnReadProofPin]::new($directory,$name,$bytes);$binding=$proof.ReadPublicBinding()
  if([IO.Path]::GetFileName($binding[1]) -cne $name){throw 'OWN_PROOF_NAME_CHANGED'}
  # A real independent READ/shareREAD consumer must work after publication.
  $read=[IO.FileStream]::new($binding[1],[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
  try{$actual=[byte[]]::new($bytes.Length);if($read.Read($actual,0,$actual.Length) -ne $actual.Length -or [Convert]::ToBase64String($actual) -cne [Convert]::ToBase64String($bytes)){throw 'OWN_PROOF_READ_CONSUMER_FAILED'}}finally{$read.Dispose()}
  switch($Case){
   'Collision'{$null=[GmgnReadProofPin]::new($directory,'stage-ready.json',$bytes)}
   'WriteBlocked'{$write=[IO.FileStream]::new($binding[1],[IO.FileMode]::Open,[IO.FileAccess]::Write,[IO.FileShare]::ReadWrite);$write.Dispose()}
   'FileAclMismatch'{
    $acl=[IO.File]::GetAccessControl($binding[1]);$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-1-0'),[Security.AccessControl.FileSystemRights]::Read,[Security.AccessControl.AccessControlType]::Allow));[IO.File]::SetAccessControl($binding[1],$acl);$proof.Check()
   }
  }
 }
}catch{$e=$_.Exception;while($e.InnerException){$e=$e.InnerException};$code=if($Case -ceq 'WriteBlocked' -and ($e.HResult -band 65535) -eq 32){'SHARING_VIOLATION'}else{$e.Message}}
$expected=switch($Case){'ProbeForbidden'{'GMGN_PROOF_FIXTURE_FORBIDDEN'};'DirectoryCollision'{'GMGN_PROOF_DIRECTORY_CREATE_NEW'};'SizeLimit'{'GMGN_PROOF_INPUT'};'NameMismatch'{'GMGN_PROOF_INPUT'};'Collision'{'GMGN_PROOF_OPEN'};'WriteBlocked'{'SHARING_VIOLATION'};'DirectoryAclMismatch'{'GMGN_PROOF_ACL'};'FileAclMismatch'{'GMGN_PROOF_ACL'};default{''}}
if($code -cne $expected){throw ('OWN_PROOF_BOUNDARY_UNEXPECTED_'+$code)}
if($Case -ceq 'ProbeForbidden' -and [IO.File]::Exists((Join-Path $path 'retire.request.json'))){throw 'OWN_PROOF_PROBE_CREATED_FILE'}
if($Case -ceq 'FileAclMismatch' -and -not $proof.Faulted){throw 'OWN_PROOF_ACL_FAILURE_NOT_ABSORBING'}
[pscustomobject]@{pass=$true;case=$Case;code=$code;provider_calls=0;production_verified=$false;source_sha256=(Get-FileHash -LiteralPath (Join-Path $tools 'GmgnReadProofPin.cs')).Hash.ToLowerInvariant()}|ConvertTo-Json -Compress
