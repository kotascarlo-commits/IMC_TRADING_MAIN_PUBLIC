param([ValidateSet('WrongAnchorHash','WrongSchema','WrongSourceHash','UnlistedSource','PreloadedType')][string]$Case='WrongAnchorHash')
$ErrorActionPreference='Stop';$repo=Split-Path -Parent $PSScriptRoot;$tools=Join-Path $repo 'TOOLS'
$scope=Join-Path (Join-Path $repo '.local-tools') ('advisor-client-own-'+[guid]::NewGuid().ToString('N'));$null=[IO.Directory]::CreateDirectory($scope)
$paths=@('GmgnRepairNamespace.cs','GmgnImmutableSources.cs','GmgnReadDirectoryIdentity.cs','GmgnScheduledActionIdentity.cs',
 'ImcAdvisorHostContext.cs','IMC-AdvisorBroker.ps1','IMC-AdvisorTaskBootstrap.ps1','GMGN-SupervisorTask.ps1')|ForEach-Object {Join-Path $tools $_}
$paths+=Join-Path $repo 'config\imc-advisor.json'
$paths+='%USERPROFILE%\AppData\Roaming\npm\node_modules\@openai\codex\node_modules\@openai\codex-win32-x64\vendor\x86_64-pc-windows-msvc\bin\codex.exe'
$sources=@($paths|ForEach-Object {@{path=$_;sha256=(Get-FileHash -LiteralPath $_).Hash.ToLowerInvariant()}})
$anchor=@{schema_version=1;task_name='IMC-AdvisorReadOnly';task_arguments='-NoProfile';owner_sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value;
 executable='C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe';directory_id=('a'*16+':'+('b'*32));
 broker_dir=(Join-Path $env:LOCALAPPDATA ('Temp\imc-astra-independent-'+[guid]::NewGuid().ToString('N')));
 sources=$sources}
switch($Case){
 'WrongSchema'{$anchor.extra=$true}
 'WrongSourceHash'{$sources[0].sha256='0'*64}
 'UnlistedSource'{$sources[0].path=Join-Path $tools 'GMGN-ReadProtocol.ps1'}
 'PreloadedType'{Add-Type 'public sealed class GmgnRepairNamespace { public void Dummy(){} }'}
}
$path=Join-Path $scope 'own-anchor.json';[IO.File]::WriteAllText($path,($anchor|ConvertTo-Json -Depth 8 -Compress),[Text.UTF8Encoding]::new($false))
$hash=(Get-FileHash -LiteralPath $path).Hash.ToLowerInvariant();if($Case -ceq 'WrongAnchorHash'){$hash='0'*64}
$stream=[IO.FileStream]::new($path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read);$code=''
try{$null=& (Join-Path $tools 'IMC-AdvisorClientBootstrap.ps1') -AnchorStream $stream -AnchorHash $hash}catch{
 $e=$_.Exception;while($e.InnerException){$e=$e.InnerException};$code=$e.Message
}
$expected=switch($Case){'WrongAnchorHash'{'ASTRA_CLIENT_ANCHOR_CHANGED'};'WrongSchema'{'ASTRA_CLIENT_ANCHOR_SCHEMA'};
 'WrongSourceHash'{'ASTRA_CLIENT_SOURCE_CHANGED'};'UnlistedSource'{'ASTRA_CLIENT_SOURCE_INVENTORY'};'PreloadedType'{'ASTRA_CLIENT_PRELOADED_TYPE'}}
if($code -cne $expected){throw ('OWN_CLIENT_WRONG_ERROR_'+$code)}
[pscustomobject]@{case=$Case;pass=$true;code=$code;provider_calls=0;production_verified=$false}|ConvertTo-Json -Compress
