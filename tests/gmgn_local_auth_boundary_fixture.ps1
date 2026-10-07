param([ValidateSet('WindowMutation','WindowWriter','AclMismatch','WriterCloseFault','OverlapCloseFault','ProductionProbeRejected')][string]$Case='WindowMutation')
$ErrorActionPreference='Stop';$repo=Split-Path -Parent $PSScriptRoot;$tools=Join-Path $repo 'TOOLS'
$probeSource=@'
using System;using System.IO;using System.Reflection;using System.Security.AccessControl;using System.Security.Principal;
public static class OwnAuthBoundary {
 public static FileStream Writer;
 public static Func<string,int> Close(string wanted){return delegate(string phase){return phase==wanted?5:0;};}
 public static Action<string,GmgnReadLocalAuth> Make(string wanted){return delegate(string phase,GmgnReadLocalAuth auth){
  if(phase!="AFTER_WRITER_CLOSE")return;
  string path=(string)typeof(GmgnReadLocalAuth).GetField("path",BindingFlags.Instance|BindingFlags.NonPublic).GetValue(auth);
  if(wanted=="AclMismatch"){
   var acl=File.GetAccessControl(path);acl.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier("S-1-1-0"),FileSystemRights.Read,AccessControlType.Allow));File.SetAccessControl(path,acl);return;
  }
  Writer=new FileStream(path,FileMode.Open,FileAccess.Write,FileShare.ReadWrite);
  if(wanted=="WindowMutation"){Writer.WriteByte(0);Writer.Flush(true);Writer.Dispose();Writer=null;}
 };}
}
'@
$sources=[string[]]@(@('GmgnRepairNamespace','GmgnReadDirectoryIdentity','GmgnReadLocalAuth')|ForEach-Object {[IO.File]::ReadAllText((Join-Path $tools ($_+'.cs')))})
$compiler=[Microsoft.CSharp.CSharpCodeProvider]::new();$parameters=[CodeDom.Compiler.CompilerParameters]::new();$parameters.GenerateInMemory=$true
foreach($reference in @('System.dll','System.Core.dll')){$null=$parameters.ReferencedAssemblies.Add($reference)}
try{$compiled=$compiler.CompileAssemblyFromSource($parameters,[string[]]($sources+@($probeSource)))}finally{$compiler.Dispose()}
if($compiled.Errors.HasErrors){$compiled.Errors|ForEach-Object {$_.ErrorText};throw 'OWN_AUTH_BOUNDARY_COMPILE'}
$scope=Join-Path (Join-Path $repo '.local-tools') ('local-auth-native-'+[guid]::NewGuid().ToString('N'));$null=[IO.Directory]::CreateDirectory($scope)
$directory=Join-Path $scope ('gmgn-read-auth-'+[guid]::NewGuid().ToString('N'))
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value;$flags=[Reflection.BindingFlags]'Static,NonPublic'
if($Case -cin @('WriterCloseFault','OverlapCloseFault')){
 $phase=if($Case -ceq 'WriterCloseFault'){'WRITER_CLOSE'}else{'OVERLAP_CLOSE'}
 [GmgnReadLocalAuth].GetField('fixtureCloseFailure',$flags).SetValue($null,[OwnAuthBoundary]::Close($phase))
}else{[GmgnReadLocalAuth].GetField('fixtureProbe',$flags).SetValue($null,[OwnAuthBoundary]::Make($Case))}
$code='';$returned=$false
try{
 if($Case -ceq 'ProductionProbeRejected'){
  $directory=Join-Path (Join-Path $repo '.local-tools') ('gmgn-read-auth-'+[guid]::NewGuid().ToString('N'))
  $null=[GmgnReadLocalAuth]::CreateNew($directory,$sid,[Text.Encoding]::ASCII.GetBytes('f'*64))
 }else{$null=[GmgnReadLocalAuth]::CreateNewFixture($directory,$sid,[Text.Encoding]::ASCII.GetBytes('f'*64),$scope)}
 $returned=$true
}catch{$e=$_.Exception;while($e.InnerException){$e=$e.InnerException};$code=$e.Message}
finally{if([OwnAuthBoundary]::Writer){[OwnAuthBoundary]::Writer.Dispose()}}
$expected=switch($Case){'WindowMutation'{'GMGN_AUTH_CIPHERTEXT_HASH'};'WindowWriter'{'GMGN_AUTH_OPEN'};'AclMismatch'{'GMGN_AUTH_ACL'};'ProductionProbeRejected'{'GMGN_AUTH_FIXTURE_FORBIDDEN'};default{'GMGN_AUTH_CLOSE_UNCONFIRMED'}}
if($returned -or $code -cne $expected){throw ('OWN_AUTH_BOUNDARY_UNEXPECTED_'+$code)}
$retained=[GmgnReadLocalAuth].GetField('retained',$flags).GetValue($null);$faulted=$retained[$retained.Count-1]
if(-not $faulted.Faulted){throw 'OWN_AUTH_BOUNDARY_NOT_FAULTED'}
$after='';try{$null=$faulted.Sign('OWN_NO_AUTH_AFTER_FAULT')}catch{$e=$_.Exception;while($e.InnerException){$e=$e.InnerException};$after=$e.Message}
if($after -cne 'GMGN_AUTH_FAULT'){throw 'OWN_AUTH_BOUNDARY_AUTHORITY_PUBLISHED'}
$unknown=0;$closed=0;$instanceFlags=[Reflection.BindingFlags]'Instance,NonPublic'
foreach($handle in $faulted.GetType().GetField('handles',$instanceFlags).GetValue($faulted)){
 if($handle.GetType().GetField('CloseUncertain',$instanceFlags).GetValue($handle)){$unknown++}
 if($handle.GetType().GetField('CloseAttempted',$instanceFlags).GetValue($handle)){$closed++}
}
if($Case -cin @('WriterCloseFault','OverlapCloseFault') -and $unknown -ne 1){throw 'OWN_AUTH_BOUNDARY_CLOSE_FAULT_NOT_HELD'}
[pscustomobject]@{pass=$true;case=$Case;code=$code;authority_returned=$returned;uncertain_closes=$unknown;close_attempts=$closed;injected_native_failure=($Case -cin @('WriterCloseFault','OverlapCloseFault'));provider_calls=0;production_verified=$false}|ConvertTo-Json -Compress
