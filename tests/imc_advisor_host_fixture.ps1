param([ValidateSet('Normal','MissingMember','WrongHash','SourceFault','ContextFault','WrongDirectory','AclMismatch','OldSelf')][string]$Case='Normal')
$ErrorActionPreference='Stop';$repo=Split-Path -Parent $PSScriptRoot;$tools=Join-Path $repo 'TOOLS'
Add-Type -Path @('GmgnRepairNamespace.cs','GmgnImmutableSources.cs','GmgnReadDirectoryIdentity.cs','GmgnScheduledActionIdentity.cs','ImcAdvisorHostContext.cs'|ForEach-Object {Join-Path $tools $_})
$names=@('GmgnRepairNamespace.cs','GmgnImmutableSources.cs','GmgnReadDirectoryIdentity.cs','GmgnScheduledActionIdentity.cs',
 'ImcAdvisorHostContext.cs','IMC-AdvisorBroker.ps1','IMC-AdvisorTaskBootstrap.ps1','GMGN-SupervisorTask.ps1')
$paths=[string[]]@(@($names|ForEach-Object {Join-Path $tools $_})+@(Join-Path $repo 'config\imc-advisor.json')+@([GmgnImmutableSources]::AdvisorExecutable))
$hashes=[string[]]@($paths|ForEach-Object {(Get-FileHash -LiteralPath $_).Hash.ToLowerInvariant()})
if($Case -ceq 'MissingMember'){$paths=$paths[0..8];$hashes=$hashes[0..8]}
$pins=[GmgnImmutableSources]::CreateAdvisorPins($paths,$hashes)
$expected=[string[]]$hashes.Clone();if($Case -ceq 'WrongHash'){$expected[0]='0'*64}
$arguments='-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+$PSCommandPath+'" -Case '+$Case
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
if($Case -ceq 'OldSelf'){
 Start-Sleep -Seconds 31
 $rejected='';try{$null=[GmgnScheduledActionIdentity]::new($PID,$sid,(Join-Path $PSHOME 'powershell.exe'),$arguments,[DateTime]::UtcNow.AddSeconds(-25).ToString('o'))}catch{
  $e=$_.Exception;while($e.InnerException){$e=$e.InnerException};$rejected=$e.Message
 };if($rejected -cne 'GMGN_ACTION_BIRTH_BEFORE_START'){throw 'OWN_OLD_SELF_EXTERNAL_WINDOW_CHANGED'}
 $action=[GmgnScheduledActionIdentity]::BindCurrentAction($sid,(Join-Path $PSHOME 'powershell.exe'),$arguments)
}else{$action=[GmgnScheduledActionIdentity]::new($PID,$sid,(Join-Path $PSHOME 'powershell.exe'),$arguments,[DateTime]::UtcNow.AddSeconds(-25).ToString('o'))}
$dir=Join-Path $env:LOCALAPPDATA ('Temp\imc-astra-independent-'+[guid]::NewGuid().ToString('N'))
if($Case -ceq 'WrongDirectory'){$dir=Join-Path (Join-Path $repo '.local-tools') ('advisor-host-own-'+[guid]::NewGuid().ToString('N'))}
if($Case -ceq 'WrongDirectory'){$null=[IO.Directory]::CreateDirectory($dir);$identity=[GmgnReadDirectoryIdentity]::new($dir)}else{$identity=[ImcAdvisorDirectory]::CreateNew($dir)}
$context=$null;$code=''
try{
 $context=[ImcAdvisorHostContext]::new($pins,$action,$identity,$paths,$expected)
 if($Case -ceq 'AclMismatch'){
  $acl=[IO.Directory]::GetAccessControl($dir);$acl.SetAccessRuleProtection($false,$true);[IO.Directory]::SetAccessControl($dir,$acl)
 }
 if($Case -ceq 'SourceFault'){$pins.Abort()};if($Case -ceq 'ContextFault'){$context.Abort()}
 $context.Check();$settings=$context.ReadConfig()|ConvertFrom-Json
 if($settings.model -cne 'gpt-6-astra' -or $context.Executable -cne [GmgnImmutableSources]::AdvisorExecutable){throw 'OWN_HOST_SETTINGS'}
}catch{$e=$_.Exception;while($e.InnerException){$e=$e.InnerException};$code=$e.Message}
$expectedCode=switch($Case){'Normal'{''};'OldSelf'{''};'AclMismatch'{'ASTRA_DIRECTORY_ACL'};'MissingMember'{'ASTRA_CONTEXT_CLOSURE'};'WrongHash'{'ASTRA_CONTEXT_GENERATION'};
 'SourceFault'{'GMGN_SOURCE_LOCKED'};'ContextFault'{'ASTRA_CONTEXT_FAULT'};'WrongDirectory'{'ASTRA_CONTEXT_DIRECTORY'}}
if($code -cne $expectedCode){throw ('OWN_HOST_WRONG_ERROR_'+$code)}
if($context -and $Case -cin @('SourceFault','ContextFault','AclMismatch')){
 $repeat='';try{$null=$context.ReadBrokerSource()}catch{$e=$_.Exception;while($e.InnerException){$e=$e.InnerException};$repeat=$e.Message}
 if($repeat -cne 'ASTRA_CONTEXT_FAULT' -or -not $context.Faulted){throw 'OWN_HOST_FAULT_NOT_PERSISTENT'}
}
[pscustomobject]@{case=$Case;pass=$true;code=$code;provider_calls=0;production_verified=$false}|ConvertTo-Json -Compress
