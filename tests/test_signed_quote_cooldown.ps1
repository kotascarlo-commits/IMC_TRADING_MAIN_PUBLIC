param([Parameter(Mandatory=$true)][string]$Workspace,[Parameter(Mandatory=$true)][string]$ModuleDir,[switch]$UnknownCooldown,
 [ValidateRange(-1,1000000000)][int]$ExpectedQuotaLimit=-1,
 [ValidateRange(-1,1000000000)][int]$ExpectedQuotaRemaining=-1,
 [ValidateSet('SIGNED_QUOTE_PROBE','EXIST_QUOTE')][string]$QuoteMode='SIGNED_QUOTE_PROBE',
 [ValidateRange(100,599)][int]$ExpectedHttpStatus=429,
 [ValidateSet('RATE_LIMIT_EXCEEDED','RATE_LIMIT_BANNED','ERROR_RATE_LIMIT_BLOCKED')][string]$ExpectedApiError,
 [ValidateRange(1,12)][int]$CliTimeoutSeconds=12)
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath($Workspace)
if (-not [IO.File]::Exists((Join-Path $root 'TOOLS\GMGN-ReadProtocol.ps1')) -or
    [IO.Path]::GetFullPath($ModuleDir) -cne (Join-Path $root 'node_modules\gmgn-cli\dist')) { throw 'ISOLATED_QUOTE_FIXTURE_REQUIRED' }
. (Join-Path $root 'TOOLS\GMGN-ReadProtocol.ps1')
$cfg=Get-GmgnReadConfig
$cfg.cli_timeout_seconds=$CliTimeoutSeconds
$node=(Get-Command node.exe -CommandType Application).Source
$entry=Join-Path $root 'TOOLS\GMGN-SignedQuoteProbe.mjs'
$quoteArgs=@('order','quote','--chain','sol','--from','So11111111111111111111111111111111111111112',
 '--input-token','So11111111111111111111111111111111111111112',
 '--output-token','EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v','--amount','10000000','--slippage','1','--raw')
for ($i=0;$i -lt 2;$i++) {
  try {
    Invoke-GmgnCoordinatedRead $node $entry $quoteArgs $cfg '' $Workspace $ModuleDir $QuoteMode | Out-Null
    throw '429 was accepted'
  } catch {
    $expected=if ($UnknownCooldown -and $i -eq 1) { 'GMGN_COOLDOWN_UNKNOWN' }
      elseif ($ExpectedApiError -ceq 'RATE_LIMIT_BANNED') { 'GMGN_RATE_BANNED' }
      elseif ($ExpectedApiError -ceq 'ERROR_RATE_LIMIT_BLOCKED') { 'GMGN_RATE_BUSINESS_BLOCKED' }
      else { 'GMGN_RATE_LIMITED' }
    if ($_.Exception.Message -cne $expected) { throw }
    $diagnostic=New-GmgnFailureEvidence $_.Exception
    if ($i -eq 0 -and $ExpectedApiError) {
      if ($diagnostic.api_error -cne $ExpectedApiError) { throw 'typed provider enum missing' }
    } elseif ($null -ne $diagnostic.api_error) { throw 'no new provider enum expected' }
    foreach ($name in @('quota_limit','quota_remaining')) {
      $expectedHint=if($name -ceq 'quota_limit'){$ExpectedQuotaLimit}else{$ExpectedQuotaRemaining}
      $actualHint=$_.Exception.Data[$name]
      if ($i -eq 1 -or $expectedHint -eq -1) {
        if ($null -ne $actualHint) { throw 'no new HTTP hint expected' }
      } elseif ($actualHint -isnot [int] -or $actualHint -ne $expectedHint) { throw 'typed quota hint missing' }
    }
  }
}
$saved=Get-Content -LiteralPath (Join-Path $Workspace 'gmgn-read-cooldown.json') -Raw | ConvertFrom-Json
if ($saved.http_status -ne $ExpectedHttpStatus) { throw '429 status missing' }
if ($UnknownCooldown) {
  if (-not $saved.unknown -or $null -ne $saved.retry_not_before) { throw 'unknown cooldown missing' }
  $before=[IO.File]::ReadAllText((Join-Path $Workspace 'gmgn-read-cooldown.json'))
  & powershell.exe -NoProfile -File (Join-Path $PSScriptRoot 'test_gmgn_cooldown_restart.ps1') -Workspace $Workspace -Node $node -Entry $entry -ExpectedCode 'GMGN_COOLDOWN_UNKNOWN' | Out-Null
  if ($LASTEXITCODE -ne 0 -or [IO.File]::ReadAllText((Join-Path $Workspace 'gmgn-read-cooldown.json')) -cne $before) { throw 'UNKNOWN changed across restart' }
} elseif ($saved.unknown -or
  ([DateTimeOffset]::Parse($saved.retry_not_before)-[DateTimeOffset]::UtcNow).TotalSeconds -lt 20) { throw '429 cooldown missing' }
if ([IO.File]::ReadAllText((Join-Path $Workspace 'fetches.txt')) -cne '1') { throw '429 sent twice' }
if ([IO.File]::ReadAllText((Join-Path $Workspace 'node-starts.txt')) -cne '1') { throw '429 started Node twice' }
Write-Output 'signed non-JSON 429 shared cooldown passed'
