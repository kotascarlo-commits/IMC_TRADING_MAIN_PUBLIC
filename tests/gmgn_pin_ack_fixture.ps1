param([ValidateSet('Normal','Replay','GenerationMismatch','DigestMismatch','DirectoryMismatch','WrongSequence','ExtraField','MissingField','DuplicateField','WrongDirection','SourceFault','AbortBeforeCommit','AbortAfterPinAck','ManipulatedCommit','ReplayNewSession','Admission','AdmissionReplay','AdmissionSourceFault','ManifestMismatch','RootAdmission','AdmissionRootExit','CompletionNormal','CompletionWrongMac','CompletionReplay','CompletionSourceFault','CompletionBeforeAdmission','CompletionDuplicateRoot','CompletionDuplicateChild','TransportDuplicateRoot','TransportDuplicateChild',
 'CompletionWarmNormal','CompletionWarmReplay','CompletionWarmSource','CompletionWarmAuth','CompletionWarmAdmission','CompletionWarmBeforeReady','CompletionWarmFault','CompletionWarmRoot',
 'CompletionFileDigest','CompletionDirectoryDigest','CompletionAuthAndDigest',
 'CompletionWarmClaimsBefore','CompletionWarmClaimsAfter','CompletionWarmClaimsAndNative',
 'CompletionWarmClaimsAfterNative','CompletionWarmSelfNull','CompletionWarmSelfShort','CompletionWarmScalarCopy')][string]$Case='Normal',[string]$RootExitProofPath,[int]$ClaimIndex=0)
$ErrorActionPreference='Stop';$repo=Split-Path -Parent $PSScriptRoot;$tools=Join-Path $repo 'TOOLS'
. (Join-Path $tools 'GMGN-SupervisorTask.ps1')
$scope=Join-Path (Join-Path $repo '.local-tools') ('pin-ack-native-'+[guid]::NewGuid().ToString('N'));$null=[IO.Directory]::CreateDirectory($scope)
$names=@('GmgnRepairNamespace','GmgnImmutableSources','GmgnReadDirectoryIdentity','GmgnRepairProcessBinding','GmgnScheduledActionIdentity','GmgnSupervisorPipe','GmgnSourcePinBinding','GmgnPinTransferHandshake','GmgnSupervisorSourceAdmission','GmgnReadLocalAuth','GmgnHandoffCompletion')
$pins=[Collections.Generic.List[object]]::new();$compile=[Collections.Generic.List[string]]::new();$paths=[Collections.Generic.List[string]]::new()
foreach($namePart in $names){
 $path=Join-Path $scope ($namePart+'.cs');[IO.File]::Copy((Join-Path $tools ($namePart+'.cs')),$path);$paths.Add($path)
 $pin=[IO.FileStream]::new($path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read);$pins.Add($pin)
 $r=[IO.StreamReader]::new($pin,[Text.UTF8Encoding]::new($false,$true),$true,4096,$true);try{$compile.Add($r.ReadToEnd())}finally{$r.Dispose()}
}
$compiler=[Microsoft.CSharp.CSharpCodeProvider]::new();$parameters=[CodeDom.Compiler.CompilerParameters]::new();$parameters.GenerateInMemory=$true
foreach($refName in @('System.dll','System.Core.dll')){$null=$parameters.ReferencedAssemblies.Add($refName)}
try{$compiled=$compiler.CompileAssemblyFromSource($parameters,$compile.ToArray())}finally{$compiler.Dispose()}
if($compiled.Errors.HasErrors){$compiled.Errors|ForEach-Object {$_.ErrorText};throw 'OWN_PIN_ACK_COMPILE'}
$ownerSid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$authDirectory=Join-Path (Join-Path $repo '.local-tools') ('gmgn-read-auth-'+[guid]::NewGuid().ToString('N'))
$localAuth=[GmgnReadLocalAuth]::CreateNew($authDirectory,$ownerSid,[Text.Encoding]::ASCII.GetBytes('f'*64))
$authBindingEncoded=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($localAuth.ReadPublicBinding()|ConvertTo-Json -Compress)))
$worker=Join-Path $scope 'worker.ps1'
$workerText=@'
param([string]$Scope,[string]$PipeName,[string]$Sid,[string]$RootBinding,[string]$DirectoryId,[string]$Inventory,[string]$Session,[string]$Challenge,[string]$Case,[string]$AuthBinding,[int]$ClaimIndex)
$ErrorActionPreference='Stop';$client=$null;$streams=[Collections.Generic.List[object]]::new();$source=@();$code=''
try{
 $manifest=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($Inventory))|ConvertFrom-Json
 foreach($file in $manifest){
  $pin=[IO.FileStream]::new($file.path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read);$streams.Add($pin)
  $sha=[Security.Cryptography.SHA256]::Create();try{$hash=[BitConverter]::ToString($sha.ComputeHash($pin)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
  if($hash -cne $file.sha256){throw 'OWN_PIN_ACK_CHILD_SOURCE'}
  if([IO.Path]::GetExtension($file.path) -ceq '.cs'){
   $pin.Position=0;$r=[IO.StreamReader]::new($pin,[Text.UTF8Encoding]::new($false,$true),$true,4096,$true);try{$source+=$r.ReadToEnd()}finally{$r.Dispose()}
  }
 }
 $compiler=[Microsoft.CSharp.CSharpCodeProvider]::new();$p=[CodeDom.Compiler.CompilerParameters]::new();$p.GenerateInMemory=$true
 $source+='using System;using System.Diagnostics;using System.Threading;public static class OwnCompletionRootExit{public static bool Observed;public static Action<GmgnScheduledActionIdentity> Make(){return delegate(GmgnScheduledActionIdentity root){var clock=Stopwatch.StartNew();while(root.ReadOriginalExitCode()<0&&clock.Elapsed.TotalSeconds<10)Thread.Sleep(1);if(root.ReadOriginalExitCode()!=0)throw new InvalidOperationException("OWN_COMPLETION_ROOT_EXIT_NOT_OBSERVED");Observed=true;};}}'
 foreach($refName in @('System.dll','System.Core.dll')){$null=$p.ReferencedAssemblies.Add($refName)}
 try{$compiled=$compiler.CompileAssemblyFromSource($p,[string[]]$source)}finally{$compiler.Dispose()}
 if($compiled.Errors.HasErrors){throw 'OWN_PIN_ACK_CHILD_COMPILE'}
 $files=@($manifest);if($Case -ceq 'DigestMismatch'){$files=@($manifest|Where-Object {[IO.Path]::GetExtension($_.path) -ceq '.cs'})}
 $first=[GmgnImmutableSources]::new('%USERPROFILE%','C:\Program Files\nodejs\node.exe',[string[]]@($files|ForEach-Object {$_.path}),[string[]]@($files|ForEach-Object {$_.sha256}))
 $binding=[GmgnSourcePinBinding]::new($first)
 if($Case -ceq 'DirectoryMismatch'){
  # Own negative message digest, not a production state mutation.
  $binding.GetType().GetField('directoryDigest',[Reflection.BindingFlags]'Instance,NonPublic').SetValue($binding,('0'*64))
 }
 $root=[string[]]([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($RootBinding))|ConvertFrom-Json)
 $directory=[GmgnReadDirectoryIdentity]::new($Scope)
 $originalRoot=[GmgnScheduledActionIdentity]::BindExpectedPeer($root,$Sid,$directory,$DirectoryId)
 $client=[GmgnSupervisorPipe]::new($PipeName,$Sid,$false,15000)
 $originalRoot.CheckPeerPid($client.ReadKernelPeerPid())
 $self=[GmgnRepairProcessBinding]::Read([Diagnostics.Process]::GetCurrentProcess())
 $manifestPath=Join-Path $Scope 'package.json'
 $manifestHash=($manifest|Where-Object {$_.path -ceq $manifestPath}).sha256
 $generation=if($Case -ceq 'GenerationMismatch'){'b'*64}else{$manifestHash}
 if($Case -ceq 'SourceFault'){
  $clock=[Diagnostics.Stopwatch]::StartNew();while(-not [IO.File]::Exists((Join-Path $Scope 'root-handshake-prepared')) -and $clock.Elapsed.TotalSeconds -lt 10){Start-Sleep -Milliseconds 10}
  if(-not [IO.File]::Exists((Join-Path $Scope 'root-handshake-prepared'))){throw 'OWN_PIN_ACK_ROOT_NOT_PREPARED'}
  $first.Abort()
 }
 $handshake=[GmgnPinTransferHandshake]::new($client,$binding,$originalRoot,'CHILD',$generation,$Session,$Challenge,$root,$self)
 if($Case -cin @('WrongSequence','ExtraField','MissingField','DuplicateField','WrongDirection')){
  $null=$client.ReadMessage()
  $sequence=if($Case -ceq 'WrongSequence'){3}else{2}
  $message=$handshake.GetType().GetMethod('Message',[Reflection.BindingFlags]'Instance,NonPublic').Invoke($handshake,[object[]]@($sequence))
  if($Case -ceq 'ExtraField'){
   $text=[Text.Encoding]::UTF8.GetString($message)
   $message=[Text.Encoding]::UTF8.GetBytes($text.Substring(0,$text.Length-1)+',"extra":true}')
  }
  if($Case -cin @('MissingField','DuplicateField','WrongDirection')){
   $text=[Text.Encoding]::UTF8.GetString($message)
   $field=if($Case -ceq 'WrongDirection'){'"direction":"CHILD_TO_ROOT"'}else{'"sequence":2,'}
   $offset=$text.IndexOf($field,[StringComparison]::Ordinal);if($offset -lt 0){throw 'OWN_PIN_ACK_NEGATIVE_FIELD_MISSING'}
   if($Case -ceq 'DuplicateField'){$text=$text.Insert($offset,$field)}
   else{$text=$text.Remove($offset,$field.Length);if($Case -ceq 'WrongDirection'){$text=$text.Insert($offset,'"direction":"ROOT_TO_CHILD"')}}
   $message=[Text.Encoding]::UTF8.GetBytes($text)
  }
  $client.WriteMessage($message)
  $clock=[Diagnostics.Stopwatch]::StartNew();while(-not [IO.File]::Exists((Join-Path $Scope 'child-exit')) -and $clock.Elapsed.TotalSeconds -lt 10){Start-Sleep -Milliseconds 10}
 }elseif($Case -ceq 'AbortBeforeCommit'){
  # Native transport receives step one, then the own child exits with no ACK.
  $null=$client.ReadMessage();$handshake.Abort()
 }else{
  $handshake.Execute();$complete=$handshake.ReadCompletedBinding()
  [IO.File]::WriteAllText((Join-Path $Scope 'child-complete.json'),(@{state=$handshake.State;binding=$complete}|ConvertTo-Json -Compress))
  if($Case -clike 'TransportDuplicate*'){
   $clock=[Diagnostics.Stopwatch]::StartNew();while(-not [IO.File]::Exists((Join-Path $Scope 'root-duplicate-test-ready')) -and $clock.Elapsed.TotalSeconds -lt 5){Start-Sleep -Milliseconds 10}
   if(-not [IO.File]::Exists((Join-Path $Scope 'root-duplicate-test-ready'))){throw 'OWN_TRANSPORT_DUPLICATE_ROOT_NOT_READY'}
   if($Case -ceq 'TransportDuplicateChild'){$null=[GmgnPinTransferHandshake]::new($client,$binding,$originalRoot,'CHILD',$generation,$Session,$Challenge,$root,$self)}
  }
  if($Case -clike 'Completion*' -or $Case -ceq 'AdmissionRootExit'){
   $clock=[Diagnostics.Stopwatch]::StartNew();while(-not [IO.File]::Exists((Join-Path $Scope 'root-fourth-ready')) -and $clock.Elapsed.TotalSeconds -lt 5){Start-Sleep -Milliseconds 10}
   if(-not [IO.File]::Exists((Join-Path $Scope 'root-fourth-ready'))){throw 'OWN_COMPLETION_ROOT_NOT_READY'}
   $b=[string[]]([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($AuthBinding))|ConvertFrom-Json)
   $auth=[GmgnReadLocalAuth]::OpenExisting($b[1],$b[2],$b[3],$b[4],$b[5])
   if($Case -ceq 'AdmissionRootExit'){
    [GmgnHandoffCompletion].GetField('fixtureAfterWrite',[Reflection.BindingFlags]'Static,NonPublic').SetValue($null,[OwnCompletionRootExit]::Make())
    $completion=[GmgnHandoffCompletion]::new($client,$handshake,$binding,$originalRoot,$auth,'CHILD',$Scope)
   }else{$completion=[GmgnHandoffCompletion]::new($client,$handshake,$binding,$originalRoot,$auth,'CHILD')}
   if($Case -ceq 'CompletionDuplicateChild'){$null=[GmgnHandoffCompletion]::new($client,$handshake,$binding,$originalRoot,$auth,'CHILD')}
   if($Case -ceq 'CompletionBeforeAdmission'){$completion.SendChildReady($null)}
   $admission=$handshake.CreateChildAdmission($manifestPath)
   if($Case -cin @('CompletionFileDigest','CompletionDirectoryDigest','CompletionAuthAndDigest')){
    $descriptor=$completion.GetType().GetField('binding',[Reflection.BindingFlags]'Instance,NonPublic').GetValue($completion)
    $index=if($Case -ceq 'CompletionDirectoryDigest'){5}else{4};$descriptor[$index]='0'*64
    if($Case -ceq 'CompletionAuthAndDigest'){$auth.Abort()}
   }
   $warmMethod=$completion.GetType().GetMethod('ClaimWarmOrigin',[Reflection.BindingFlags]'Instance,NonPublic')
   if($Case -ceq 'CompletionWarmBeforeReady'){$null=$warmMethod.Invoke($completion,[object[]]@($first,$auth,$admission))}
   if($Case -ceq 'CompletionSourceFault'){$first.Abort()}
   if($Case -ceq 'CompletionWrongMac'){
    $bytes=$completion.GetType().GetMethod('Packet',[Reflection.BindingFlags]'Instance,NonPublic').Invoke($completion,@())
    $bytes[$bytes.Length-3]=$bytes[$bytes.Length-3] -bxor 1;$client.WriteMessage($bytes)
   }else{$completion.SendChildReady($admission)}
   $ownClaimCase=$Case -cin @('CompletionWarmClaimsBefore','CompletionWarmClaimsAfter','CompletionWarmClaimsAndNative','CompletionWarmClaimsAfterNative','CompletionWarmSelfNull','CompletionWarmSelfShort','CompletionWarmScalarCopy')
   if($ownClaimCase){
    $f=[Reflection.BindingFlags]'Instance,NonPublic';$field=$completion.GetType().GetField('binding',$f)
    $after=$Case -cin @('CompletionWarmClaimsAfter','CompletionWarmClaimsAfterNative','CompletionWarmSelfNull','CompletionWarmSelfShort','CompletionWarmScalarCopy')
    if($after){$warm=$warmMethod.Invoke($completion,[object[]]@($first,$auth,$admission))}
    if($Case -ceq 'CompletionWarmScalarCopy'){
     $read=$admission.GetType().GetMethod('CheckClaimScalars',$f);$one=$read.Invoke($admission,[object[]]@($binding));$one[1]='OWN_CALLER_MUTATION'
     $two=$read.Invoke($admission,[object[]]@($binding));if($two[1] -cne $generation){throw 'OWN_SCALAR_COPY_NOT_ISOLATED'}
     $null=$warm.GetType().GetMethod('CheckValidatedOrigin',$f).Invoke($warm,[object[]]@($first,$auth,$admission))
    }else{
    $descriptor=$field.GetValue($completion)
    if($Case -cin @('CompletionWarmSelfNull','CompletionWarmSelfShort')){
     if($Case -ceq 'CompletionWarmSelfNull'){$admission.GetType().GetField('self',$f).SetValue($admission,$null)}
     else{$admission.GetType().GetField('self',$f).SetValue($admission,[string[]]@('1'))}
    }
    elseif($ClaimIndex -eq -1){$field.SetValue($completion,$null)}
    elseif($ClaimIndex -ge 6){$field.SetValue($completion,[string[]]::new($ClaimIndex-6))}
    elseif($ClaimIndex -cin @(2,3)){
     $length=if($ClaimIndex -eq 2){32}else{64};$replacement='0'*$length
     if($descriptor[$ClaimIndex] -ceq $replacement){$replacement='1'*$length}
     $descriptor[$ClaimIndex]=$replacement
    }else{$descriptor[$ClaimIndex]='OWN_MUTATED_CLAIM'}
    $nativeFault=$Case -cin @('CompletionWarmClaimsAndNative','CompletionWarmClaimsAfterNative')
    if($nativeFault){$first.Abort()}
    $claimCode='';try{
     if($after){$null=$warm.GetType().GetMethod('CheckValidatedOrigin',$f).Invoke($warm,[object[]]@($first,$auth,$admission))}
     else{$null=$warmMethod.Invoke($completion,[object[]]@($first,$auth,$admission))}
    }catch{$e=$_.Exception;while($e.InnerException){$e=$e.InnerException};$claimCode=$e.Message}
    $wantedClaim=if($nativeFault){'GMGN_SOURCE_LOCKED'}elseif($Case -cin @('CompletionWarmSelfNull','CompletionWarmSelfShort')){'GMGN_SOURCE_ADMISSION_SELF'}else{'GMGN_WARM_ORIGIN_SOURCE'}
    if($claimCode -cne $wantedClaim -or -not $admission.Faulted -or $completion.State -cne 'FAULT'){throw ('OWN_WARM_CLAIM_NOT_ABSORBED_'+$claimCode)}
    if($binding.Faulted -ne $nativeFault){throw 'OWN_WARM_CLAIM_PIN_OWNERSHIP'}
    $repeat='';try{$null=$warmMethod.Invoke($completion,[object[]]@($first,$auth,$admission))}catch{$e=$_.Exception;while($e.InnerException){$e=$e.InnerException};$repeat=$e.Message}
    if($repeat -cne 'GMGN_WARM_ORIGIN_BINDING'){throw 'OWN_WARM_CLAIM_REOPENED'}
    }
   }
   if($Case -clike 'CompletionWarm*' -and $Case -cne 'CompletionWarmRoot' -and -not $ownClaimCase){
    $selectedSource=$first;$selectedAuth=$auth;$selectedAdmission=$admission
    if($Case -ceq 'CompletionWarmSource'){$selectedSource=[GmgnImmutableSources]::new('%USERPROFILE%','C:\Program Files\nodejs\node.exe',[string[]]@($files|ForEach-Object {$_.path}),[string[]]@($files|ForEach-Object {$_.sha256}))}
    if($Case -ceq 'CompletionWarmAuth'){$selectedAuth=[GmgnReadLocalAuth]::OpenExisting($b[1],$b[2],$b[3],$b[4],$b[5])}
    if($Case -ceq 'CompletionWarmAdmission'){
     # A valid OWN object with the same source references but different identity.
     $selectedAdmission=$admission.GetType().GetMethod('MemberwiseClone',[Reflection.BindingFlags]'Instance,NonPublic').Invoke($admission,@())
     $selectedAdmission.Check()
    }
    $warm=$warmMethod.Invoke($completion,[object[]]@($selectedSource,$selectedAuth,$selectedAdmission))
    $originalRoot.Abort();$warm.Check() # Never re-query the original Root after successful send.
    $snapshot=$warm.ReadBinding();if($warm.State -cne 'ORIGIN_BOUND' -or $snapshot.Length -ne 14 -or $snapshot[1] -cne $generation){throw 'OWN_WARM_ORIGIN_NOT_BOUND'}
    $snapshot[1]='MUTATED';if($warm.ReadBinding()[1] -cne $generation){throw 'OWN_WARM_ORIGIN_MUTABLE'}
    if($Case -ceq 'CompletionWarmReplay'){$null=$warmMethod.Invoke($completion,[object[]]@($first,$auth,$admission))}
    if($Case -ceq 'CompletionWarmFault'){$first.Abort();$warm.Check()}
   }
  }
  if($Case -cin @('Admission','AdmissionReplay','AdmissionSourceFault','ManifestMismatch','AdmissionRootExit')){
   $selectedManifest=if($Case -ceq 'ManifestMismatch'){Join-Path (Join-Path $Scope 'other') 'package.json'}else{$manifestPath}
   if(-not $admission){$admission=$handshake.CreateChildAdmission($selectedManifest)}
   if($handshake.State -cne 'TRANSFERRED'){throw 'OWN_ADMISSION_NOT_TRANSFERRED'}
   $client.Dispose();$admission.Check()
   if($Case -ceq 'AdmissionRootExit'){
    [IO.File]::WriteAllText((Join-Path $Scope 'child-admission-ready'),'OWN_CHILD_HAS_INDEPENDENT_PINS')
    $clock=[Diagnostics.Stopwatch]::StartNew();while($originalRoot.ReadOriginalExitCode() -lt 0 -and $clock.Elapsed.TotalSeconds -lt 15){Start-Sleep -Milliseconds 10}
    if($originalRoot.ReadOriginalExitCode() -ne 0){throw 'OWN_ADMISSION_ORIGINAL_ROOT_EXIT_UNCONFIRMED'}
    $admission.Check()
    $warmOrigin=$warmMethod.Invoke($completion,[object[]]@($first,$auth,$admission));$warmOrigin.Check()
    if($warmOrigin.State -cne 'ORIGIN_BOUND'){throw 'OWN_WARM_ORIGIN_AFTER_ROOT_EXIT'}
   }else{$originalRoot.Abort()}
   $admitted=$admission.ReadBinding();$admitted[1]='caller mutation';$admission.Check()
   if($admission.ReadBinding()[1] -cne $generation){throw 'OWN_ADMISSION_CALLER_MUTATION'}
   if($Case -ceq 'AdmissionReplay'){$null=$handshake.CreateChildAdmission($manifestPath)}
   if($Case -ceq 'AdmissionSourceFault'){$first.Abort();$admission.Check()}
   [IO.File]::WriteAllText((Join-Path $Scope 'child-admission.json'),(@{pass=$true;independent_of_peer=$true;warm_origin_independent=($Case -ceq 'AdmissionRootExit' -and $warmOrigin.State -ceq 'ORIGIN_BOUND');original_root_exit=($Case -ceq 'AdmissionRootExit');root_exit_during_send=[OwnCompletionRootExit]::Observed;generation=$generation}|ConvertTo-Json -Compress))
   if($Case -ceq 'AdmissionRootExit'){
    $clock=[Diagnostics.Stopwatch]::StartNew();while(-not [IO.File]::Exists((Join-Path $Scope 'outer-child-exit')) -and $clock.Elapsed.TotalSeconds -lt 15){Start-Sleep -Milliseconds 10}
    if(-not [IO.File]::Exists((Join-Path $Scope 'outer-child-exit'))){throw 'OWN_ADMISSION_OUTER_CLEANUP_NOT_REACHED'}
   }
  }
  if($Case -cne 'AdmissionRootExit'){$clock=[Diagnostics.Stopwatch]::StartNew();while(-not [IO.File]::Exists((Join-Path $Scope 'child-exit')) -and $clock.Elapsed.TotalSeconds -lt 10){Start-Sleep -Milliseconds 10}}
 }
}catch{
 $e=$_.Exception;while($e.InnerException){$e=$e.InnerException};$code=$e.Message
 if($code -cnotmatch '\A(?:GMGN|OWN)_[A-Z0-9_]+\z'){$code='OWN_PIN_ACK_CHILD_INTERNAL'}
 if($Case -cin @('CompletionFileDigest','CompletionDirectoryDigest','CompletionAuthAndDigest') -and ($binding.Faulted -or $admission.Faulted)){$code='OWN_COMPLETION_FAULT_ISOLATION'}
}finally{
 [IO.File]::WriteAllText((Join-Path $Scope 'child-result.json'),(@{code=$code;provider_calls=0}|ConvertTo-Json -Compress))
 if($client){$client.Dispose()};foreach($pin in $streams){$pin.Dispose()}
}
if($Case -cin @('SourceFault','AbortBeforeCommit','CompletionSourceFault','CompletionBeforeAdmission','CompletionDuplicateChild','TransportDuplicateChild','CompletionFileDigest','CompletionDirectoryDigest','CompletionAuthAndDigest')){
 # Keep the original peer alive after closing its pipe, so this fixture
 # specifically tests disconnect rather than racing native process exit.
 $clock=[Diagnostics.Stopwatch]::StartNew();while(-not [IO.File]::Exists((Join-Path $Scope 'child-exit')) -and $clock.Elapsed.TotalSeconds -lt 10){Start-Sleep -Milliseconds 10}
 if(-not [IO.File]::Exists((Join-Path $Scope 'child-exit'))){exit 1}
}
exit 0
'@
[IO.File]::WriteAllText($worker,$workerText,[Text.UTF8Encoding]::new($false));$paths.Add($worker)
$workerPin=[IO.FileStream]::new($worker,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read);$pins.Add($workerPin)
$generationPath=Join-Path $scope 'package.json'
$generationMembers=@($paths|ForEach-Object {@{path=$_;sha256=(Get-FileHash -LiteralPath $_).Hash.ToLowerInvariant()}})
[IO.File]::WriteAllText($generationPath,(@{schema_version=1;files=$generationMembers}|ConvertTo-Json -Depth 5 -Compress),[Text.UTF8Encoding]::new($false));$paths.Add($generationPath)
$otherDirectory=Join-Path $scope 'other';$null=[IO.Directory]::CreateDirectory($otherDirectory)
$otherManifest=Join-Path $otherDirectory 'package.json';[IO.File]::WriteAllText($otherManifest,'{}');$paths.Add($otherManifest)
$manifest=@($paths|ForEach-Object {@{path=$_;sha256=(Get-FileHash -LiteralPath $_).Hash.ToLowerInvariant()}})
$generation=(Get-FileHash -LiteralPath $generationPath).Hash.ToLowerInvariant()
$inventory=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($manifest|ConvertTo-Json -Compress)))
$first=[GmgnImmutableSources]::new('%USERPROFILE%','C:\Program Files\nodejs\node.exe',[string[]]@($manifest|ForEach-Object {$_.path}),[string[]]@($manifest|ForEach-Object {$_.sha256}))
$binding=[GmgnSourcePinBinding]::new($first);$directory=[GmgnReadDirectoryIdentity]::new($scope)
$root=[GmgnRepairProcessBinding]::Read([Diagnostics.Process]::GetCurrentProcess());$rootEncoded=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($root|ConvertTo-Json -Compress)))
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value;$pipeName='imc-read-bootstrap-'+[guid]::NewGuid().ToString('N')
$session=[guid]::NewGuid().ToString('N');$challengeBytes=[byte[]]::new(32);$rng=[Security.Cryptography.RandomNumberGenerator]::Create();try{$rng.GetBytes($challengeBytes)}finally{$rng.Dispose()};$challenge=[BitConverter]::ToString($challengeBytes).Replace('-','').ToLowerInvariant()
$server=[GmgnSupervisorPipe]::new($pipeName,$sid,$true,15000);$created=$false;$task=$null;$action=$null;$handshake=$null;$code=''
function Cause($record){$e=$record.Exception;while($e.InnerException){$e=$e.InnerException};return $e.Message}
try{
 $workerHash=(Get-FileHash -LiteralPath $worker).Hash.ToLowerInvariant()
 $launch='$ErrorActionPreference="Stop";$s=[IO.FileStream]::new("'+$worker+'",[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read);$h=[Security.Cryptography.SHA256]::Create();try{$v=[BitConverter]::ToString($h.ComputeHash($s)).Replace("-","").ToLowerInvariant()}finally{$h.Dispose()};if($v -cne "'+$workerHash+'"){exit 125};$s.Position=0;$r=[IO.StreamReader]::new($s);& ([scriptblock]::Create($r.ReadToEnd())) -Scope "'+$scope+'" -PipeName '+$pipeName+' -Sid '+$sid+' -RootBinding '+$rootEncoded+' -DirectoryId '+$directory.Identity+' -Inventory '+$inventory+' -Session '+$session+' -Challenge '+$challenge+' -Case '+$Case+' -AuthBinding '+$authBindingEncoded+' -ClaimIndex '+$ClaimIndex
 $args='-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -EncodedCommand '+[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($launch))
 $service=New-Object -ComObject Schedule.Service;$service.Connect();$folder=$service.GetFolder('\');$name='IMC-READ-FIXTURE-'+[guid]::NewGuid().ToString('N');$exe=Join-Path $PSHOME 'powershell.exe'
 $definition=New-GmgnSupervisorTaskDefinition $service $sid $exe $args $repo
 try{$task=Register-GmgnSupervisorTask $folder $name $definition $sid $exe $args $repo;$created=$true}catch{if($_.Exception.Data.Contains('GmgnCreatedTask')){$task=$_.Exception.Data['GmgnCreatedTask'];$created=$true};throw}
 $earliest=[DateTime]::UtcNow.ToString('o');$instance=Start-GmgnSupervisorTask $task $sid $exe $args $repo;$savedGuid=$instance.InstanceGuid;$instance.Refresh();$savedEngine=[int]$instance.EnginePID
 $action=Bind-GmgnSupervisorTaskAction $instance $task $savedGuid $savedEngine $sid $exe $args $repo $earliest
 $self=$action.ReadNativeBinding();$server.Accept();$action.CheckPeerPid($server.ReadKernelPeerPid())
 $handshake=[GmgnPinTransferHandshake]::new($server,$binding,$action,'ROOT',$generation,$session,$challenge,$root,$self)
 [IO.File]::WriteAllText((Join-Path $scope 'root-handshake-prepared'),'OWN_ROOT_HEALTHY_HANDSHAKE')
 try{
  if($Case -cin @('AbortAfterPinAck','ManipulatedCommit','ReplayNewSession')){
   $selected=$handshake
   $method=$selected.GetType().GetMethod('Message',[Reflection.BindingFlags]'Instance,NonPublic')
   $message=$method.Invoke($selected,[object[]]@(1))
   if($Case -ceq 'ReplayNewSession'){
    # Fabricate a frame from an old session. A second handshake object may
    # no longer take ownership of this original transport.
    $text=[Text.Encoding]::UTF8.GetString($message)
    foreach($entry in @(@('session_id',$session),@('challenge',$challenge))){
     $offset=$text.IndexOf(('"'+$entry[0]+'":"'),[StringComparison]::Ordinal)+$entry[0].Length+4
     if($offset -lt $entry[0].Length+4){throw 'OWN_PIN_ACK_REPLAY_FIELD_MISSING'}
     $text=$text.Remove($offset,$entry[1].Length).Insert($offset,('0'*$entry[1].Length))
    }
    $message=[Text.Encoding]::UTF8.GetBytes($text)
   }
   $server.WriteMessage($message)
   if($Case -cne 'ReplayNewSession'){
    $ack=$server.ReadMessage();$expectedAck=$method.Invoke($selected,[object[]]@(2))
    if([Convert]::ToBase64String($ack) -cne [Convert]::ToBase64String($expectedAck)){throw 'OWN_PIN_ACK_STEP_TWO_NOT_VALID'}
    $script:OwnStepTwoVerified=$true
    if($Case -ceq 'AbortAfterPinAck'){$server.Dispose()}
    else{$server.WriteMessage($method.Invoke($selected,[object[]]@(2)))}
   }
  }else{$handshake.Execute()}
 }catch{$code=Cause $_}
 if($Case -clike 'TransportDuplicate*'){
  if($code -or $handshake.State -cne 'COMPLETE'){throw 'OWN_TRANSPORT_FIRST_HANDSHAKE_NOT_COMPLETE'}
  if($Case -ceq 'TransportDuplicateRoot'){
   try{$null=[GmgnPinTransferHandshake]::new($server,$binding,$action,'ROOT',$generation,$session,$challenge,$root,$self)}catch{$code=Cause $_}
   if($code -cne 'GMGN_PIPE_HANDSHAKE_CLAIMED' -or $server.State -cne 'FAULT'){throw 'OWN_TRANSPORT_SECOND_ROOT_HANDSHAKE_ADMITTED'}
  }
  [IO.File]::WriteAllText((Join-Path $scope 'root-duplicate-test-ready'),'OWN_TRANSPORT_FIRST_SESSION_COMPLETE')
 }elseif($Case -clike 'Completion*' -or $Case -ceq 'AdmissionRootExit'){
  if($code -or $handshake.State -cne 'COMPLETE'){throw 'OWN_COMPLETION_HANDSHAKE_NOT_COMPLETE'}
  $completion=[GmgnHandoffCompletion]::new($server,$handshake,$binding,$action,$localAuth,'ROOT')
  if($Case -ceq 'CompletionDuplicateRoot'){
   try{$null=[GmgnHandoffCompletion]::new($server,$handshake,$binding,$action,$localAuth,'ROOT')}catch{$code=Cause $_}
   if($code -cne 'GMGN_PIN_ACK_COMPLETION_CLAIMED' -or $handshake.State -cne 'FAULT'){throw 'OWN_COMPLETION_SECOND_ROOT_ADMITTED'}
  }
  [IO.File]::WriteAllText((Join-Path $scope 'root-fourth-ready'),'OWN_COMPLETION_READY')
  if($Case -cne 'CompletionDuplicateRoot'){try{$completion.WaitChildReady()}catch{$code=Cause $_}}
  $expectedCompletion=switch($Case){'CompletionWrongMac'{'GMGN_HANDOFF_READY_MESSAGE'};'CompletionSourceFault'{'GMGN_PIPE_DISCONNECTED'};'CompletionBeforeAdmission'{'GMGN_PIPE_DISCONNECTED'};'CompletionWarmBeforeReady'{'GMGN_PIPE_DISCONNECTED'};'CompletionDuplicateChild'{'GMGN_PIPE_DISCONNECTED'};'CompletionDuplicateRoot'{'GMGN_PIN_ACK_COMPLETION_CLAIMED'};'CompletionFileDigest'{'GMGN_PIPE_DISCONNECTED'};'CompletionDirectoryDigest'{'GMGN_PIPE_DISCONNECTED'};'CompletionAuthAndDigest'{'GMGN_PIPE_DISCONNECTED'};default{''}}
  if($code -cne $expectedCompletion){throw ('OWN_COMPLETION_ROOT_REJECTION_'+$code)}
  if($Case -ceq 'CompletionReplay'){
   try{$completion.WaitChildReady()}catch{$code=Cause $_}
   if($code -cne 'GMGN_HANDOFF_READY_STATE' -or $completion.State -cne 'FAULT'){throw 'OWN_COMPLETION_REPLAY_ADMITTED'}
  }elseif($Case -ceq 'CompletionNormal' -and $completion.State -cne 'COMPLETE'){throw 'OWN_COMPLETION_INCOMPLETE'}
  if($Case -ceq 'CompletionWarmRoot'){
   try{$null=$completion.GetType().GetMethod('ClaimWarmOrigin',[Reflection.BindingFlags]'Instance,NonPublic').Invoke($completion,[object[]]@($first,$localAuth,$null))}catch{$code=Cause $_}
   if($code -cne 'GMGN_WARM_ORIGIN_BINDING' -or $completion.State -cne 'FAULT'){throw 'OWN_ROOT_WARM_ORIGIN_ADMITTED'}
  }
 }elseif($Case -cin @('Normal','Replay','RootAdmission')){
  if($code -or $handshake.State -cne 'COMPLETE'){throw ('OWN_PIN_ACK_NOT_COMPLETE_'+$code)}
  $complete=$handshake.ReadCompletedBinding();$clock=[Diagnostics.Stopwatch]::StartNew()
  while(-not [IO.File]::Exists((Join-Path $scope 'child-complete.json')) -and $clock.Elapsed.TotalSeconds -lt 5){Start-Sleep -Milliseconds 10}
  if(-not [IO.File]::Exists((Join-Path $scope 'child-complete.json'))){throw 'OWN_PIN_ACK_CHILD_NOT_COMPLETE'}
  $child=[IO.File]::ReadAllText((Join-Path $scope 'child-complete.json'))|ConvertFrom-Json
  if($child.state -cne 'COMPLETE' -or ($child.binding -join ':') -cne ($complete -join ':')){throw 'OWN_PIN_ACK_BINDING_DIFFERS'}
  if($Case -ceq 'Replay'){
   try{$handshake.Execute()}catch{$code=Cause $_}
   if($code -cne 'GMGN_PIN_ACK_USED' -or $handshake.State -cne 'FAULT'){throw 'OWN_PIN_ACK_REPLAY_ADMITTED'}
  }
  if($Case -ceq 'RootAdmission'){
   try{$null=$handshake.CreateChildAdmission($generationPath)}catch{$code=Cause $_}
   if($code -cne 'GMGN_PIN_ACK_ADMISSION_STATE'){throw 'OWN_ROOT_ADMISSION_NOT_BLOCKED'}
  }
 }elseif($Case -cin @('Admission','AdmissionReplay','AdmissionSourceFault','ManifestMismatch','AdmissionRootExit')){
  # Child may close the transport immediately after successful transfer.
  # Root's final Execute check is intentionally not used as its runtime gate.
  if($code -and $code -cnotmatch '\AGMGN_(?:PIPE_DISCONNECTED|PIPE_KERNEL_PEER)\z'){throw ('OWN_ADMISSION_ROOT_RESULT_'+$code)}
 }elseif($Case -cin @('AbortAfterPinAck','ManipulatedCommit','ReplayNewSession')){
  if($code -or $handshake.State -cne 'NEW'){throw 'OWN_PIN_ACK_RAW_TEST_UNEXPECTED_STATE'}
  if($Case -cne 'ReplayNewSession' -and -not $script:OwnStepTwoVerified){throw 'OWN_PIN_ACK_STEP_TWO_UNVERIFIED'}
 }elseif($Case -cin @('WrongSequence','ExtraField','MissingField','DuplicateField','WrongDirection')){
  if($code -cne 'GMGN_PIN_ACK_MESSAGE' -or $handshake.State -cne 'FAULT'){throw ('OWN_PIN_ACK_BAD_MESSAGE_ADMITTED_'+$code)}
 }else{
  if($code -cne 'GMGN_PIPE_DISCONNECTED' -or $handshake.State -cne 'FAULT'){throw ('OWN_PIN_ACK_BAD_NEGATIVE_'+$code)}
 }
 if($Case -ceq 'AdmissionRootExit'){
  if($completion.State -cne 'COMPLETE'){throw 'OWN_ADMISSION_AUTHENTICATED_READY_REQUIRED'}
  if(-not $RootExitProofPath -or [IO.Path]::GetFullPath($RootExitProofPath) -cne $RootExitProofPath -or -not $RootExitProofPath.StartsWith((Join-Path $repo '.local-tools')+'\',[StringComparison]::Ordinal)){throw 'OWN_ADMISSION_ROOT_EXIT_PROOF_PATH'}
  [IO.File]::WriteAllText($RootExitProofPath,(@{name=$name;arguments=$args;exe=$exe;sid=$sid;scope=$scope;child=$self;root=$root;directory_id=$directory.Identity}|ConvertTo-Json -Depth 4 -Compress))
  $script:OwnRootExitPrepared=$true
  return
 }
 [IO.File]::WriteAllText((Join-Path $scope 'child-exit'),'OWN_EXIT')
 $clock=[Diagnostics.Stopwatch]::StartNew();while($action.ReadOriginalExitCode() -lt 0 -and $clock.Elapsed.TotalSeconds -lt 10){Start-Sleep -Milliseconds 10}
 if($action.ReadOriginalExitCode() -ne 0){throw 'OWN_PIN_ACK_CHILD_EXIT_UNCONFIRMED'}
 $childResult=[IO.File]::ReadAllText((Join-Path $scope 'child-result.json'))|ConvertFrom-Json
 $expectedChild=switch($Case){'GenerationMismatch'{'GMGN_PIN_ACK_MESSAGE'};'DigestMismatch'{'GMGN_PIN_ACK_MESSAGE'};'DirectoryMismatch'{'GMGN_PIN_ACK_MESSAGE'};'SourceFault'{'GMGN_SOURCE_LOCKED'};'AbortAfterPinAck'{'GMGN_PIPE_DISCONNECTED'};'ManipulatedCommit'{'GMGN_PIN_ACK_MESSAGE'};'ReplayNewSession'{'GMGN_PIN_ACK_MESSAGE'};'AdmissionReplay'{'GMGN_PIN_ACK_ADMISSION_STATE'};'AdmissionSourceFault'{'GMGN_SOURCE_LOCKED'};'ManifestMismatch'{'GMGN_PIN_ACK_MANIFEST_GENERATION'};'CompletionSourceFault'{'GMGN_SOURCE_LOCKED'};'CompletionBeforeAdmission'{'GMGN_HANDOFF_READY_STATE'};'CompletionDuplicateChild'{'GMGN_PIN_ACK_COMPLETION_CLAIMED'};'TransportDuplicateChild'{'GMGN_PIPE_HANDSHAKE_CLAIMED'};
  'CompletionWarmReplay'{'GMGN_WARM_ORIGIN_BINDING'};'CompletionWarmSource'{'GMGN_WARM_ORIGIN_BINDING'};'CompletionWarmAuth'{'GMGN_WARM_ORIGIN_BINDING'};'CompletionWarmAdmission'{'GMGN_WARM_ORIGIN_BINDING'};'CompletionWarmBeforeReady'{'GMGN_WARM_ORIGIN_BINDING'};'CompletionWarmFault'{'GMGN_SOURCE_LOCKED'};'CompletionFileDigest'{'GMGN_HANDOFF_READY_SOURCE'};'CompletionDirectoryDigest'{'GMGN_HANDOFF_READY_SOURCE'};'CompletionAuthAndDigest'{'GMGN_AUTH_FAULT'};default{''}}
 if($childResult.code -cne $expectedChild){throw ('OWN_PIN_ACK_WRONG_CHILD_REJECTION_'+$childResult.code)}
 $hashes=@{}
 foreach($namePart in $names){$hashes[$namePart]=(Get-FileHash -LiteralPath (Join-Path $scope ($namePart+'.cs'))).Hash.ToLowerInvariant()}
 [pscustomobject]@{kind='NATIVE_PIN_ACK_FIXTURE';case=$Case;pass=$true;code=$code;child_code=$childResult.code;child_native_exit=$action.ReadOriginalExitCode();provider_calls=0;ipc_token_transferred=$false;production_verified=$false;step_two_verified=[bool]$script:OwnStepTwoVerified;source_hashes=$hashes;fixture_sha256=(Get-FileHash -LiteralPath $PSCommandPath).Hash.ToLowerInvariant();worker_sha256=$workerHash}|ConvertTo-Json -Depth 4 -Compress
}finally{
 [IO.File]::WriteAllText((Join-Path $scope 'child-exit'),'OWN_FINALLY_EXIT');$server.Dispose()
 if($created -and -not $script:OwnRootExitPrepared){
  $clock=[Diagnostics.Stopwatch]::StartNew();while($task.GetInstances(0).Count -and $clock.Elapsed.TotalSeconds -lt 20){Start-Sleep -Milliseconds 10}
  if($task.GetInstances(0).Count){throw 'OWN_PIN_ACK_TASK_RETAINED'}
  $current=$folder.GetTask($name);Assert-GmgnSupervisorTaskDefinition $current.Definition $sid $exe $args $repo
  if($action){
   $clock.Restart();while($action.ReadOriginalExitCode() -lt 0 -and $clock.Elapsed.TotalSeconds -lt 5){Start-Sleep -Milliseconds 10}
   if($action.ReadOriginalExitCode() -lt 0){throw 'OWN_PIN_ACK_FINAL_NATIVE_EXIT_UNCONFIRMED'}
  }
  $folder.DeleteTask($name,0)
  $absent=$false;try{$null=$folder.GetTask($name)}catch{$cause=$_.Exception;while($cause.InnerException){$cause=$cause.InnerException};$absent=($cause.HResult -band 65535) -eq 2}
  if(-not $absent){throw 'OWN_PIN_ACK_TASK_DELETE_UNCONFIRMED'}
 }
 foreach($pin in $pins){$pin.Dispose()}
}
