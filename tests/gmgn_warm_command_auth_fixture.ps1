param([ValidateSet('Normal','WrongMac','MissingAuth','ForeignAuth','AuthFault','PlanFault')][string]$Case='Normal')
$ErrorActionPreference='Stop';$repo=Split-Path -Parent $PSScriptRoot;$tools=Join-Path $repo 'TOOLS'
Add-Type -AssemblyName System.Web.Extensions
$types=@('GmgnRepairNamespace','GmgnImmutableSources','GmgnReadDirectoryIdentity','GmgnScheduledActionIdentity','GmgnReadLocalAuth','GmgnReadInstallPlan')
Add-Type -Path @($types|ForEach-Object {Join-Path $tools ($_+'.cs')}) -ReferencedAssemblies @('System.dll','System.Core.dll','System.Web.Extensions.dll')
$scope=Join-Path (Join-Path $repo '.local-tools') ('warm-command-native-'+[guid]::NewGuid().ToString('N'));$null=[IO.Directory]::CreateDirectory($scope)
$sourcePath=Join-Path $scope 'package.json';[IO.File]::WriteAllText($sourcePath,'{"own_source_only":true}',[Text.UTF8Encoding]::new($false))
$commandPath=Join-Path $tools 'GMGN-ReadSupervisorCommands.ps1'
$paths=[string[]](@($types|ForEach-Object {Join-Path $tools ($_+'.cs')})+@($sourcePath,$commandPath))
$hashes=[string[]]@($paths|ForEach-Object {(Get-FileHash $_).Hash.ToLowerInvariant()})
$source=[GmgnImmutableSources]::new('%USERPROFILE%','C:\Program Files\nodejs\node.exe',$paths,$hashes)
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value;$id=[guid]::NewGuid().ToString('N')
$authPath=Join-Path (Join-Path $repo '.local-tools') ('gmgn-read-auth-'+[guid]::NewGuid().ToString('N'))
$auth=[GmgnReadLocalAuth]::CreateNew($authPath,$sid,[Text.Encoding]::ASCII.GetBytes('f'*64))
$recoveryPath=Join-Path $scope 'recovery';$null=[IO.Directory]::CreateDirectory($recoveryPath);$recovery=[GmgnReadDirectoryIdentity]::new($recoveryPath)
$sha=[Security.Cryptography.SHA256]::Create();try{$authHash=([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes(($auth.ReadPublicBinding() -join "`n"))))).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
$fields=[Collections.Generic.SortedDictionary[string,string]]::new([StringComparer]::Ordinal)
$values=@{schema_version='1';purpose='IMC_READ_INSTALL_PLAN_V1';installation_id=$id;source1_manifest=$sourcePath;source1_sha256=$source.HashForPath($sourcePath);source1_fingerprint=$source.Fingerprint();auth_binding_sha256=$authHash;recovery_directory=$recovery.Path;recovery_identity=$recovery.Identity;owner_sid=$sid;primary_plan=(Join-Path (Join-Path $scope 'primary') 'package.json');primary_plan_sha256=('a'*64)}
foreach($name in $values.Keys){$fields.Add($name,$values[$name])}
$planDir=Join-Path $scope 'plan';$null=[IO.Directory]::CreateDirectory($planDir);$planPath=Join-Path $planDir 'package.json'
$serializer=[Web.Script.Serialization.JavaScriptSerializer]::new();[IO.File]::WriteAllText($planPath,$serializer.Serialize($fields),[Text.UTF8Encoding]::new($false));$planHash=(Get-FileHash $planPath).Hash.ToLowerInvariant()
$planPins=[GmgnImmutableSources]::new('%USERPROFILE%','C:\Program Files\nodejs\node.exe',[string[]]@($planPath),[string[]]@($planHash))
$arguments='-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+$PSCommandPath+'" -Case '+$Case
$plan=[GmgnReadInstallPlan]::new($source,$planPins,$planPath,$planHash,$sourcePath,$source.HashForPath($sourcePath),$auth,$recovery,$id,$arguments)
$tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($source.ReadText($commandPath),$commandPath,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'OWN_COMMAND_SOURCE_PARSE'};. ($ast.GetScriptBlock())
$body='{"own_command":true}';$mac=$auth.Sign($body);if($Case -ceq 'WrongMac'){$mac='0'*64}
$packetPath=Join-Path $scope 'command.json';[IO.File]::WriteAllText($packetPath,(@{body=$body;mac=$mac}|ConvertTo-Json -Compress),[Text.UTF8Encoding]::new($false))
$stage=[pscustomobject]@{WarmMode=$true;Sources=$source;Authentication=$auth;InstallPlan=$plan;Streams=[Collections.Generic.List[object]]::new()}
switch($Case){'MissingAuth'{$stage.Authentication=$null};'ForeignAuth'{$stage.Authentication=[pscustomobject]@{fake=$true}};'AuthFault'{$auth.Abort()};'PlanFault'{$plan.Abort()}}
# Correct legacy environment bait must never rescue a rejected Warm contract.
$prior=$env:IMC_GMGN_IPC_TOKEN;$env:IMC_GMGN_IPC_TOKEN='f'*64;$code=''
try{try{$result=Read-GmgnStageAuthenticatedFile $stage $packetPath;if($result.Body.own_command -ne $true){throw 'OWN_COMMAND_BODY'}}catch{$e=$_.Exception;while($e.InnerException){$e=$e.InnerException};$code=$e.Message}}
finally{$env:IMC_GMGN_IPC_TOKEN=$prior;foreach($stream in $stage.Streams){$stream.Dispose()}}
$expected=switch($Case){'WrongMac'{'GMGN_STAGE_COMMAND_AUTH'};'MissingAuth'{'GMGN_STAGE_COMMAND_AUTH_UNAVAILABLE'};'ForeignAuth'{'GMGN_STAGE_COMMAND_AUTH_UNAVAILABLE'};'AuthFault'{'GMGN_AUTH_FAULT'};'PlanFault'{'GMGN_INSTALL_PLAN_FAULT'};default{''}}
if($code -cne $expected){throw ('OWN_COMMAND_UNEXPECTED_'+$code)}
[pscustomobject]@{pass=$true;case=$Case;code=$code;provider_calls=0;process_actions=0;production_verified=$false}|ConvertTo-Json -Compress
