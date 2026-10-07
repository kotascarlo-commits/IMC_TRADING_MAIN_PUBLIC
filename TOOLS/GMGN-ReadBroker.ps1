param(
  [Parameter(Mandatory=$true)][string]$BrokerDir,
  [Parameter(Mandatory=$true)][string]$RealCli,
  [Parameter(Mandatory=$true)][int]$ParentPid,
  [ValidateSet('work','runtime')][string]$ParentRole = 'work',
  [switch]$SignedQuoteProbe
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'GMGN-ReadProtocol.ps1')
$config = Get-GmgnReadConfig
if ($SignedQuoteProbe) {
  if ($ParentRole -cne 'runtime') { throw 'GMGN_PROBE_PARENT_INVALID' }
  $parent=Get-CimInstance Win32_Process -Filter ('ProcessId={0}' -f $ParentPid) -ErrorAction Stop
  . (Join-Path (Split-Path -Parent $PSScriptRoot) 'LAUNCHERS\IMC_PROCESS_COMMON.ps1')
  if ((Get-IMCProcessRole $parent).Role -cne 'GMGNReadProbe') { throw 'GMGN_PROBE_PARENT_INVALID' }
  $parentArgs=@([IMCCommandLine]::Split($parent.CommandLine))
  if (@($parentArgs | Where-Object { $_ -ceq '-SignedQuoteProbe' }).Count -ne 1) { throw 'GMGN_PROBE_PARENT_INVALID' }
}
New-Item -ItemType Directory -Path $BrokerDir -Force | Out-Null
$BrokerDir = Assert-GmgnLocalPath $BrokerDir
$ready = Join-Path $BrokerDir 'broker.ready'
$stop = Join-Path $BrokerDir 'broker.stop'
$node = (Get-Command node.exe -CommandType Application -ErrorAction Stop).Source
$entry = Join-Path (Split-Path ([IO.Path]::GetFullPath($RealCli)) -Parent) 'node_modules\gmgn-cli\dist\index.js'
if (-not (Test-Path -LiteralPath $entry -PathType Leaf)) { throw 'GMGN_CLI_UNAVAILABLE' }
$quoteMode=if($SignedQuoteProbe){'SIGNED_QUOTE_PROBE'}else{'EXIST_QUOTE'}
$transport=Get-GmgnTransportBinding $entry $quoteMode
$identity = @{schema_version=3; instance_id=[guid]::NewGuid().ToString('N'); directory=$BrokerDir;transport=$transport;
  broker=(Get-GmgnProcessBinding $PID $PSCommandPath);
  parent=(Get-GmgnProcessBinding $ParentPid $(if ($ParentRole -eq 'work') {
    Join-Path $PSScriptRoot 'Start-IMC-Work.ps1'
  } else { Join-Path (Split-Path -Parent $PSScriptRoot) 'LAUNCHERS\RUN_IMC_SIGNAL_ONLY.ps1' }))}
$identity=[pscustomobject]$identity
$handles=@{}
try {
foreach ($role in @('broker','parent')) {
  $handles[$role]=[Diagnostics.Process]::GetProcessById($identity.$role.pid)
  $null=$handles[$role].Handle
  Assert-GmgnNativeBinding $handles[$role] $identity.$role
}

function Write-AtomicResponse {
  param([string]$Id, [int]$ExitCode, [string]$Output, $Evidence=$null)
  if ($Id -cnotmatch '\A[0-9a-f]{32}\z') { throw 'invalid_response_id' }
  Write-GmgnAtomicJson $BrokerDir ($Id + '.response.json') @{
    schema_version=3; instance_id=$identity.instance_id; id=$Id; exit_code=$ExitCode; output=$Output;evidence=$Evidence
  } $config.max_response_bytes
}

Write-GmgnAtomicJson $BrokerDir 'broker.ready' $identity $config.max_request_bytes
$nextIdentityCheck = [DateTime]::MinValue
while ($true) {
  if (Test-Path -LiteralPath $stop) { break }
  if ([DateTime]::UtcNow -ge $nextIdentityCheck) {
    try {
      Assert-GmgnReady $identity $BrokerDir $handles
    } catch {
      $code=[string]$_.Exception.Message
      if($code -cnotmatch '\AGMGN_[A-Z0-9_]+\z'){$code='GMGN_INTERNAL_ERROR'}
      Set-Content -LiteralPath (Join-Path $BrokerDir 'broker.last_error') -Value $code -Encoding ascii -ErrorAction SilentlyContinue
      break
    }
    $nextIdentityCheck = [DateTime]::UtcNow.AddMilliseconds($config.identity_check_milliseconds)
  }

  $requests = @(Get-ChildItem -LiteralPath $BrokerDir -Filter '*.request.json' -File -ErrorAction SilentlyContinue |
    Select-Object -First $config.max_requests_per_poll)
  foreach ($request in $requests) {
    $id = $null
    if ($request.Name -cnotmatch '\A([0-9a-f]{32})\.request\.json\z') { continue }
    $id = $Matches[1]

    try {
      # Retained native handles bind the original lifetime, without repeated
      # CIM queries. Revalidate before every request, not only on cadence.
      Assert-GmgnReady $identity $BrokerDir $handles
      Assert-GmgnTransportUnchanged $transport
      $payload = Read-GmgnRequest $request.FullName ([pscustomobject]$identity) $config
      if (($payload.args[0] + ' ' + $payload.args[1]) -ceq 'order quote') {
        $outputText = Invoke-GmgnCoordinatedRead $node (Join-Path $PSScriptRoot 'GMGN-SignedQuoteProbe.mjs') `
          $payload.args $config $payload.expires_at '' (Split-Path $entry -Parent) $quoteMode
      } else {
        $outputText = Invoke-GmgnCoordinatedRead $node $entry $payload.args $config $payload.expires_at
      }
      Write-AtomicResponse -Id $id -ExitCode 0 -Output $outputText
    }
    catch {
      $code=[string]$_.Exception.Message
      if($code -cnotmatch '\AGMGN_[A-Z0-9_]+\z'){$code='GMGN_INTERNAL_ERROR'}
      $retrySeconds=0
      if ($code -clike 'GMGN_RATE_*') {
        $retrySeconds=$_.Exception.Data['retry_after_seconds']
        if ($retrySeconds -isnot [int]) { $retrySeconds=-1 }
      }
      Set-Content -LiteralPath (Join-Path $BrokerDir 'broker.last_error') -Value $code -Encoding ascii -ErrorAction SilentlyContinue
      @{code=$code; retry_after_seconds=$retrySeconds; http_status=$_.Exception.Data['http_status'];
        api_code=$_.Exception.Data['api_code']; quota_limit=$_.Exception.Data['quota_limit'];
        quota_remaining=$_.Exception.Data['quota_remaining']; observed_at=[DateTimeOffset]::UtcNow.ToString('o')} |
        ConvertTo-Json -Compress | Set-Content -LiteralPath (Join-Path $BrokerDir 'broker.last_error.json') -Encoding ascii -ErrorAction SilentlyContinue
      if ($id) {
        try { Write-AtomicResponse -Id $id -ExitCode 125 -Output 'GMGN_READ_ONLY_BROKER_ERROR' -Evidence (New-GmgnFailureEvidence $_.Exception) } catch {}
      }
    }
    finally {
      try { $null=Assert-GmgnLocalPath $request.FullName; Remove-Item -LiteralPath $request.FullName -Force } catch {}
    }
  }

  Start-Sleep -Milliseconds $config.poll_milliseconds
}

Remove-Item -LiteralPath $ready -Force -ErrorAction SilentlyContinue
} finally { foreach ($handle in $handles.Values) { $handle.Dispose() } }
