[CmdletBinding(DefaultParameterSetName='ParentBound')]
param(
  [Parameter(Mandatory=$true)][string]$BrokerDir,
  [Parameter(Mandatory=$true)][string]$Project,
  [Parameter(Mandatory=$true)][string]$Model,
  [Parameter(Mandatory=$true)][string]$Reasoning,
  [Parameter(Mandatory=$true)][string]$RiskReasoning,
  [Parameter(Mandatory=$true,ParameterSetName='ParentBound')][int]$ParentPid,
  [Parameter(Mandatory=$true,ParameterSetName='Independent')][object]$HostContext
)

$ErrorActionPreference = 'Stop'
if($PSCmdlet.ParameterSetName -ceq 'ParentBound'){
. (Join-Path $PSScriptRoot 'GMGN-ReadProtocol.ps1')
$expectedParent=Get-GmgnProcessBinding $ParentPid (Join-Path $PSScriptRoot 'Start-IMC-Work.ps1')
$parentProcess=[Diagnostics.Process]::GetProcessById($ParentPid)
$null=$parentProcess.Handle
if ([math]::Abs(($parentProcess.StartTime.ToUniversalTime()-[DateTime]::Parse($expectedParent.created_at).ToUniversalTime()).Ticks) -ge 10 -or
    $parentProcess.MainModule.FileName -ine $expectedParent.executable -or $parentProcess.HasExited) {
  $parentProcess.Dispose(); throw 'ASTRA_PARENT_IDENTITY_CHANGED'
}
}else{
 if($HostContext.GetType().FullName -cne 'ImcAdvisorHostContext' -or -not $HostContext.GetType().IsSealed){throw 'ASTRA_CONTEXT_REQUIRED'}
 $HostContext.Check()
 if($Project -cne $HostContext.GetType().GetField('Project').GetRawConstantValue() -or
  $BrokerDir -cne $HostContext.BrokerDirectory){throw 'ASTRA_CONTEXT_ARGUMENT_MISMATCH'}
}
try {
if($PSCmdlet.ParameterSetName -ceq 'ParentBound'){
 New-Item -ItemType Directory -Path $BrokerDir -Force | Out-Null
 $BrokerDir=Assert-GmgnLocalPath $BrokerDir
}
$ready = Join-Path $BrokerDir 'broker.ready'
$stop = Join-Path $BrokerDir 'broker.stop'
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$advisorSettings=if($HostContext){$HostContext.ReadConfig()|ConvertFrom-Json -ErrorAction Stop}else{& (Join-Path $PSScriptRoot 'Get-IMC-AdvisorConfig.ps1')}
if($HostContext -and ($advisorSettings.model -cne 'gpt-6-astra' -or $advisorSettings.reasoning_effort -cne 'high' -or
 $advisorSettings.risk_reasoning_effort -cne 'high' -or $advisorSettings.work.model -cne 'gpt-6.1-sol' -or
 $advisorSettings.max_response_words -isnot [int] -or $advisorSettings.max_response_words -lt 80 -or
 $advisorSettings.max_response_words -gt 500)){throw 'ASTRA_INDEPENDENT_CONFIG_INVALID'}
if ($Model -cne $advisorSettings.model -or $Reasoning -cne $advisorSettings.reasoning_effort -or
    $RiskReasoning -cne $advisorSettings.risk_reasoning_effort) { throw 'ASTRA_CONFIG_MISMATCH' }

function Write-AtomicResponse {
  param([string]$Id, [int]$ExitCode, [string]$Output)
  if($HostContext){$HostContext.Check()}
  $final = Join-Path $BrokerDir ($Id + '.response.json')
  $temp = Join-Path $BrokerDir ($Id + '.response.tmp.' + [guid]::NewGuid().ToString('N'))
  @{ exit_code=$ExitCode; output=$Output } |
    ConvertTo-Json -Compress |
    Set-Content -LiteralPath $temp -Encoding utf8
  if($HostContext){$HostContext.Check()}
  Move-Item -LiteralPath $temp -Destination $final -ErrorAction Stop
}
function Invoke-Astra {
  param([string]$Question, [bool]$RiskReview)
  $effort = if ($RiskReview) { $RiskReasoning } else { $Reasoning }
  $reasoningConfig = 'model_reasoning_effort="{0}"' -f $effort
  $prompt = @"
Du bist ausschliesslich der unabhaengige IMC Astra Advisor.
Arbeite READ-ONLY. Aendere keine Dateien und starte/stoppe keine IMC-Prozesse.
Fuehre keine Tests aus. Ausschliesslich der Sol-Hauptagent darf Tests ausfuehren.
Lies, kopiere oder veroeffentliche niemals Credentials oder private Keys.
Arbeitsverzeichnis ist das bestehende Git-Repository: $Project
Lies AGENTS.md und nur die fuer die konkrete Frage relevanten Quellen.
Pruefe Behauptungen anhand von Code, Git, Daten oder Logs statt zu raten.
Gib zuerst: OK, RISK oder CHANGE.
Danach: Befund, Begruendung und konkrete Empfehlung fuer den Sol-Hauptagenten.
Keine weiteren Advisor oder Unteragenten konsultieren.
Antworte moeglichst in hoechstens $($advisorSettings.max_response_words) Woertern, ohne wesentliche Risiken wegzulassen.

Frage des Hauptagenten:
$Question
"@
  $previousPreference = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    $launcher=if($HostContext){$HostContext.Executable}else{'codex.cmd'}
    if($HostContext){$HostContext.Check()}
    $lines = @($prompt | & $launcher --search exec -C $Project -m $Model -c $reasoningConfig -s read-only --ephemeral - 2>$null)
    $exitCode = [int]$LASTEXITCODE
  }
  finally {
    $ErrorActionPreference = $previousPreference
  }
  if($HostContext){$HostContext.Check()}
  if ($exitCode -ne 0) {
    return @{ exit_code=$exitCode; output='ASTRA_ADVISOR_EXEC_FAILED' }
  }
  return @{ exit_code=0; output=(($lines | ForEach-Object { [string]$_ }) -join [Environment]::NewLine) }
}
Set-Content -LiteralPath $ready -Value 'ready' -Encoding ascii
$seen=[Collections.Generic.HashSet[string]]::new()

while ($true) {
  if($HostContext){$HostContext.Check()}
  if (Test-Path -LiteralPath $stop) { break }
  if ($parentProcess -and $parentProcess.HasExited) { break }

  $requests = @(Get-ChildItem -LiteralPath $BrokerDir -Filter '*.request.json' -File -ErrorAction SilentlyContinue)
  foreach ($request in $requests) {
    $name = $request.Name
    if ($name -cnotmatch '\A([0-9a-f]{32})\.request\.json\z') { continue }
    $id = $Matches[1]
    if (-not $seen.Add($id)) { continue }

    try {
      if($HostContext){
       $HostContext.Check()
       if([IO.Path]::GetFullPath($request.FullName) -cne (Join-Path $BrokerDir ($id+'.request.json')) -or
        ([IO.File]::GetAttributes($request.FullName) -band [IO.FileAttributes]::ReparsePoint)){throw 'ASTRA_REQUEST_PATH'}
      }else{$null=Assert-GmgnLocalPath $request.FullName}
      $stream=[IO.File]::OpenRead($request.FullName)
      try {
        if ($stream.Length -gt 65536) { throw 'request_too_large' }
        $buffer=[byte[]]::new(65537);$count=0
        while ($count -lt $buffer.Length) {
          $n=$stream.Read($buffer,$count,$buffer.Length-$count)
          if (-not $n) { break };$count+=$n
        }
        if ($count -gt 65536) { throw 'request_too_large' }
        $payload=([Text.UTF8Encoding]::new($false,$true).GetString($buffer,0,$count).TrimStart([char]0xfeff) | ConvertFrom-Json)
      } finally { $stream.Dispose() }
      if ([string]$payload.id -ne $id) { throw 'request_id_mismatch' }
      $question = [string]$payload.question
      if ([string]::IsNullOrWhiteSpace($question) -or $question.Length -gt 20000) { throw 'bad_question' }
      if ($payload.risk_review -isnot [bool]) { throw 'invalid_review_flag' }
      $risk = $payload.risk_review
      $result = Invoke-Astra -Question $question -RiskReview $risk
      Write-AtomicResponse -Id $id -ExitCode $result.exit_code -Output $result.output
    }
    catch {
      if($HostContext){$HostContext.Check()}
      Write-AtomicResponse -Id $id -ExitCode 125 -Output 'ASTRA_ADVISOR_BROKER_ERROR'
    }
  }

  Start-Sleep -Milliseconds 150
}

Remove-Item -LiteralPath $ready -Force -ErrorAction SilentlyContinue
} finally { if($parentProcess){$parentProcess.Dispose()} }
