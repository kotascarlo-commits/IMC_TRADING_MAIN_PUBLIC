param([ValidateSet('AllowedConfig','AllowedBroker','TradingConfig','Node','OtherSource','HashMismatch','WriteBlocked','TradingRejectAdvisor','WrongDomain')][string]$Case='AllowedConfig')
$ErrorActionPreference='Stop';$repo=Split-Path -Parent $PSScriptRoot;$tools=Join-Path $repo 'TOOLS'
Add-Type -Path @('GmgnRepairNamespace.cs','GmgnImmutableSources.cs','GmgnReadDirectoryIdentity.cs','GmgnScheduledActionIdentity.cs','ImcAdvisorHostContext.cs'|ForEach-Object {Join-Path $tools $_})
$path=Join-Path $repo 'config/imc-advisor.json';$code='';$pins=$null
switch($Case){
 'AllowedBroker'{$path=Join-Path $tools 'IMC-AdvisorBroker.ps1'}
 'TradingConfig'{$path=Join-Path $repo 'config/imc-execution.json'}
 'Node'{$path='C:\Program Files\nodejs\node.exe'}
 'OtherSource'{$path=Join-Path $tools 'GMGN-ReadProtocol.ps1'}
}
$hash=(Get-FileHash -LiteralPath $path).Hash.ToLowerInvariant()
if($Case -ceq 'HashMismatch'){$hash='0'*64}
try{
 if($Case -ceq 'TradingRejectAdvisor'){
  $pins=[GmgnImmutableSources]::new('%USERPROFILE%','C:\Program Files\nodejs\node.exe',[string[]]@($path),[string[]]@($hash))
 }elseif($Case -ceq 'WrongDomain'){
  $public=Join-Path $repo 'config/imc-execution.json'
  $pins=[GmgnImmutableSources]::new('%USERPROFILE%','C:\Program Files\nodejs\node.exe',[string[]]@($public),[string[]]@((Get-FileHash $public).Hash.ToLowerInvariant()))
  $null=[ImcAdvisorHostContext]::new($pins,$null,$null,[string[]]@(),[string[]]@())
 }else{$pins=[GmgnImmutableSources]::CreateAdvisorPins([string[]]@($path),[string[]]@($hash))}
}catch{$e=$_.Exception;while($e.InnerException){$e=$e.InnerException};$code=$e.Message}
if($Case -cin @('AllowedConfig','AllowedBroker','WriteBlocked')){
 if(-not $pins.IsAdvisorDomain -or -not $pins.ReadText($path)){throw 'OWN_ADVISOR_DOMAIN_NOT_ADMITTED'}
 if($Case -ceq 'WriteBlocked'){
  $writer=$null;try{$writer=[IO.File]::Open($path,[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,[IO.FileShare]::ReadWrite)}catch{
   $e=$_.Exception;while($e.InnerException){$e=$e.InnerException};if(($e.HResult -band 65535) -ne 32){throw};$code='SHARING_VIOLATION'
  };if($writer){$writer.Dispose();throw 'OWN_ADVISOR_PIN_WRITE_ALLOWED'}
  if($code -cne 'SHARING_VIOLATION'){throw 'OWN_ADVISOR_PIN_NO_BLOCK'};$pins.Check()
 }
}else{
 $expected=switch($Case){'HashMismatch'{'GMGN_SOURCE_GENERATION_CHANGED'};'WrongDomain'{'ASTRA_CONTEXT_TYPES'};default{'GMGN_SOURCE_PATH_OR_HASH'}}
 if($code -cne $expected){throw ('OWN_ADVISOR_DOMAIN_WRONG_ERROR_'+$code)}
}
[pscustomobject]@{case=$Case;pass=$true;code=$code;provider_calls=0;production_verified=$false}|ConvertTo-Json -Compress
