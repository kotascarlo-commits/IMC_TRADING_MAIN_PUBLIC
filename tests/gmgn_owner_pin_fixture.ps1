param([string]$Case='Normal')
$ErrorActionPreference='Stop';$repo=Split-Path -Parent $PSScriptRoot;$tools=Join-Path $repo 'TOOLS'
$files=@('GmgnRepairNamespace.cs','GmgnReadDirectoryIdentity.cs','GmgnImmutableSources.cs','GmgnReadLocalAuth.cs','GmgnReadOwnerProof.cs')|ForEach-Object{Join-Path $tools $_}
Add-Type -Path $files -ReferencedAssemblies @('System.dll','System.Core.dll','System.Web.Extensions.dll','System.Management.Automation.dll')
$sourcePaths=[string[]]@((Join-Path $tools 'GmgnReadOwnerProof.cs'),(Join-Path $tools 'GMGN-ReadSupervisorCommands.ps1'))
$hashes=[string[]]@($sourcePaths|ForEach-Object{(Get-FileHash -LiteralPath $_).Hash.ToLowerInvariant()})
$sources=[GmgnImmutableSources]::new('%USERPROFILE%','C:\unused-node.exe',$sourcePaths,$hashes)
$authDirectory=Join-Path (Join-Path $repo '.local-tools') ('gmgn-read-auth-'+[guid]::NewGuid().ToString('N'))
# Artificial local IPC bytes; no provider credential is read.
$key=[Text.Encoding]::ASCII.GetBytes('f'*64)
$auth=[GmgnReadLocalAuth]::CreateNew($authDirectory,[Security.Principal.WindowsIdentity]::GetCurrent().User.Value,$key)
[Array]::Clear($key,0,$key.Length)
$bindings=[string[][]]@(@('14552','3904','2026-10-02T15:14:56.6594833Z','OWN_EXE'),@('3904','4176','2026-10-02T15:14:56.3325126Z','OWN_EXE'))
$now=[Diagnostics.Stopwatch]::GetTimestamp();$frequency=[Diagnostics.Stopwatch]::Frequency
$body=[ordered]@{schema_version=1;kind='ORIGINAL_OWNER_RETIREMENT_EVIDENCE';owner_pid=3904;owner_birth=$bindings[1][2];broker_pid=14552;broker_birth=$bindings[0][2];broker_directory='%USERPROFILE%\AppData\Local\Temp\imc-gmgn-broker-3904';loaded_source_sha256='2243b01e0c24c60aa3bd6906486424a66007d33e063100fe949968ec01500ed6';loaded_cleanup_sha256='b16f53d6cc9d6e679c11d210196a4a84b945e419753c383d6ec67be5a5d6be50';original_script=(Join-Path $tools 'Start-IMC-Work.ps1');original_line=215;original_broker_handle_verified=$true;variables_changed=$false;provider_calls=0;observed_qpc=$now.ToString();qpc_frequency=$frequency.ToString()}
switch($Case){
 'Expired'{$body.observed_qpc=($now-16*$frequency).ToString()}
 'Future'{$body.observed_qpc=($now+30*$frequency).ToString()}
 'ExpiresAfterBinding'{$body.observed_qpc=($now-13*$frequency).ToString()}
 'ExpiresDuringPublicCheck'{$body.observed_qpc=($now-13*$frequency).ToString()}
 'ClockSnapshotExpired'{$body.observed_qpc=($now-13*$frequency).ToString()}
 'Frequency'{$body.qpc_frequency=($frequency+1).ToString()}
 'WrongType'{$body.original_broker_handle_verified='true'}
 'LoadedHash'{$body.loaded_source_sha256='a'*64}
 'Frame'{$body.original_line=214}
}
$text=$body|ConvertTo-Json -Compress
switch($Case){
 'DuplicateBody'{$text=$text.Substring(0,$text.Length-1)+',"schema_version":1}'}
 'EscapedDuplicateBody'{$text=$text.Substring(0,$text.Length-1)+',"schema_\u0076ersion":1}'}
 'BadNumber'{$text=$text.Replace('"schema_version":1,','"schema_version":01,')}
 'NestedBody'{$text=$text.Replace('"schema_version":1,','"schema_version":{"bait":1},')}
 'BadEscape'{$text=$text.Replace('ORIGINAL_OWNER_RETIREMENT_EVIDENCE','BAD\q')}
}
$mac=$auth.Sign($text);if($Case -ceq 'WrongMac'){$mac='0'*64}
switch($Case){'EmptyMac'{$mac=''};'ShortMac'{$mac='f'*63};'NonHexMac'{$mac='g'*64};'UpperMac'{$mac='F'*64}}
$packet=[ordered]@{body=$text;mac=$mac}|ConvertTo-Json -Compress
switch($Case){
 'DuplicateEnvelope'{$packet=$packet.Substring(0,$packet.Length-1)+',"mac":"'+$mac+'"}'}
 'EscapedDuplicateEnvelope'{$packet=$packet.Substring(0,$packet.Length-1)+',"m\u0061c":"'+$mac+'"}'}
 'EnvelopeType'{$packet='{"body":true,"mac":"'+$mac+'"}'}
 'NullBody'{$packet='{"body":null,"mac":"'+$mac+'"}'}
 'Trailing'{$packet+='false'}
 'Zero'{$packet=''}
 'Oversize'{$packet='x'*32769}
}
$path=Join-Path (Join-Path $repo '.local-tools') ('gmgn-owner-proof-'+[guid]::NewGuid().ToString('N')+'.json')
$bytes=[Text.UTF8Encoding]::new($false,$true).GetBytes($packet)
if($Case -ceq 'InvalidUtf8'){$bytes=[byte[]]@(0xff,0xfe,0xff)}
$writer=[IO.FileStream]::new($path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
try{$writer.Write($bytes,0,$bytes.Length);$writer.Flush($true)}finally{$writer.Dispose()}
$hash=(Get-FileHash -LiteralPath $path).Hash.ToLowerInvariant()
if($Case -ceq 'WrongHash'){$hash='0'*64}
if($Case -ceq 'HardLink'){$null=New-Item -ItemType HardLink -Path ($path+'.link') -Target $path}
if($Case -ceq 'Reparse'){
 $target=$path+'.target';$null=[IO.Directory]::CreateDirectory($target)
 $path=Join-Path (Join-Path $repo '.local-tools') ('gmgn-owner-proof-'+[guid]::NewGuid().ToString('N')+'.json')
 $null=New-Item -ItemType Junction -Path $path -Target $target
}
$pin=$null;$code='';$blocked=$false;$bound=$false;$expiryStep=$false
function Cause($record){$e=$record.Exception;while($e.InnerException){$e=$e.InnerException};return $e.Message}
try{
 $pin=[GmgnReadOwnerProof]::new($sources,$auth,$path,$hash,$bindings,$null)
 if($pin.State -cne 'PUBLIC_PROOF_BOUND'){throw 'OWN_PIN_NOT_BOUND'};$bound=$true
 $binding=$pin.ReadBinding();if($binding.Length -ne 5 -or $binding[0] -cne $path -or $binding[2] -cne $hash -or $binding[3] -cne $body.observed_qpc){throw 'OWN_PIN_BINDING'}
 $binding[0]='MUTATED';$bindings[0][0]='MUTATED';$pin.Check()
 if($pin.ReadBinding()[0] -cne $path){throw 'OWN_PIN_COPY'}
 try{$attempt=[IO.FileStream]::new($path,[IO.FileMode]::Open,[IO.FileAccess]::Write,[IO.FileShare]::Read);$attempt.Dispose()}catch{$blocked=$true}
 if(-not $blocked){throw 'OWN_PIN_WRITE_ALLOWED'}
 switch($Case){
  'ClockSnapshot'{
   $match=$pin.GetType().GetMethod('MatchesValidatedClock',[Reflection.BindingFlags]'Instance,NonPublic')
   $clockArgs=[object[]]@($sources,$auth,[long]$body.observed_qpc,[long]$body.qpc_frequency)
   if(-not $match.Invoke($pin,$clockArgs)){throw 'OWN_CLOCK_SNAPSHOT_MISMATCH'}
   foreach($wrong in @([object[]]@($sources,$auth,([long]$body.observed_qpc+1),[long]$body.qpc_frequency),[object[]]@($sources,$auth,[long]$body.observed_qpc,([long]$body.qpc_frequency+1)),[object[]]@($null,$auth,[long]$body.observed_qpc,[long]$body.qpc_frequency),[object[]]@($sources,$null,[long]$body.observed_qpc,[long]$body.qpc_frequency))){if($match.Invoke($pin,$wrong)){throw 'OWN_CLOCK_FOREIGN_SNAPSHOT_MATCHED'}}
   $pin.Abort();if($match.Invoke($pin,$clockArgs)){throw 'OWN_CLOCK_ABORT_MATCHED'};$pin.Check()
  }
  'ClockSnapshotSourceFault'{
   $match=$pin.GetType().GetMethod('MatchesValidatedClock',[Reflection.BindingFlags]'Instance,NonPublic');$sources.Abort()
   if(-not $match.Invoke($pin,[object[]]@($sources,$auth,[long]$body.observed_qpc,[long]$body.qpc_frequency))){throw 'OWN_CLOCK_SNAPSHOT_CHANGED'}
   $pin.Check()
  }
  'ClockSnapshotAuthFault'{
   $match=$pin.GetType().GetMethod('MatchesValidatedClock',[Reflection.BindingFlags]'Instance,NonPublic');$auth.Abort()
   if(-not $match.Invoke($pin,[object[]]@($sources,$auth,[long]$body.observed_qpc,[long]$body.qpc_frequency))){throw 'OWN_CLOCK_SNAPSHOT_CHANGED'}
   $pin.Check()
  }
  'ClockSnapshotExpired'{
   $expiryStep=$true;Start-Sleep -Seconds 3
   $match=$pin.GetType().GetMethod('MatchesValidatedClock',[Reflection.BindingFlags]'Instance,NonPublic')
   if(-not $match.Invoke($pin,[object[]]@($sources,$auth,[long]$body.observed_qpc,[long]$body.qpc_frequency))){throw 'OWN_CLOCK_SNAPSHOT_RENEWED'}
   $pin.Check()
  }
  'Abort'{$pin.Abort();$pin.Check()};'SourceAbort'{$sources.Abort();$pin.Check()};'AuthAbort'{$auth.Abort();$pin.Check()}
  'ExpiresAfterBinding'{$expiryStep=$true;Start-Sleep -Seconds 3;$pin.Check()}
  'ExpiresDuringValidation'{
   # OWN object only: force a slow public validator after initial healthy
   # binding. No production hook or source-file replacement is introduced.
   $field=$pin.GetType().GetField('helper',[Reflection.BindingFlags]'Instance,NonPublic')
   $field.SetValue($pin,"function Assert-GmgnStageOwnerProofBody { param(`$owner,`$legacy,`$fixture) Start-Sleep -Seconds 16; [pscustomobject]@{Observed=[long]`$owner.observed_qpc;Frequency=[long]`$owner.qpc_frequency} }")
   $expiryStep=$true
   # Invoke the same private semantic validator explicitly in this OWN
   # fixture; normal checks now validate held bytes/MAC without reparsing.
   # Finally also require public Check to absorb the expired original clock.
   $validationCode='';try{$null=$pin.GetType().GetMethod('Validate',[Reflection.BindingFlags]'Instance,NonPublic').Invoke($pin,$null)}catch{$validationCode=Cause $_}
   if($validationCode -cne 'GMGN_STAGE_OWNER_PROOF_STALE'){throw 'OWN_PIN_VALIDATOR_FINAL_CLOCK_MISSING'}
   $checkCode='';try{$pin.Check()}catch{$checkCode=Cause $_}
   if($checkCode -cne 'GMGN_STAGE_OWNER_PROOF_STALE'){throw 'OWN_PIN_PUBLIC_FINAL_CLOCK_MISSING'}
   throw [InvalidOperationException]::new($checkCode)
  }
  'ExpiresDuringPublicCheck'{
   Add-Type -TypeDefinition @'
using System;using System.Threading;
public sealed class OwnProofAuthDelay {
 public readonly ManualResetEvent Held=new ManualResetEvent(false);readonly object sync;
 public readonly Thread Worker;
 public OwnProofAuthDelay(object gate){sync=gate;Worker=new Thread(delegate(){lock(sync){Held.Set();Thread.Sleep(3000);}});Worker.IsBackground=true;}
 public void Start(){Worker.Start();if(!Held.WaitOne(1000))throw new InvalidOperationException("OWN_PIN_AUTH_DELAY_NOT_HELD");}
 public void Finish(){if(!Worker.Join(5000))throw new InvalidOperationException("OWN_PIN_AUTH_DELAY_NOT_RELEASED");}
}
'@
   $gate=$auth.GetType().GetField('sync',[Reflection.BindingFlags]'Instance,NonPublic').GetValue($auth)
   $delay=[OwnProofAuthDelay]::new($gate);$delay.Start()
   try{
    $age=([Diagnostics.Stopwatch]::GetTimestamp()-[long]$body.observed_qpc)/[double]$frequency
    if($age -ge 15){throw 'OWN_PIN_CHECK_ENTRY_ALREADY_STALE'}
    $expiryStep=$true;$pin.Check()
   }finally{$delay.Finish()}
  }
  'AuthKeyChanged'{
   $field=$auth.GetType().GetField('key',[Reflection.BindingFlags]'Instance,NonPublic')
   $field.SetValue($auth,[Text.Encoding]::ASCII.GetBytes('e'*64));$pin.Check()
  }
  'NamespaceAbort'{
   $field=$pin.GetType().GetField('names',[Reflection.BindingFlags]'Instance,NonPublic');$field.GetValue($pin).Abort();$pin.Check()
  }
 }
}catch{$code=Cause $_}
$expected=switch($Case){
 'Expired'{'GMGN_STAGE_OWNER_PROOF_STALE'};'Future'{'GMGN_STAGE_OWNER_PROOF_STALE'};'Frequency'{'GMGN_STAGE_OWNER_PROOF_STALE'}
 'ExpiresAfterBinding'{'GMGN_STAGE_OWNER_PROOF_STALE'}
 'ExpiresDuringValidation'{'GMGN_STAGE_OWNER_PROOF_STALE'};'ExpiresDuringPublicCheck'{'GMGN_STAGE_OWNER_PROOF_STALE'};'NamespaceAbort'{'GMGN_OWNER_PIN_OPERATION'};'AuthKeyChanged'{'GMGN_OWNER_PIN_AUTH'}
 'WrongType'{'GMGN_STAGE_OWNER_PROOF_BINDING'};'LoadedHash'{'GMGN_STAGE_OWNER_LOADED_SOURCE'};'Frame'{'GMGN_STAGE_OWNER_LOADED_SOURCE'}
 'DuplicateBody'{'GMGN_OWNER_PIN_DUPLICATE'};'EscapedDuplicateBody'{'GMGN_OWNER_PIN_DUPLICATE'};'DuplicateEnvelope'{'GMGN_OWNER_PIN_DUPLICATE'};'EscapedDuplicateEnvelope'{'GMGN_OWNER_PIN_DUPLICATE'}
 'BadNumber'{'GMGN_OWNER_PIN_JSON'};'NestedBody'{'GMGN_OWNER_PIN_JSON'};'BadEscape'{'GMGN_OWNER_PIN_JSON'};'Trailing'{'GMGN_OWNER_PIN_JSON'}
 'EnvelopeType'{'GMGN_OWNER_PIN_ENVELOPE'};'NullBody'{'GMGN_OWNER_PIN_ENVELOPE'};'WrongMac'{'GMGN_OWNER_PIN_AUTH'};'WrongHash'{'GMGN_OWNER_PIN_HASH'}
 'EmptyMac'{'GMGN_OWNER_PIN_ENVELOPE'};'ShortMac'{'GMGN_OWNER_PIN_ENVELOPE'};'NonHexMac'{'GMGN_OWNER_PIN_ENVELOPE'};'UpperMac'{'GMGN_OWNER_PIN_ENVELOPE'}
 'Zero'{'GMGN_OWNER_PIN_SHAPE'};'Oversize'{'GMGN_OWNER_PIN_SHAPE'};'HardLink'{'GMGN_OWNER_PIN_SHAPE'};'InvalidUtf8'{'GMGN_OWNER_PIN_OPERATION'}
 'Reparse'{'GMGN_OWNER_PIN_SHAPE'}
 'Abort'{'GMGN_OWNER_PIN_FAULT'};'ClockSnapshot'{'GMGN_OWNER_PIN_FAULT'};'SourceAbort'{'GMGN_SOURCE_LOCKED'};'ClockSnapshotSourceFault'{'GMGN_SOURCE_LOCKED'};'AuthAbort'{'GMGN_AUTH_FAULT'};'ClockSnapshotAuthFault'{'GMGN_AUTH_FAULT'};'ClockSnapshotExpired'{'GMGN_STAGE_OWNER_PROOF_STALE'};default{''}
}
if($code -cne $expected){throw ('OWN_PIN_WRONG_CODE_'+$code)}
if($Case -cin @('ExpiresAfterBinding','ExpiresDuringValidation','ExpiresDuringPublicCheck','ClockSnapshotExpired') -and (-not $bound -or -not $expiryStep)){throw 'OWN_PIN_EXPIRY_NOT_REACHED'}
if($pin -and $code){if($pin.State -cne 'FAULT'){throw 'OWN_PIN_FAULT_LOST'};$again='';try{$pin.Check()}catch{$again=Cause $_};if($again -cne 'GMGN_OWNER_PIN_FAULT'){throw 'OWN_PIN_FAULT_REOPENED'}}
if($Case -cnotin @('AuthAbort','ClockSnapshotAuthFault')){$auth.Check()}
# Even a failed constructor retains its opened native object.
$retainedWrite=$false;$retainedDelete=$false;$retainedRename=$false
if($Case -ceq 'Reparse'){
 Add-Type -TypeDefinition 'using System;using System.Runtime.InteropServices;public static class OwnReparseWrite{[DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]public static extern IntPtr CreateFileW(string p,uint a,uint s,IntPtr z,uint c,uint f,IntPtr t);[DllImport("kernel32.dll")]public static extern bool CloseHandle(IntPtr h);}'
 $attempt=[OwnReparseWrite]::CreateFileW($path,[uint32]1073741824,1,[IntPtr]::Zero,3,0x02200080,[IntPtr]::Zero)
 $nativeError=[Runtime.InteropServices.Marshal]::GetLastWin32Error()
 $retainedWrite=$attempt -eq [IntPtr](-1) -and $nativeError -eq 32
 if($attempt -ne [IntPtr](-1) -and $attempt -ne [IntPtr]::Zero){$null=[OwnReparseWrite]::CloseHandle($attempt)}
 try{[IO.Directory]::Delete($path)}catch{$retainedDelete=$true}
 try{[IO.Directory]::Move($path,$path+'.renamed')}catch{$retainedRename=$true}
}else{
 try{$attempt=[IO.FileStream]::new($path,[IO.FileMode]::Open,[IO.FileAccess]::Write,[IO.FileShare]::Read);$attempt.Dispose()}catch{$retainedWrite=$true}
 try{[IO.File]::Delete($path)}catch{$retainedDelete=$true}
 try{[IO.File]::Move($path,$path+'.renamed')}catch{$retainedRename=$true}
}
if(-not ($retainedWrite -and $retainedDelete -and $retainedRename)){throw 'OWN_PIN_RETENTION_LOST'}
[pscustomobject]@{pass=$true;case=$Case;code=$code;bound=$bound;expiry_step=$expiryStep;write_blocked=$blocked;retained_write=$retainedWrite;retained_delete=$retainedDelete;retained_rename=$retainedRename;provider_calls=0;process_actions=0;production_verified=$false}|ConvertTo-Json -Compress
