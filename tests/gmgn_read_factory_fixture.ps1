param([ValidateSet('Normal','Mutation','DuplicateOption','AmountOverflow','PrivateTicket','CallerMutation','StartFailure','Aborted','ParentExit','DelayedParentExit','WrongParentBirth','ConcurrentDispose','AfterCreateFailure','FilteredEnvironment','StreamDisposeFailure','GenerationRead','GenerationNull','GenerationSame','GenerationTicketFault')][string]$Case='Normal')
$ErrorActionPreference='Stop';$repo=Split-Path -Parent $PSScriptRoot
$tools=Join-Path $repo 'TOOLS'
. (Join-Path $repo 'TOOLS\IMC_RUNTIME_JOB.ps1')
Add-Type -Path @((Join-Path $tools 'GmgnRepairNamespace.cs'),(Join-Path $tools 'GmgnImmutableSources.cs'),(Join-Path $tools 'GmgnRepairProcessBinding.cs'),(Join-Path $tools 'GmgnReadProcessLifetime.cs'))
$scope=Join-Path (Join-Path $repo '.local-tools') ('healthy-read-native-'+[guid]::NewGuid().ToString('N'));$null=[IO.Directory]::CreateDirectory($scope)
$node='C:\Program Files\nodejs\node.exe';$entry=Join-Path $scope 'entry.mjs';$bootstrap=Join-Path $scope 'bootstrap.mjs'
$utf8=[Text.UTF8Encoding]::new($false)
[IO.File]::WriteAllText($bootstrap,'// OWN fixture preload, no SDK/config/auth/fetch.',$utf8)
$body=if($Case -cin @('DelayedParentExit','ConcurrentDispose')){'console.log("OWN_READ_RUNNING");await new Promise(resolve=>setTimeout(resolve,10000));'}else{'console.log(JSON.stringify({own:true,requests:0,args:process.argv.slice(2),provider_env_inherited:!!process.env.GMGN_API_KEY,node_preload_inherited:!!process.env.NODE_OPTIONS,retry:process.env.GMGN_RATE_LIMIT_AUTO_RETRY_MAX_WAIT_MS}));'}
[IO.File]::WriteAllText($entry,$body,$utf8)
$paths=[string[]]@($node,$entry,$bootstrap);$hashes=[string[]]@($paths|ForEach-Object {(Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash.ToLowerInvariant()})
$sources=[GmgnImmutableSources]::new('%USERPROFILE%',$node,$paths,$hashes)
$firstPath=Join-Path $scope 'first.mjs';[IO.File]::WriteAllText($firstPath,'// OWN first generation, no executable code.',$utf8)
$first=[GmgnImmutableSources]::new('%USERPROFILE%',$node,[string[]]@($firstPath),[string[]]@((Get-FileHash -LiteralPath $firstPath -Algorithm SHA256).Hash.ToLowerInvariant()))
$job=[IMCRuntimeJob]::new();$shutdown=$false;$parent=$null;$p=$null;$life=$null;$code='';$started=$false;$nativeError=$null
function AwaitFactory([scriptblock]$Condition,[string]$Failure){$clock=[Diagnostics.Stopwatch]::StartNew();while(-not (& $Condition) -and $clock.Elapsed.TotalSeconds -lt 5){Start-Sleep -Milliseconds 5};if(-not (& $Condition)){throw $Failure}}
try{
 if($Case -cin @('ParentExit','DelayedParentExit')){
  $parentFile=Join-Path $scope 'parent.ps1'
  [IO.File]::WriteAllText($parentFile,'param([string]$Scope);while(-not [IO.File]::Exists((Join-Path $Scope "parent-exit"))){Start-Sleep -Milliseconds 5}',$utf8)
  $id=$job.Start((Join-Path $PSHOME 'powershell.exe'),[string[]]@('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',$parentFile,'-Scope',$scope),$scope);$job.ObserveMember($id)
  $parent=[Diagnostics.Process]::GetProcessById($id);$null=$parent.Handle
 }else{$parent=[Diagnostics.Process]::GetCurrentProcess()}
 $birth=$parent.StartTime.ToUniversalTime().ToString('o')
 if($Case -ceq 'WrongParentBirth'){
  try{$life=[GmgnReadProcessLifetime]::new($sources,$node,$entry,$bootstrap,$parent.Id,'2000-01-01T00:00:00.0000000Z',$parent.MainModule.FileName)}catch{$code=$_.Exception.InnerException.Message}
  if($life -or $code -cne 'GMGN_READ_NATIVE_PARENT_BINDING'){throw 'OWN_FACTORY_WRONG_PARENT_ACCEPTED'}
 }else{
 $life=[GmgnReadProcessLifetime]::new($sources,$node,$entry,$bootstrap,$parent.Id,$birth,$parent.MainModule.FileName)
 $arguments=[string[]]@('portfolio','info','--raw')
 switch -CaseSensitive($Case){
  'Mutation'{$arguments=[string[]]@('order','create','--raw')}
  'DuplicateOption'{$arguments=[string[]]@('order','get','--chain','sol','--chain','sol','--order-id','own','--raw')}
  'AmountOverflow'{$arguments=[string[]]@('order','quote','--chain','sol','--from',('a'*32),'--input-token',('a'*32),'--output-token',('b'*32),'--amount','18446744073709551616','--slippage','1','--raw')}
  'StartFailure'{
   # Own fault injection only: point the private readonly executable field at
   # this fixture's pinned text file, forcing a real Win32 bad-image failure.
   if($entry -cnotmatch '\\[.]local-tools\\healthy-read-native-[0-9a-f]{32}\\entry[.]mjs$'){throw 'OWN_FACTORY_FAULT_INJECTION_SCOPE'}
   $life.GetType().GetField('node',[Reflection.BindingFlags]'Instance,NonPublic').SetValue($life,$entry)
  }
  'AfterCreateFailure'{
   Add-Type -TypeDefinition 'using System;public static class OwnNativeCreatedFailure{public static Action Callback(){return delegate{throw new InvalidOperationException("OWN_AFTER_NATIVE_CREATION");};}}'
   $life.GetType().GetField('afterCreateFixtureProbe',[Reflection.BindingFlags]'Instance,NonPublic').SetValue($life,[OwnNativeCreatedFailure]::Callback())
  }
  'FilteredEnvironment'{
   # Own dummy values only. No provider configuration/key is read by Root.
   $env:GMGN_API_KEY='OWN_FAKE_NOT_A_PROVIDER_KEY';$env:NODE_OPTIONS='--import ./OWN_UNREVIEWED_PRELOAD.mjs'
  }
  'Aborted'{$life.Abort()}
  'ParentExit'{[IO.File]::WriteAllText((Join-Path $scope 'parent-exit'),'OWN_EXIT');AwaitFactory {-not $job.Running($id)} 'OWN_FACTORY_PARENT_TIMEOUT'}
 }
 if($Case -ceq 'GenerationTicketFault'){
  Add-Type -TypeDefinition 'using System;public static class OwnWrapperTicketFault{public static Action Make(object first){return delegate{first.GetType().GetMethod("Abort").Invoke(first,null);};}}'
  $life.GetType().GetField('afterGenerationTicketFixtureProbe',[Reflection.BindingFlags]'Instance,NonPublic').SetValue($life,[OwnWrapperTicketFault]::Make($first))
 }
 try{
  if($Case -clike 'Generation*'){
   $usedFirst=if($Case -ceq 'GenerationNull'){$null}elseif($Case -ceq 'GenerationSame'){$sources}else{$first}
   $method=$life.GetType().GetMethod('StartReadWithinGeneration',[Reflection.BindingFlags]'Instance,NonPublic')
   $p=$method.Invoke($life,[object[]]@($usedFirst,$arguments))
  }else{$p=$life.StartRead($arguments)}
  $started=$null -ne $p
 }catch{$cause=$_.Exception;while($cause.InnerException){$cause=$cause.InnerException};$code=$cause.Message;$nativeError=$cause.Data['native_error']}
 if($Case -cin @('Normal','PrivateTicket','CallerMutation','DelayedParentExit','ConcurrentDispose','FilteredEnvironment','StreamDisposeFailure','GenerationRead')){
  if(-not $started -or $code){throw 'OWN_FACTORY_NOT_STARTED'}
  if($Case -ceq 'CallerMutation'){$arguments[0]='order';$arguments[1]='create'}
  $p.StandardInput.Close()
  if($Case -ceq 'ConcurrentDispose'){
   if($p.StandardOutput.ReadLine() -cne 'OWN_READ_RUNNING'){throw 'OWN_FACTORY_CONCURRENT_RUNNING'}
   Add-Type -TypeDefinition 'using System;using System.Threading;public sealed class OwnReadTicketWait{readonly object ticket;readonly Thread thread;public volatile int Result;public OwnReadTicketWait(object value){ticket=value;thread=new Thread(Run);thread.IsBackground=true;thread.Start();}void Run(){try{Result=(bool)ticket.GetType().GetMethod("WaitForExit").Invoke(ticket,new object[]{3000})?1:2;}catch{Result=3;}}public bool Join(){return thread.Join(4000);}}'
   $waiting=[OwnReadTicketWait]::new($p)
   $internalChild=$p.GetType().GetField('child',[Reflection.BindingFlags]'Instance,NonPublic').GetValue($p)
   $waitersField=$internalChild.GetType().GetField('Waiters',[Reflection.BindingFlags]'Instance,NonPublic,Public')
   AwaitFactory {$waitersField.GetValue($internalChild) -gt 0} 'OWN_FACTORY_CONCURRENT_WAITER_NOT_ENTERED'
   $p.Dispose()
   if(-not $waiting.Join() -or $waiting.Result -ne 1 -or -not $p.HasExited -or $life.Status()[1] -cne 'False'){throw 'OWN_FACTORY_CONCURRENT_DISPOSE_FAILED'}
  }elseif($Case -ceq 'DelayedParentExit'){
   if($p.StandardOutput.ReadLine() -cne 'OWN_READ_RUNNING'){throw 'OWN_FACTORY_RUNNING_MARKER'}
   [IO.File]::WriteAllText((Join-Path $scope 'parent-exit'),'OWN_EXIT');AwaitFactory {-not $job.Running($id)} 'OWN_FACTORY_DELAYED_PARENT_TIMEOUT'
   AwaitFactory {$p.HasExited -and $life.Status()[0] -ceq 'FAULT' -and $life.Status()[2] -ceq '0'} 'OWN_FACTORY_CHILD_NOT_CANCELED'
  }else{
   if(-not $p.WaitForExit(3000) -or $p.ExitCode -ne 0){throw 'OWN_FACTORY_NODE_EXIT'}
   $output=$p.StandardOutput.ReadToEnd()|ConvertFrom-Json
   if(-not $output.own -or $output.requests -ne 0 -or ($output.args -join ' ') -cne 'portfolio info --raw'){throw 'OWN_FACTORY_NODE_OUTPUT'}
   if($output.provider_env_inherited -or $output.node_preload_inherited -or $output.retry -cne '0'){throw 'OWN_FACTORY_ENVIRONMENT_LEAK'}
   AwaitFactory {$life.Status()[2] -ceq '0'} 'OWN_FACTORY_CHILD_REAP'
   if($Case -ceq 'PrivateTicket'){
    foreach($name in @('StartInfo','Handle','Id','Start')){if($p.GetType().GetMember($name,[Reflection.BindingFlags]'Instance,Public').Length){throw 'OWN_FACTORY_PROCESS_EXPOSED'}}
    if($life.GetType().GetMethod('StartReadProcess')){throw 'OWN_FACTORY_MUTABLE_PROCESS_API_REMAINS'}
   }
   $life.Check()
  }
  if($Case -ceq 'StreamDisposeFailure'){
   Add-Type -TypeDefinition 'using System.IO;public sealed class OwnDisposeFailureWriter:StreamWriter{public OwnDisposeFailureWriter():base(new MemoryStream()){}protected override void Dispose(bool disposing){throw new IOException("OWN_STREAM_DISPOSE_FAILURE");}}'
   $internalChild=$p.GetType().GetField('child',[Reflection.BindingFlags]'Instance,NonPublic').GetValue($p)
   $internalChild.GetType().GetField('In',[Reflection.BindingFlags]'Instance,NonPublic,Public').SetValue($internalChild,[OwnDisposeFailureWriter]::new())
   try{$p.Dispose()}catch{$code=$_.Exception.InnerException.Message}
   if($code -cne 'GMGN_READ_NATIVE_TICKET_CLEANUP' -or $life.Status()[0] -cne 'FAULT' -or $life.Status()[1] -cne 'True' -or $life.Status()[3] -cne '0'){throw 'OWN_FACTORY_STREAM_FAILURE_NOT_CLEANED'}
  }
 }else{
  $wanted=if($Case -cin @('Aborted','ParentExit')){@('GMGN_READ_NATIVE_FAULT','GMGN_READ_NATIVE_PARENT_ENDED')}elseif($Case -ceq 'StartFailure'){@('GMGN_READ_NATIVE_START_FAILED')}elseif($Case -ceq 'AfterCreateFailure'){@('OWN_AFTER_NATIVE_CREATION')}elseif($Case -cin @('GenerationNull','GenerationSame')){@('GMGN_READ_NATIVE_GENERATION_CONTEXT')}elseif($Case -ceq 'GenerationTicketFault'){@('GMGN_SOURCE_LOCKED')}else{@('GMGN_READ_NATIVE_COMMAND_BLOCKED')}
  if($started -or $code -cnotin $wanted -or $life.Status()[0] -cne 'FAULT'){throw ('OWN_FACTORY_WRONG_REJECT_'+$code)}
  if($Case -ceq 'StartFailure' -and $nativeError -ne 193){throw 'OWN_FACTORY_BAD_IMAGE_NOT_NATIVE_193'}
  if($Case -ceq 'AfterCreateFailure'){AwaitFactory {$life.Status()[2] -ceq '0'} 'OWN_FACTORY_SUSPENDED_CHILD_NOT_CLEANED'}
  if($Case -clike 'Generation*'){
   AwaitFactory {$life.Status()[2] -ceq '0' -and $life.Status()[3] -ceq '0'} 'OWN_FACTORY_UNDELIVERED_TICKET_RETAINED'
   foreach($operation in @({$life.Check()},{$null=$life.StartRead([string[]]@('portfolio','info','--raw'))})){
    $rejected='';try{& $operation}catch{$cause=$_.Exception;while($cause.InnerException){$cause=$cause.InnerException};$rejected=$cause.Message};if($rejected -cne 'GMGN_READ_NATIVE_FAULT'){throw 'OWN_FACTORY_GENERATION_FAULT_REOPENED'}
   }
  }
 }
 }
 # A PASS is emitted only after ticket cleanup, not before the finally path.
 if($p){$p.Dispose()}
 $shutdown=$true;$job.ShutdownWithin(10000);if(-not $job.CleanupConfirmed){throw 'OWN_FACTORY_JOB_CLEANUP'}
 $expectedCleanupFault=$Case -ceq 'StreamDisposeFailure'
 if($life -and (($life.Status()[1] -ceq 'True') -ne $expectedCleanupFault -or $life.Status()[2] -cne '0' -or $life.Status()[3] -cne '0')){throw 'OWN_FACTORY_NATIVE_CHILD_CLEANUP'}
 [pscustomobject]@{kind='HEALTHY_NATIVE_READ_FACTORY_FIXTURE';case=$Case;pass=$true;started=$started;code=$code;state=$(if($life){$life.Status()}else{'REJECTED_BEFORE_START'});
  native_error=$nativeError;
  factory_sha256=(Get-FileHash -LiteralPath (Join-Path $tools 'GmgnReadProcessLifetime.cs') -Algorithm SHA256).Hash.ToLowerInvariant();
  native_job_cleanup_confirmed=$job.CleanupConfirmed;provider_calls=0;production_verified=$false}|ConvertTo-Json -Compress
}finally{
 if($life){$life.Abort()};if($p){if($started -and -not $p.HasExited){if(-not $p.WaitForExit(3000)){throw 'OWN_FACTORY_FINAL_CHILD_EXIT'}};$p.Dispose()}
 if($parent){$parent.Dispose()};if(-not $shutdown){$shutdown=$true;$job.ShutdownWithin(10000)};if(-not $job.CleanupConfirmed){throw 'OWN_FACTORY_FINAL_JOB_CLEANUP'}
}
