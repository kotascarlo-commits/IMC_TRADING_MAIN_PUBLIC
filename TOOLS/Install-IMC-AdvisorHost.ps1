# Root-only read host installation. Never promotes the client or retires the
# old broker. Promotion requires a verified response from this new host.
[CmdletBinding(DefaultParameterSetName='Prepare')]
param([Parameter(ParameterSetName='Prepare')][switch]$Prepare,
 [Parameter(Mandatory=$true,ParameterSetName='Start')][string]$PlanPath,
 [Parameter(Mandatory=$true,ParameterSetName='Start')][string]$PlanHash)
$ErrorActionPreference='Stop';$repo='%USERPROFILE%\Desktop\IMC_TRADING_MAIN';$tools=Join-Path $repo 'TOOLS'
$held=[Collections.Generic.List[IO.FileStream]]::new();$text=@{};$files=@()
function ReadHeldSource([string]$Path,[string]$Expected){
 $s=[IO.FileStream]::new($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read);$held.Add($s)
 $sha=[Security.Cryptography.SHA256]::Create();try{$hash=([BitConverter]::ToString($sha.ComputeHash($s))).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
 if($Expected -and $hash -cne $Expected){throw 'ASTRA_INSTALL_SOURCE_CHANGED'}
 if([IO.Path]::GetExtension($Path) -cne '.exe'){
  if($s.Length -gt 2097152){throw 'ASTRA_INSTALL_SOURCE_SIZE'};$s.Position=0
  $r=[IO.StreamReader]::new($s,[Text.UTF8Encoding]::new($false,$true),$true,4096,$true);try{$text[$Path]=$r.ReadToEnd()}finally{$r.Dispose()}
 }
 return @{path=$Path;sha256=$hash}
}
$names=@('GmgnRepairNamespace.cs','GmgnImmutableSources.cs','GmgnReadDirectoryIdentity.cs','GmgnScheduledActionIdentity.cs',
 'ImcAdvisorHostContext.cs','IMC-AdvisorBroker.ps1','IMC-AdvisorTaskBootstrap.ps1','GMGN-SupervisorTask.ps1')
$required=@($names|ForEach-Object {Join-Path $tools $_})
$required+=Join-Path $repo 'config\imc-advisor.json'
$required+='%USERPROFILE%\AppData\Roaming\npm\node_modules\@openai\codex\node_modules\@openai\codex-win32-x64\vendor\x86_64-pc-windows-msvc\bin\codex.exe'
if($PSCmdlet.ParameterSetName -ceq 'Start'){
 if($PlanHash -cnotmatch '\A[0-9a-f]{64}\z' -or [IO.Path]::GetFullPath($PlanPath) -cne $PlanPath){throw 'ASTRA_INSTALL_PLAN_INPUT'}
 $null=ReadHeldSource $PlanPath $PlanHash;$plan=$text[$PlanPath]|ConvertFrom-Json -ErrorAction Stop
 if($plan.schema_version -cne 1 -or $plan.task_name -cne 'IMC-AdvisorReadOnly' -or $plan.sources.Count -ne 10 -or
  $plan.owner_sid -cne [Security.Principal.WindowsIdentity]::GetCurrent().User.Value){throw 'ASTRA_INSTALL_PLAN_SCHEMA'}
 foreach($entry in $plan.sources){
  if($entry.path -cnotin $required -or $entry.sha256 -cnotmatch '\A[0-9a-f]{64}\z'){throw 'ASTRA_INSTALL_PLAN_CLOSURE'}
  $files+=ReadHeldSource $entry.path $entry.sha256
 }
}else{foreach($path in $required){$files+=ReadHeldSource $path ''}}
$types=@('GmgnRepairNamespace','GmgnImmutableSources','GmgnReadDirectoryIdentity','GmgnScheduledActionIdentity','ImcAdvisorHostContext')
foreach($type in @($types)+@('ImcAdvisorDirectory')){if($type -as [type]){throw 'ASTRA_INSTALL_PRELOADED_TYPE'}}
$compiler=[Microsoft.CSharp.CSharpCodeProvider]::new();$parameters=[CodeDom.Compiler.CompilerParameters]::new();$parameters.GenerateInMemory=$true
foreach($reference in @('System.dll','System.Core.dll')){$null=$parameters.ReferencedAssemblies.Add($reference)}
try{$compiled=$compiler.CompileAssemblyFromSource($parameters,[string[]]@($types|ForEach-Object {$text[(Join-Path $tools ($_+'.cs'))]}))}finally{$compiler.Dispose()}
if($compiled.Errors.HasErrors){throw 'ASTRA_INSTALL_COMPILE'}
foreach($type in @($types)+@('ImcAdvisorDirectory')){if(-not [object]::ReferenceEquals(($type -as [type]).Assembly,$compiled.CompiledAssembly)){throw 'ASTRA_INSTALL_ASSEMBLY'}}
$pins=[GmgnImmutableSources]::CreateAdvisorPins([string[]]@($files.path),[string[]]@($files.sha256));$pins.Check()
. ([scriptblock]::Create($pins.ReadText((Join-Path $tools 'GMGN-SupervisorTask.ps1'))))
$owner=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value;$exe=Join-Path $PSHOME 'powershell.exe'
if($PSCmdlet.ParameterSetName -ceq 'Prepare'){
 $dir=Join-Path $env:LOCALAPPDATA ('Temp\imc-astra-independent-'+[guid]::NewGuid().ToString('N'))
 $directory=[ImcAdvisorDirectory]::CreateNew($dir);$protected=[ImcAdvisorDirectory]::new($directory)
 $body=@'
$ErrorActionPreference='Stop'
$actionText=$MyInvocation.MyCommand.ScriptBlock.ToString()
$expected='-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -EncodedCommand '+[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($actionText))
$files=ConvertFrom-Json $filesJson
$bootstrap=@($files|Where-Object {$_.path.EndsWith('\IMC-AdvisorTaskBootstrap.ps1')})[0]
$stream=[IO.FileStream]::new($bootstrap.path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
$sha=[Security.Cryptography.SHA256]::Create();try{$hash=([BitConverter]::ToString($sha.ComputeHash($stream))).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
if($hash -cne $bootstrap.sha256){throw 'ASTRA_TASK_BOOTSTRAP_CHANGED'}
$stream.Position=0;$reader=[IO.StreamReader]::new($stream,[Text.UTF8Encoding]::new($false,$true),$true,4096,$true)
try{$text=$reader.ReadToEnd()}finally{$reader.Dispose()}
& ([scriptblock]::Create($text)) -ExpectedFiles $files -BrokerDir $brokerDir -TaskName 'IMC-AdvisorReadOnly' -ExpectedArguments $expected
'@
 $prefix='$filesJson = '''+($files|ConvertTo-Json -Compress)+"'`n"+'$brokerDir = '''+$dir+"'`n"
 $arguments='-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -EncodedCommand '+[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($prefix+$body))
 $plan=@{schema_version=1;task_name='IMC-AdvisorReadOnly';task_arguments=$arguments;executable=$exe;
  broker_dir=$dir;directory_id=$directory.Identity;owner_sid=$owner;sources=$files}
 $PlanPath=Join-Path (Join-Path $repo 'RUNTIME') ('imc-advisor-plan-'+[guid]::NewGuid().ToString('N')+'.json')
 $bytes=[Text.UTF8Encoding]::new($false).GetBytes(($plan|ConvertTo-Json -Depth 8 -Compress))
 $writer=[IO.FileStream]::new($PlanPath,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
 try{$writer.Write($bytes,0,$bytes.Length);$writer.Flush($true)}finally{$writer.Dispose()}
 $sha=[Security.Cryptography.SHA256]::Create();try{$PlanHash=([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
 $null=ReadHeldSource $PlanPath $PlanHash;$protected.Check();$pins.Check()
 [pscustomobject]@{phase='PREPARED';plan_path=$PlanPath;plan_sha256=$PlanHash;broker_dir=$dir;task_started=$false}|ConvertTo-Json -Compress
 return
}
$directory=[GmgnReadDirectoryIdentity]::new($plan.broker_dir);$protected=[ImcAdvisorDirectory]::new($directory)
if($directory.Identity -cne $plan.directory_id -or $plan.executable -cne $exe){throw 'ASTRA_INSTALL_PLAN_BINDING'}
$service=New-Object -ComObject Schedule.Service;$service.Connect();$folder=$service.GetFolder('\')
$definition=New-GmgnSupervisorTaskDefinition $service $owner $exe $plan.task_arguments $repo
$task=Register-GmgnSupervisorTask $folder $plan.task_name $definition $owner $exe $plan.task_arguments $repo
$earliest=[DateTime]::UtcNow.AddSeconds(-1).ToString('o')
$instance=Start-GmgnSupervisorTask $task $owner $exe $plan.task_arguments $repo;$guid=$instance.InstanceGuid
$clock=[Diagnostics.Stopwatch]::StartNew()
do{$instance.Refresh();$engine=$instance.EnginePID;if($engine -gt 0){break};Start-Sleep -Milliseconds 20}while($clock.Elapsed.TotalSeconds -lt 10)
$original=Bind-GmgnSupervisorTaskAction $instance $task $guid $engine $owner $exe $plan.task_arguments $repo $earliest
$bindingPath=Join-Path $plan.broker_dir 'host.binding.json';$readyPath=Join-Path $plan.broker_dir 'broker.ready'
while(( -not [IO.File]::Exists($bindingPath) -or -not [IO.File]::Exists($readyPath)) -and $clock.Elapsed.TotalSeconds -lt 30){
 $pins.Check();$protected.Check();$original.Check();Start-Sleep -Milliseconds 50
}
$binding=[IO.File]::ReadAllText($bindingPath)|ConvertFrom-Json -ErrorAction Stop
if(-not [IO.File]::Exists($readyPath) -or ($binding.native_self -join '|') -cne ($original.ReadNativeBinding() -join '|') -or
 $binding.owner_sid -cne $owner -or $binding.directory_id -cne $directory.Identity -or $binding.generation -cne $pins.Fingerprint()){throw 'ASTRA_INSTALL_HOST_BINDING'}
$instance.Refresh()
if($instance.InstanceGuid -cne $guid -or $instance.EnginePID -ne $engine){throw 'ASTRA_INSTALL_ORIGINAL_INSTANCE_CHANGED'}
Assert-GmgnSupervisorTaskDefinition $task.Definition $owner $exe $plan.task_arguments $repo
$original.Check();$pins.Check();$protected.Check()
[pscustomobject]@{phase='HOST_BOUND';plan_path=$PlanPath;plan_sha256=$PlanHash;broker_dir=$plan.broker_dir;
 native_self=$original.ReadNativeBinding();instance_guid=$guid;client_promoted=$false;old_broker_retired=$false}|ConvertTo-Json -Compress
