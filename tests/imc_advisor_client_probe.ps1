param([Parameter(Mandatory=$true)][string]$PlanPath,[Parameter(Mandatory=$true)][string]$ExpectedHash,
 [Parameter(Mandatory=$true)][string]$ExpectedCode)
$ErrorActionPreference='Stop';$repo=Split-Path -Parent $PSScriptRoot;$binding=$null;$code=''
$stream=[IO.FileStream]::new($PlanPath,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
try{$binding=& (Join-Path $repo 'TOOLS\IMC-AdvisorClientBootstrap.ps1') -AnchorStream $stream -AnchorHash $ExpectedHash;& $binding.Check}catch{
 $e=$_.Exception;while($e.InnerException){$e=$e.InnerException};$code=$e.Message
}
if($ExpectedCode -ceq 'NONE'){$ExpectedCode=''}
if($code -cne $ExpectedCode){throw ('OWN_CLIENT_PROBE_WRONG_ERROR_'+$code)}
$native=if($binding){$binding.Peer.ReadNativeBinding()}else{@()}
[pscustomobject]@{pass=$true;code=$code;native_self=$native;provider_calls=0;production_verified=$false}|ConvertTo-Json -Compress
