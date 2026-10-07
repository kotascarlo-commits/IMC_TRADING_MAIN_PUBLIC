# Executed from verified bytes by the fixed encoded Task action. Never loads a
# manifest supplied by a running broker or reopens configuration for settings.
param([Parameter(Mandatory=$true)][object[]]$ExpectedFiles,
 [Parameter(Mandatory=$true)][string]$BrokerDir,
 [Parameter(Mandatory=$true)][string]$TaskName,
 [Parameter(Mandatory=$true)][string]$ExpectedArguments)
$ErrorActionPreference='Stop'
$project='%USERPROFILE%\Desktop\IMC_TRADING_MAIN';$tools=Join-Path $project 'TOOLS'
$ownFixture=$TaskName -cmatch '\AIMC-READ-FIXTURE-[0-9a-f]{32}\z'
if($TaskName -cne 'IMC-AdvisorReadOnly' -and -not $ownFixture){throw 'ASTRA_TASK_NAME'}
$held=[Collections.Generic.List[IO.FileStream]]::new();$bytes=@{};$paths=[Collections.Generic.List[string]]::new();$hashes=[Collections.Generic.List[string]]::new()
# These streams remain retained for this entire bootstrap invocation, including
# FAULT. Sources must be pinned before compilation or any helper execution.
foreach($file in $ExpectedFiles){
 if($file.path -isnot [string] -or $file.sha256 -cnotmatch '\A[0-9a-f]{64}\z' -or
  [IO.Path]::GetFullPath($file.path) -cne $file.path -or $bytes.ContainsKey($file.path)){throw 'ASTRA_BOOTSTRAP_INVENTORY'}
 $allowed=@('GmgnRepairNamespace.cs','GmgnImmutableSources.cs','GmgnReadDirectoryIdentity.cs','GmgnScheduledActionIdentity.cs',
  'ImcAdvisorHostContext.cs','IMC-AdvisorBroker.ps1','IMC-AdvisorTaskBootstrap.ps1','GMGN-SupervisorTask.ps1')|ForEach-Object {Join-Path $tools $_}
 $allowed+=Join-Path $project 'config\imc-advisor.json'
 $allowed+='%USERPROFILE%\AppData\Roaming\npm\node_modules\@openai\codex\node_modules\@openai\codex-win32-x64\vendor\x86_64-pc-windows-msvc\bin\codex.exe'
 if($file.path -cnotin $allowed){throw 'ASTRA_BOOTSTRAP_PATH'}
 $stream=[IO.FileStream]::new($file.path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read);$held.Add($stream)
 $sha=[Security.Cryptography.SHA256]::Create()
 try{$actual=([BitConverter]::ToString($sha.ComputeHash($stream))).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
 if($actual -cne $file.sha256){throw 'ASTRA_BOOTSTRAP_HASH'}
 if([IO.Path]::GetExtension($file.path) -cne '.exe'){
  if($stream.Length -gt 2097152){throw 'ASTRA_BOOTSTRAP_SIZE'}
  $stream.Position=0;$reader=[IO.StreamReader]::new($stream,[Text.UTF8Encoding]::new($false,$true),$true,4096,$true)
  try{$bytes[$file.path]=$reader.ReadToEnd()}finally{$reader.Dispose()}
 }else{$bytes[$file.path]=$null}
 $paths.Add($file.path);$hashes.Add($file.sha256)
}
if($paths.Count -ne 10){throw 'ASTRA_BOOTSTRAP_CLOSURE'}
$nativeNames=@('GmgnRepairNamespace','GmgnImmutableSources','GmgnReadDirectoryIdentity','GmgnScheduledActionIdentity','ImcAdvisorHostContext')
foreach($name in @($nativeNames)+@('ImcAdvisorDirectory')){if($name -as [type]){throw 'ASTRA_BOOTSTRAP_PRELOADED_TYPE'}}
$compiler=[Microsoft.CSharp.CSharpCodeProvider]::new();$parameters=[CodeDom.Compiler.CompilerParameters]::new();$parameters.GenerateInMemory=$true
foreach($reference in @('System.dll','System.Core.dll')){$null=$parameters.ReferencedAssemblies.Add($reference)}
try{$result=$compiler.CompileAssemblyFromSource($parameters,[string[]]@($nativeNames|ForEach-Object {$bytes[(Join-Path $tools ($_+'.cs'))]}))}finally{$compiler.Dispose()}
if($result.Errors.HasErrors){throw 'ASTRA_BOOTSTRAP_COMPILE'}
foreach($name in @($nativeNames)+@('ImcAdvisorDirectory')){if(-not [object]::ReferenceEquals(($name -as [type]).Assembly,$result.CompiledAssembly)){throw 'ASTRA_BOOTSTRAP_ASSEMBLY'}}
$pins=[GmgnImmutableSources]::CreateAdvisorPins($paths.ToArray(),$hashes.ToArray());$pins.Check()
. ([scriptblock]::Create($pins.ReadText((Join-Path $tools 'GMGN-SupervisorTask.ps1'))))
$owner=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value;$exe=Join-Path $PSHOME 'powershell.exe'
$service=New-Object -ComObject Schedule.Service;$service.Connect();$task=$service.GetFolder('\').GetTask($TaskName)
Assert-GmgnSupervisorTaskDefinition $task.Definition $owner $exe $ExpectedArguments $project
$action=[GmgnScheduledActionIdentity]::BindCurrentAction($owner,$exe,$ExpectedArguments)
# Self identity and the fixed definition do not independently prove Task
# provenance. Root binds the original IRunningTask returned by Run and only
# publishes a client anchor after matching this complete native self binding.
Assert-GmgnSupervisorTaskDefinition $task.Definition $owner $exe $ExpectedArguments $project
$directory=[GmgnReadDirectoryIdentity]::new($BrokerDir)
$context=[ImcAdvisorHostContext]::new($pins,$action,$directory,$paths.ToArray(),$hashes.ToArray())
$settings=$context.ReadConfig()|ConvertFrom-Json -ErrorAction Stop
$context.Check()
[IO.File]::WriteAllText((Join-Path $BrokerDir 'host.binding.json'),(@{schema_version=1;native_self=$context.ReadNativeBinding();
 owner_sid=$owner;directory_id=$directory.Identity;generation=$pins.Fingerprint()}|ConvertTo-Json -Compress),[Text.UTF8Encoding]::new($false))
$context.Check()
if($ownFixture){
 $context.Check();$native=$context.ReadNativeBinding()
 [IO.File]::WriteAllText((Join-Path $BrokerDir 'own-host-proof.json'),(@{kind='OWN_ADVISOR_TASK';native_self=$native;
  directory_id=$directory.Identity;pass=$true;provider_calls=0;production_verified=$false}|ConvertTo-Json -Compress),[Text.UTF8Encoding]::new($false))
 $deadline=[Diagnostics.Stopwatch]::StartNew()
 while(-not [IO.File]::Exists((Join-Path $BrokerDir 'own-host-exit')) -and $deadline.Elapsed.TotalSeconds -lt 25){$context.Check();Start-Sleep -Milliseconds 20}
 return
}
& ([scriptblock]::Create($context.ReadBrokerSource())) -BrokerDir $BrokerDir -Project $project -Model $settings.model `
 -Reasoning $settings.reasoning_effort -RiskReasoning $settings.risk_reasoning_effort -HostContext $context
