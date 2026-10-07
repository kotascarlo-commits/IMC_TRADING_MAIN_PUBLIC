param(
  [Parameter(Position=0, ValueFromRemainingArguments=$true)][string[]]$CliArgs
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'GMGN-ReadProtocol.ps1')
$config = Get-GmgnReadConfig
$deadline = [DateTimeOffset]::UtcNow.AddSeconds($config.response_timeout_seconds)
if ($env:IMC_GMGN_READ_DEADLINE_UTC) {
  $budgetDeadline = [DateTimeOffset]::MinValue
  if (-not [DateTimeOffset]::TryParseExact($env:IMC_GMGN_READ_DEADLINE_UTC,
      "yyyy-MM-dd'T'HH:mm:ss.ffffffzzz", [Globalization.CultureInfo]::InvariantCulture,
      [Globalization.DateTimeStyles]::None, [ref]$budgetDeadline) -or
      $budgetDeadline.Offset -ne [TimeSpan]::Zero -or $budgetDeadline -le [DateTimeOffset]::UtcNow) {
    [Console]::Error.WriteLine('GMGN_READ_BUDGET_INVALID'); exit 124
  }
  if ($budgetDeadline -lt $deadline) { $deadline=$budgetDeadline }
}
$brokerDir = $env:IMC_GMGN_BROKER_DIR
if (-not $brokerDir -or -not (Test-Path -LiteralPath $brokerDir -PathType Container)) {
  [Console]::Error.WriteLine('GMGN_READ_ONLY_BROKER_UNAVAILABLE')
  exit 125
}
try {
  $brokerDir = Assert-GmgnLocalPath $brokerDir
  $identity = Read-GmgnJson (Join-Path $brokerDir 'broker.ready') $config.max_request_bytes
  # The host broker performs the strong CIM process binding before it accepts
  # any request. The sandbox client deliberately avoids CIM because Windows
  # denies that query to CodexSandboxOffline. Here we validate only the
  # immutable IPC envelope needed to bind request/response to this instance.
  Assert-GmgnReadyEnvelope $identity $brokerDir
  $expectedIdentity=$identity | ConvertTo-Json -Depth 12 -Compress
  if (-not (Test-GmgnReadOnlyArgs $CliArgs $config)) { throw 'GMGN_COMMAND_BLOCKED' }
} catch {
  [Console]::Error.WriteLine('GMGN_READ_ONLY_BROKER_UNAVAILABLE')
  exit 125
}

$id = [guid]::NewGuid().ToString('N')
$requestPath = Join-Path $brokerDir ($id + '.request.json')
$responsePath = Join-Path $brokerDir ($id + '.response.json')

try {
  Write-GmgnAtomicJson $brokerDir ($id + '.request.json') @{
    schema_version=3; instance_id=$identity.instance_id; id=$id; args=@($CliArgs); expires_at=$deadline.ToString('o')
  } $config.max_request_bytes
} catch {
  [Console]::Error.WriteLine('GMGN_READ_ONLY_BROKER_ERROR')
  exit 125
}

while ([DateTimeOffset]::UtcNow -lt $deadline) {
  if (Test-Path -LiteralPath $responsePath -PathType Leaf) { break }
  Start-Sleep -Milliseconds $config.poll_milliseconds
}
if (-not (Test-Path -LiteralPath $responsePath -PathType Leaf)) {
  [Console]::Error.WriteLine('GMGN_READ_ONLY_BROKER_TIMEOUT')
  exit 124
}

try {
  $currentIdentity = Read-GmgnJson (Join-Path $brokerDir 'broker.ready') $config.max_request_bytes
  Assert-GmgnReadyEnvelope $currentIdentity $brokerDir
  if (($currentIdentity | ConvertTo-Json -Depth 12 -Compress) -cne $expectedIdentity) {
    throw 'GMGN_BROKER_IDENTITY_CHANGED'
  }
  $response = Read-GmgnResponse $responsePath $id $identity.instance_id $config
  if ($response.exit_code -eq 0) {
    [Console]::Out.WriteLine((ConvertTo-GmgnSafeOutput $response.output $CliArgs))
  } else {
    [Console]::Error.WriteLine($response.output)
    # Already authenticated and strictly typed; contains no provider strings.
    [Console]::Error.WriteLine('GMGN_READ_EVIDENCE=' + ($response.evidence | ConvertTo-Json -Compress))
  }
  $exitCode = [int]$response.exit_code
} catch {
  [Console]::Error.WriteLine('GMGN_READ_ONLY_BROKER_ERROR')
  $exitCode = 125
}
finally {
  try { $null=Assert-GmgnLocalPath $responsePath; Remove-Item -LiteralPath $responsePath -Force } catch {}
}

exit $exitCode
