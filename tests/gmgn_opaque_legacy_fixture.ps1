param([ValidateSet('Normal','WrongEnvironment','LegacyAbsent','Null','False','Empty','ForeignType','WrongMac','Faulted','FactoryNull','FactoryWrongMac','FactoryFaulted','RetireAuthFault','RetireNormal','RetireLateAuthFault','RetireOwnerDeadline')][string]$Case='Normal')
$ErrorActionPreference='Stop';$repo=Split-Path -Parent $PSScriptRoot;$tools=Join-Path $repo 'TOOLS'
. (Join-Path $tools 'IMC_RUNTIME_JOB.ps1')
$names=@('GmgnRepairNamespace','GmgnImmutableSources','GmgnRepairProcessBinding','GmgnReadDirectoryIdentity','GmgnLegacyLifetimePins','GmgnLegacyCapture','GmgnLegacyRetirement','GmgnReadLocalAuth')
$code=[string[]]@($names|ForEach-Object {Join-Path $tools ($_+'.cs')});$code+=Join-Path $tools 'GmgnLegacyRetirement.Auth.cs'
Add-Type -Path $code -ReferencedAssemblies @('System.dll','System.Core.dll',[Management.Automation.PSObject].Assembly.Location)
$scope=Join-Path (Join-Path $repo '.local-tools') ('legacy-capture-native-'+[guid]::NewGuid().ToString('N'));$null=[IO.Directory]::CreateDirectory($scope)
$legacy=Join-Path $scope 'legacy';$capturePath=Join-Path $scope 'capture';foreach($p in @($legacy,$capturePath)){$null=[IO.Directory]::CreateDirectory($p)}
$utf8=[Text.UTF8Encoding]::new($false);$brokerScript=Join-Path $scope 'broker.ps1';$parentScript=Join-Path $scope 'parent.ps1'
[IO.File]::WriteAllText($brokerScript,'param($Scope);$c=[Diagnostics.Stopwatch]::StartNew();while($c.Elapsed.TotalSeconds -lt 25){Start-Sleep -Milliseconds 20}',$utf8)
$parent=@'
param($Scope,$Tools)
$ErrorActionPreference='Stop';Add-Type -Path (Join-Path $Tools 'GmgnRepairProcessBinding.cs')
$script=Join-Path $Scope 'broker.ps1'
$child=Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') -ArgumentList @('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',('"'+$script+'"'),'-Scope',('"'+$Scope+'"')) -PassThru -WindowStyle Hidden
$null=$child.Handle;$self=[Diagnostics.Process]::GetCurrentProcess()
$broker=[GmgnRepairProcessBinding]::Read($child);$owner=[GmgnRepairProcessBinding]::Read($self)
function Binding($v,$s){@{pid=[int]$v[0];parent_pid=[int]$v[1];created_at=$v[2];executable=$v[3];script=$s}}
$body=@{schema_version=1;instance_id=('d'*32);directory=(Join-Path $Scope 'legacy');broker=(Binding $broker $script);parent=(Binding $owner (Join-Path $Scope 'parent.ps1'))}|ConvertTo-Json -Compress -Depth 5
$h=[Security.Cryptography.HMACSHA256]::new([Text.Encoding]::ASCII.GetBytes('f'*64))
try{$mac=([BitConverter]::ToString($h.ComputeHash([Text.Encoding]::UTF8.GetBytes($body)))).Replace('-','').ToLowerInvariant()}finally{$h.Dispose()}
[IO.File]::WriteAllText((Join-Path $Scope 'legacy\broker.ready'),(@{body=$body;mac=$mac}|ConvertTo-Json -Compress),[Text.UTF8Encoding]::new($false))
[IO.File]::WriteAllText((Join-Path $Scope 'pair-ready.json'),(@{broker=$broker;parent=$owner}|ConvertTo-Json -Compress),[Text.UTF8Encoding]::new($false))
$c=[Diagnostics.Stopwatch]::StartNew();while($c.Elapsed.TotalSeconds -lt 25){Start-Sleep -Milliseconds 20}
'@
[IO.File]::WriteAllText($parentScript,$parent,$utf8)
$manifest=Join-Path $scope 'package.json';[IO.File]::WriteAllText($manifest,'{"own_ipc_only":true}',$utf8)
$paths=[string[]](@($code)+@((Join-Path $tools 'GMGN-LegacyReady.ps1'),(Join-Path $tools 'GMGN-ReadSupervisor.ps1'),$manifest))
$sources=[GmgnImmutableSources]::new('%USERPROFILE%','C:\Program Files\nodejs\node.exe',$paths,[string[]]@($paths|ForEach-Object {(Get-FileHash $_).Hash.ToLowerInvariant()}))
$authDir=Join-Path (Join-Path $repo '.local-tools') ('gmgn-read-auth-'+[guid]::NewGuid().ToString('N'));$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$key=if($Case -cin @('WrongMac','FactoryWrongMac')){'e'*64}else{'f'*64}
$auth=[GmgnReadLocalAuth]::CreateNew($authDir,$sid,[Text.Encoding]::ASCII.GetBytes($key))
$env:IMC_GMGN_IPC_TOKEN='f'*64 # Deliberately correct fallback bait for every negative case.
$job=[IMCRuntimeJob]::new();$lifetime=$null;$capture=$null;$retired=$null;$errorCode='';$expected='';$stopped=$false;$cleanup=$false
function Cause($record){$e=$record.Exception;while($e.InnerException){$e=$e.InnerException};$e.Message}
try{
 $parentPid=$job.Start((Join-Path $PSHOME 'powershell.exe'),[string[]]@('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',$parentScript,'-Scope',$scope,'-Tools',$tools),$scope);$job.ObserveMember($parentPid)
 $c=[Diagnostics.Stopwatch]::StartNew();while(-not [IO.File]::Exists((Join-Path $scope 'pair-ready.json')) -and $c.Elapsed.TotalSeconds -lt 8){Start-Sleep -Milliseconds 10}
 if(-not [IO.File]::Exists((Join-Path $scope 'pair-ready.json'))){throw 'OWN_OPAQUE_PAIR_TIMEOUT'}
 $ready=Get-Content (Join-Path $scope 'pair-ready.json') -Raw|ConvertFrom-Json;$job.ObserveMember([int]$ready.broker[0])
 $lifetime=[GmgnLegacyLifetimePins]::new([int]$ready.broker[0],$ready.broker[2],[int]$ready.parent[0],$ready.parent[2],$ready.parent[3])
 $capture=[GmgnLegacyCapture]::new($legacy,$capturePath,[string[]]@('broker.ready'),60,$lifetime)
 $p=Join-Path $tools 'GMGN-LegacyReady.ps1';$t=$null;$e=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($sources.ReadText($p),$p,[ref]$t,[ref]$e);if($e.Count){throw 'OWN_OPAQUE_PARSE'};. ($ast.GetScriptBlock())
 $selected=$auth
 switch($Case){'Null'{$selected=$null};'FactoryNull'{$selected=$null};'False'{$selected=$false};'Empty'{$selected=''};
  'Faulted'{$auth.Abort()};'FactoryFaulted'{$auth.Abort()};'WrongEnvironment'{$env:IMC_GMGN_IPC_TOKEN='e'*64};'Normal'{$env:IMC_GMGN_IPC_TOKEN=$null};
  'ForeignType'{
   $compiler=[Microsoft.CSharp.CSharpCodeProvider]::new();$parameters=[CodeDom.Compiler.CompilerParameters]::new();$parameters.GenerateInMemory=$true;$null=$parameters.ReferencedAssemblies.Add('System.dll')
   try{$assembly=$compiler.CompileAssemblyFromSource($parameters,[string[]]@('public sealed class GmgnReadLocalAuth { public void Check(){} public bool Verify(string a,string b){return true;} }')).CompiledAssembly}finally{$compiler.Dispose()}
   $selected=[Activator]::CreateInstance($assembly.GetType('GmgnReadLocalAuth'))
  }
 }
 try{
  if($Case -clike 'Factory*' -or $Case -cin @('Normal','WrongEnvironment','RetireAuthFault','RetireNormal','RetireLateAuthFault','RetireOwnerDeadline')){
   $retired=[GmgnLegacyRetirement]::CreateAuthenticated($sources,$lifetime,$capture,$legacy,$scope,$selected)
   if($retired.Phase -cne 'ADMITTED'){throw 'OWN_OPAQUE_FACTORY_NOT_ADMITTED'}
   if($Case -clike 'Retire*'){
    $capture.Copy()
    if($Case -ceq 'RetireAuthFault'){$auth.Abort()}
    if($Case -ceq 'RetireLateAuthFault'){
     Add-Type -TypeDefinition 'using System;public static class OwnOpaqueRetireFault{public static Action Make(object auth){return delegate{auth.GetType().GetMethod("Abort").Invoke(auth,null);};}}'
     $retired.GetType().GetField('beforeAtomicRetireFixtureProbe',[Reflection.BindingFlags]'Instance,NonPublic').SetValue($retired,[OwnOpaqueRetireFault]::Make($auth))
    }
    if($Case -ceq 'RetireOwnerDeadline'){
     Add-Type -TypeDefinition 'using System;using System.Threading;public static class OwnOpaqueRetireDelay{public static bool Reached;public static Action Make(){return delegate{Reached=true;Thread.Sleep(16000);};}}'
     $retired.GetType().GetField('beforeAtomicRetireFixtureProbe',[Reflection.BindingFlags]'Instance,NonPublic').SetValue($retired,[OwnOpaqueRetireDelay]::Make())
    }
    $retired.Retire([Diagnostics.Stopwatch]::GetTimestamp(),[Diagnostics.Stopwatch]::Frequency)
   }
  }else{
   $arguments=@{Capture=$capture;Lifetime=$lifetime;Directory=$legacy;FixtureScope=$scope}
   if($Case -cne 'LegacyAbsent'){$arguments.Authentication=$selected}
   $instance=Assert-GmgnCapturedLegacyReady @arguments
   if($instance -cne ('d'*32)){throw 'OWN_OPAQUE_ROLE_RESULT'}
  }
 }catch{$errorCode=Cause $_}
 $expected=switch($Case){'Null'{'GMGN_CAPTURE_READY_AUTH_TYPE'};'False'{'GMGN_CAPTURE_READY_AUTH_TYPE'};'Empty'{'GMGN_CAPTURE_READY_AUTH_TYPE'};'ForeignType'{'GMGN_CAPTURE_READY_AUTH_TYPE'};
  'WrongMac'{'GMGN_CAPTURE_READY_AUTH'};'Faulted'{'GMGN_AUTH_FAULT'};'FactoryNull'{'GMGN_LEGACY_AUTH_CONTEXT'};'FactoryWrongMac'{'GMGN_LEGACY_RETIRE_ROLE_INVALID'};
  'FactoryFaulted'{'GMGN_AUTH_FAULT'};'RetireAuthFault'{'GMGN_AUTH_FAULT'};'RetireLateAuthFault'{'GMGN_AUTH_FAULT'};'RetireOwnerDeadline'{'GMGN_LEGACY_OWNER_PROOF_EXPIRED'};default{''}}
 if($errorCode -cne $expected){throw ('OWN_OPAQUE_WRONG_ERROR_'+$errorCode)}
 if($Case -ceq 'RetireNormal'){
  if(-not $lifetime.ObserveOriginalExits()[0] -or -not $lifetime.Retired -or $retired.Phase -cne 'RETIRED'){throw 'OWN_OPAQUE_RETIRE_NOT_CONFIRMED'}
 }elseif(($lifetime.ObserveOriginalExits() -contains $true) -or $lifetime.Retired){throw 'OWN_OPAQUE_UNEXPECTED_RETIREMENT'}
 if($Case -cin @('RetireAuthFault','RetireLateAuthFault','RetireOwnerDeadline') -and $retired.Phase -cne 'FAULT'){throw 'OWN_OPAQUE_RETIRE_FAULT_RESET'}
 if($Case -ceq 'RetireOwnerDeadline'){
  if(-not [OwnOpaqueRetireDelay]::Reached){throw 'OWN_OPAQUE_DEADLINE_PROBE_NOT_REACHED'}
  $again='';try{$retired.Retire([Diagnostics.Stopwatch]::GetTimestamp(),[Diagnostics.Stopwatch]::Frequency)}catch{$again=Cause $_}
  if($again -cne 'GMGN_LEGACY_RETIRE_USED' -or ($lifetime.ObserveOriginalExits() -contains $true)){throw 'OWN_OPAQUE_DEADLINE_RETRY_OR_EFFECT'}
 }
}finally{
 $stopped=$true;$job.ShutdownWithin(10000);$cleanup=$job.CleanupConfirmed
 if(-not $cleanup){throw 'OWN_OPAQUE_JOB_CLEANUP'}
}
[pscustomobject]@{pass=$true;case=$Case;code=$errorCode;native_job_cleanup_confirmed=$cleanup;provider_calls=0;retirement_performed=($Case -ceq 'RetireNormal');production_verified=$false}|ConvertTo-Json -Compress
