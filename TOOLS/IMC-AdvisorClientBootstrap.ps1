# Invoker holds and verifies these bytes before execution. AnchorHash is a
# fixed external deployment value, not a field supplied by the locator.
param([Parameter(Mandatory=$true)][IO.FileStream]$AnchorStream,
 [Parameter(Mandatory=$true)][string]$AnchorHash)
$ErrorActionPreference='Stop';$repo='%USERPROFILE%\Desktop\IMC_TRADING_MAIN';$tools=Join-Path $repo 'TOOLS'
if($AnchorHash -cnotmatch '\A[0-9a-f]{64}\z' -or $AnchorStream.Length -gt 65536){throw 'ASTRA_CLIENT_ANCHOR_UNTRUSTED'}
$sha=[Security.Cryptography.SHA256]::Create();$AnchorStream.Position=0
try{$actual=([BitConverter]::ToString($sha.ComputeHash($AnchorStream))).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
if($actual -cne $AnchorHash){throw 'ASTRA_CLIENT_ANCHOR_CHANGED'}
$AnchorStream.Position=0;$reader=[IO.StreamReader]::new($AnchorStream,[Text.UTF8Encoding]::new($false,$true),$true,4096,$true)
try{$anchor=$reader.ReadToEnd()|ConvertFrom-Json -ErrorAction Stop}finally{$reader.Dispose()}
$ownPlan=$AnchorStream.Name -cmatch '\AC:\\Users\\<local-user>\\Desktop\\IMC_TRADING_MAIN\\[.]local-tools\\advisor-client-own-[0-9a-f]{32}\\own-plan[.]json\z'
if($anchor.task_name -cne 'IMC-AdvisorReadOnly' -and -not ($ownPlan -and $anchor.task_name -cmatch '\AIMC-READ-FIXTURE-[0-9a-f]{32}\z')){throw 'ASTRA_CLIENT_ANCHOR_SCHEMA'}
if(($anchor.PSObject.Properties.Name|Sort-Object) -join ',' -cne 'broker_dir,directory_id,executable,owner_sid,schema_version,sources,task_arguments,task_name' -or
 $anchor.schema_version -cne 1 -or
 $anchor.broker_dir -cnotmatch '\AC:\\Users\\<local-user>\\AppData\\Local\\Temp\\imc-astra-independent-[0-9a-f]{32}\z' -or
 $anchor.directory_id -cnotmatch '\A[0-9a-f]{16}:[0-9a-f]{32}\z' -or
 $anchor.executable -cne 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' -or
 $anchor.owner_sid -cne [Security.Principal.WindowsIdentity]::GetCurrent().User.Value -or
 $anchor.task_arguments -isnot [string] -or $anchor.sources.Count -ne 10){throw 'ASTRA_CLIENT_ANCHOR_SCHEMA'}
$required=@('GmgnRepairNamespace.cs','GmgnImmutableSources.cs','GmgnReadDirectoryIdentity.cs','GmgnScheduledActionIdentity.cs',
 'ImcAdvisorHostContext.cs','IMC-AdvisorBroker.ps1','IMC-AdvisorTaskBootstrap.ps1','GMGN-SupervisorTask.ps1')|ForEach-Object {Join-Path $tools $_}
$required+=Join-Path $repo 'config\imc-advisor.json'
$required+='%USERPROFILE%\AppData\Roaming\npm\node_modules\@openai\codex\node_modules\@openai\codex-win32-x64\vendor\x86_64-pc-windows-msvc\bin\codex.exe'
$held=[Collections.Generic.List[IO.FileStream]]::new();$text=@{};$paths=[Collections.Generic.List[string]]::new();$hashes=[Collections.Generic.List[string]]::new()
foreach($file in $anchor.sources){
 if($file.path -cnotin $required -or $text.ContainsKey($file.path) -or $file.sha256 -cnotmatch '\A[0-9a-f]{64}\z'){throw 'ASTRA_CLIENT_SOURCE_INVENTORY'}
 $stream=[IO.FileStream]::new($file.path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read);$held.Add($stream)
 $sha=[Security.Cryptography.SHA256]::Create();try{$hash=([BitConverter]::ToString($sha.ComputeHash($stream))).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
 if($hash -cne $file.sha256){throw 'ASTRA_CLIENT_SOURCE_CHANGED'}
 if([IO.Path]::GetExtension($file.path) -cne '.exe'){
  if($stream.Length -gt 2097152){throw 'ASTRA_CLIENT_SOURCE_SIZE'};$stream.Position=0
  $reader=[IO.StreamReader]::new($stream,[Text.UTF8Encoding]::new($false,$true),$true,4096,$true)
  try{$text[$file.path]=$reader.ReadToEnd()}finally{$reader.Dispose()}
 }else{$text[$file.path]=$null}
 $paths.Add($file.path);$hashes.Add($file.sha256)
}
$types=@('GmgnRepairNamespace','GmgnImmutableSources','GmgnReadDirectoryIdentity','GmgnScheduledActionIdentity','ImcAdvisorHostContext')
foreach($type in @($types)+@('ImcAdvisorDirectory')){if($type -as [type]){throw 'ASTRA_CLIENT_PRELOADED_TYPE'}}
$compiler=[Microsoft.CSharp.CSharpCodeProvider]::new();$parameters=[CodeDom.Compiler.CompilerParameters]::new();$parameters.GenerateInMemory=$true
foreach($reference in @('System.dll','System.Core.dll')){$null=$parameters.ReferencedAssemblies.Add($reference)}
try{$result=$compiler.CompileAssemblyFromSource($parameters,[string[]]@($types|ForEach-Object {$text[(Join-Path $tools ($_+'.cs'))]}))}finally{$compiler.Dispose()}
if($result.Errors.HasErrors){throw 'ASTRA_CLIENT_COMPILE'}
foreach($type in @($types)+@('ImcAdvisorDirectory')){if(-not [object]::ReferenceEquals(($type -as [type]).Assembly,$result.CompiledAssembly)){throw 'ASTRA_CLIENT_ASSEMBLY'}}
$sources=[GmgnImmutableSources]::CreateAdvisorPins($paths.ToArray(),$hashes.ToArray())
$directory=[GmgnReadDirectoryIdentity]::new($anchor.broker_dir)
$protectedDirectory=[ImcAdvisorDirectory]::new($directory)
$bindingPath=Join-Path $anchor.broker_dir 'host.binding.json'
$bindingStream=[IO.FileStream]::new($bindingPath,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read);$held.Add($bindingStream)
if($bindingStream.Length -le 0 -or $bindingStream.Length -gt 16384 -or ([IO.File]::GetAttributes($bindingPath) -band [IO.FileAttributes]::ReparsePoint)){throw 'ASTRA_CLIENT_BINDING_SIZE_OR_PATH'}
$reader=[IO.StreamReader]::new($bindingStream,[Text.UTF8Encoding]::new($false,$true),$true,4096,$true)
try{$hint=$reader.ReadToEnd()|ConvertFrom-Json -ErrorAction Stop}finally{$reader.Dispose()}
if(($hint.PSObject.Properties.Name|Sort-Object) -join ',' -cne 'directory_id,generation,native_self,owner_sid,schema_version' -or
 $hint.schema_version -cne 1 -or $hint.native_self.Count -ne 4 -or $hint.owner_sid -cne $anchor.owner_sid -or
 $hint.directory_id -cne $anchor.directory_id -or $hint.generation -cne $sources.Fingerprint()){throw 'ASTRA_CLIENT_BINDING_SCHEMA'}
foreach($item in $hint.native_self){if($item -isnot [string] -or -not $item){throw 'ASTRA_CLIENT_BINDING_NATIVE_SHAPE'}}
# The hint never chooses its own expected executable or arguments.
if($hint.native_self[3] -cne $anchor.executable){throw 'ASTRA_CLIENT_EXECUTABLE_MISMATCH'}
$peer=[GmgnScheduledActionIdentity]::BindExpectedPeerAction([string[]]$hint.native_self,$anchor.owner_sid,$directory,$anchor.directory_id,$anchor.task_arguments)
. ([scriptblock]::Create($sources.ReadText((Join-Path $tools 'GMGN-SupervisorTask.ps1'))))
$service=New-Object -ComObject Schedule.Service;$service.Connect();$task=$service.GetFolder('\').GetTask($anchor.task_name)
$binding=[pscustomobject]@{BrokerDir=$anchor.broker_dir;Sources=$sources;Directory=$directory;Peer=$peer;Anchor=$anchor;Task=$task;Held=$held;AnchorStream=$AnchorStream;Check=$null}
$binding.Check={
 $sources.Check();$directory.Check();$protectedDirectory.Check();$peer.Check()
 . ([scriptblock]::Create($sources.ReadText((Join-Path $repo 'TOOLS\GMGN-SupervisorTask.ps1'))))
 Assert-GmgnSupervisorTaskDefinition $task.Definition $anchor.owner_sid ([IO.Path]::Combine($PSHOME,'powershell.exe')) $anchor.task_arguments $repo
 # The fixed plan originates from reviewed Root deployment and scheduler
 # binding. GetInstances is never an absence or exit proof.
 $peer.Check();$sources.Check()
}.GetNewClosure()
& $binding.Check
return $binding
