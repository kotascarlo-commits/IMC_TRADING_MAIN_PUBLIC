param(
  [Parameter(Position=0, ValueFromRemainingArguments=$true)][string[]]$Question,
  [switch]$RiskReview
)

$ErrorActionPreference = 'Stop'
$anchorPath=Join-Path $PSScriptRoot '..\RUNTIME\imc-advisor-host.json'
# Set only by reviewed deployment after binding the actual Task instance.
$expectedAnchorHash='8ac0d1afb17690febeece3a1e4dfbb017bc25099ede44db11bdd0c440faa124e'
$expectedBootstrapHash='8e6afea99b4669769b573edb02750488ca39742ae022f231069b54f4c4ff874f'
$binding=$null;$anchorStream=$null;$bootstrapStream=$null;$absent=$false
try{$anchorStream=[IO.FileStream]::new($anchorPath,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)}catch{
 $cause=$_.Exception;while($cause.InnerException){$cause=$cause.InnerException}
 if(($cause.HResult -band 65535) -ne 2){throw 'ASTRA_ADVISOR_ANCHOR_UNREADABLE'};$absent=$true
}
if(-not $absent){
 if($expectedAnchorHash -cnotmatch '\A[0-9a-f]{64}\z' -or $expectedBootstrapHash -cnotmatch '\A[0-9a-f]{64}\z'){throw 'ASTRA_ADVISOR_ANCHOR_NOT_DEPLOYED'}
 $bootstrap=Join-Path $PSScriptRoot 'IMC-AdvisorClientBootstrap.ps1'
 $bootstrapStream=[IO.FileStream]::new($bootstrap,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
 $sha=[Security.Cryptography.SHA256]::Create()
 try{$hash=([BitConverter]::ToString($sha.ComputeHash($bootstrapStream))).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
 if($hash -cne $expectedBootstrapHash){throw 'ASTRA_ADVISOR_BOOTSTRAP_CHANGED'}
 $bootstrapStream.Position=0;$reader=[IO.StreamReader]::new($bootstrapStream,[Text.UTF8Encoding]::new($false,$true),$true,4096,$true)
 try{$code=$reader.ReadToEnd()}finally{$reader.Dispose()}
 $binding=& ([scriptblock]::Create($code)) -AnchorStream $anchorStream -AnchorHash $expectedAnchorHash
 $brokerDir=$binding.BrokerDir
}else{
 if($expectedAnchorHash -or $expectedBootstrapHash){throw 'ASTRA_ADVISOR_ANCHOR_MISSING'}
 $brokerDir=$env:IMC_ASTRA_BROKER_DIR
}
if (-not $brokerDir -or -not (Test-Path -LiteralPath $brokerDir -PathType Container)) {
  Write-Error 'ASTRA_ADVISOR_BROKER_UNAVAILABLE'
  exit 125
}
if (-not $Question -or $Question.Count -eq 0) {
  Write-Error 'ASTRA_ADVISOR_QUESTION_REQUIRED'
  exit 2
}

$id = [guid]::NewGuid().ToString('N')
if($binding){& $binding.Check}
$requestPath = Join-Path $brokerDir ($id + '.request.json')
$tempRequest = Join-Path $brokerDir ($id + '.request.tmp')
$responsePath = Join-Path $brokerDir ($id + '.response.json')
$q = ($Question -join ' ')
if ($q.Length -gt 20000) {
  Write-Error 'ASTRA_ADVISOR_QUESTION_TOO_LONG'
  exit 2
}
@{
  id = $id
  question = $q
  risk_review = [bool]$RiskReview
} | ConvertTo-Json -Compress |
  Set-Content -LiteralPath $tempRequest -Encoding utf8
if($binding){& $binding.Check}
Move-Item -LiteralPath $tempRequest -Destination $requestPath

$deadline = [DateTime]::UtcNow.AddSeconds(120)
while ([DateTime]::UtcNow -lt $deadline) {
  if($binding){& $binding.Check}
  if (Test-Path -LiteralPath $responsePath -PathType Leaf) { break }
  Start-Sleep -Milliseconds 100
}
if (-not (Test-Path -LiteralPath $responsePath -PathType Leaf)) {
  Remove-Item -LiteralPath $requestPath -Force -ErrorAction SilentlyContinue
  Write-Error 'ASTRA_ADVISOR_BROKER_TIMEOUT'
  exit 124
}

try {
  if($binding){& $binding.Check}
  $response = Get-Content -LiteralPath $responsePath -Raw | ConvertFrom-Json
  if ($null -ne $response.output -and [string]$response.output -ne '') {
    [Console]::Out.WriteLine([string]$response.output)
  }
  $exitCode = [int]$response.exit_code
}
finally {
  Remove-Item -LiteralPath $responsePath -Force -ErrorAction SilentlyContinue
}

exit $exitCode
