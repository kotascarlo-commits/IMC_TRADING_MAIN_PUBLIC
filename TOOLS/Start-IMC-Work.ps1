param(
  [ValidateSet('routine', 'standard', 'complex')][string]$Level = 'standard',
  [switch]$Check,
  [switch]$BrokerCheck,
  [switch]$ShowLaunchConfig
)

$ErrorActionPreference = 'Stop'
$advisorConfig = & (Join-Path $PSScriptRoot 'Get-IMC-AdvisorConfig.ps1')
. (Join-Path $PSScriptRoot 'IMC_WORK_SESSION.ps1')
$workSessionArguments = @(Get-IMCWorkSessionArguments -Config $advisorConfig -Level $Level)
$workReasoningEffort = $advisorConfig.work.$Level
if ($ShowLaunchConfig) {
  [pscustomobject]@{model=$advisorConfig.work.model; reasoning_effort=$workReasoningEffort;
    sandbox_mode=$advisorConfig.work.sandbox_mode; approval_policy=$advisorConfig.work.approval_policy;
    approvals_reviewer=$advisorConfig.work.approvals_reviewer; git_transport='native';
    git_write_verified=$false; applies_to='new_session'} | ConvertTo-Json -Compress
  exit 0
}
$codexCommand = Get-Command codex.cmd -CommandType Application -ErrorAction Stop
$codexEntryPoint = Join-Path (Split-Path $codexCommand.Source -Parent) 'node_modules\@openai\codex\bin\codex.js'
if (-not (Test-Path -LiteralPath $codexEntryPoint -PathType Leaf)) {
  throw "Codex npm entry point missing: $codexEntryPoint"
}
$nodeCommand = Get-Command node.exe -CommandType Application -ErrorAction Stop

# GMGN credentials stay outside the Codex sandbox. A local read-only broker
# executes approved GMGN read commands and redacts secrets from all responses.
$gmgnEnvFile = Join-Path $HOME '.config\gmgn\.env'
$gmgnLoaded = Test-Path -LiteralPath $gmgnEnvFile -PathType Leaf
$realGmgnCli = $null
if ($gmgnLoaded) {
  $realGmgnCli = (Get-Command gmgn-cli.cmd -CommandType Application -ErrorAction Stop).Source
}
if ($Check) {
  Write-Output ('IMC OK: {0} / {1}; Advisor {2} / {3}; RiskReview {4}; GMGNLocalAuth {5}; Work {6}/{7}; Approvals {8}; Git native (write not verified)' -f $advisorConfig.work.model, $workReasoningEffort, $advisorConfig.model, $advisorConfig.reasoning_effort, $advisorConfig.risk_reasoning_effort, $gmgnLoaded, $advisorConfig.work.sandbox_mode, $advisorConfig.work.approval_policy, $advisorConfig.work.approvals_reviewer)
  & $nodeCommand.Source $codexEntryPoint --version
  exit $LASTEXITCODE
}

$gmgnBrokerDir = $null
$gmgnBrokerProcess = $null
$gmgnBrokerReady = $false
. (Join-Path $PSScriptRoot 'IMC_WORK_BROKERS.ps1')
. (Join-Path $PSScriptRoot 'IMC_WORK_PROGRESS.ps1')
$workBrokerJob=$null; $gmgnOwnedDir=$null; $astraOwnedDir=$null; $progressWatcher=$null
$env:IMC_WORK_SESSION_ID=[guid]::NewGuid().ToString('N')
$env:IMC_WORK_PROGRESS_PATH=Join-Path (Split-Path -Parent $PSScriptRoot) ('RUNTIME\work_progress.'+$env:IMC_WORK_SESSION_ID+'.json')
try {
$workBrokerJob=[IMCRuntimeJob]::new()
$env:IMC_GMGN_IPC_TOKEN = New-GmgnIpcToken
# Install the read-only client even if no host broker becomes ready. Its
# unavailable response blocks fallback to a real GMGN CLI in the inherited PATH.
Remove-Item Env:IMC_GMGN_BROKER_DIR -ErrorAction SilentlyContinue
Remove-Item Env:IMC_ASTRA_BROKER_DIR -ErrorAction SilentlyContinue
$shimDir = Join-Path $PSScriptRoot 'GMGN_READ_SHIM'
if (-not (Test-Path -LiteralPath (Join-Path $shimDir 'gmgn-cli.cmd') -PathType Leaf)) {
  throw 'GMGN read-only client missing; refusing to start the work session.'
}
$env:PATH = $shimDir + ';' + $env:PATH
if ($gmgnLoaded -and $realGmgnCli) {
  $gmgnOwnedDir=New-IMCOwnedBrokerDirectory $env:TEMP ('imc-gmgn-broker-'+[guid]::NewGuid().ToString('N'))
  $gmgnBrokerDir=$gmgnOwnedDir.Path
  $brokerScript = Join-Path $PSScriptRoot 'GMGN-ReadBroker.ps1'
  $gmgnBrokerProcess = $workBrokerJob.Start((Join-Path $PSHOME 'powershell.exe'),[string[]]@(
    '-NoProfile','-ExecutionPolicy','Bypass','-File',$brokerScript,
    '-BrokerDir',$gmgnBrokerDir,'-RealCli',$realGmgnCli,'-ParentPid',[string]$PID
  ),(Split-Path -Parent $PSScriptRoot))
  $readyFile = Join-Path $gmgnBrokerDir 'broker.ready'
  $deadline = [DateTime]::UtcNow.AddSeconds(5)
  while ([DateTime]::UtcNow -lt $deadline -and -not (Test-Path -LiteralPath $readyFile)) {
    Start-Sleep -Milliseconds 100
  }
  $gmgnBrokerReady = Test-Path -LiteralPath $readyFile -PathType Leaf
  if ($gmgnBrokerReady) {
    $env:IMC_GMGN_BROKER_DIR = $gmgnBrokerDir
  }
}

$astraOwnedDir=New-IMCOwnedBrokerDirectory $env:TEMP ('imc-astra-broker-'+[guid]::NewGuid().ToString('N'))
$astraBrokerDir=$astraOwnedDir.Path
$astraBrokerProcess = $null
$astraBrokerReady = $false
$astraBrokerScript = Join-Path $PSScriptRoot 'IMC-AdvisorBroker.ps1'
$astraBrokerProcess = $workBrokerJob.Start((Join-Path $PSHOME 'powershell.exe'),[string[]]@(
  '-NoProfile','-ExecutionPolicy','Bypass','-File',$astraBrokerScript,
  '-BrokerDir',$astraBrokerDir,
  '-Project','%USERPROFILE%\Desktop\IMC_TRADING_MAIN',
  '-Model',$advisorConfig.model,
  '-Reasoning',$advisorConfig.reasoning_effort,
  '-RiskReasoning',$advisorConfig.risk_reasoning_effort,
  '-ParentPid',[string]$PID
),(Split-Path -Parent $PSScriptRoot))
$astraReadyFile = Join-Path $astraBrokerDir 'broker.ready'
$astraDeadline = [DateTime]::UtcNow.AddSeconds(5)
while ([DateTime]::UtcNow -lt $astraDeadline -and -not (Test-Path -LiteralPath $astraReadyFile)) {
  Start-Sleep -Milliseconds 100
}
$astraBrokerReady = Test-Path -LiteralPath $astraReadyFile -PathType Leaf
if ($astraBrokerReady) {
  $env:IMC_ASTRA_BROKER_DIR = $astraBrokerDir
  $astraShimDir = Join-Path $PSScriptRoot 'ASTRA_ADVISOR_SHIM'
  $env:PATH = $astraShimDir + ';' + $env:PATH
}

# Git uses native Codex approvals. Do not proxy protected .git writes to a
# privileged host process or test the broker by changing the real index.

if ($BrokerCheck) {
  $astraOk = $false
  $gmgnOk = $false
  $writeBlocked = $false
  try {
    if ($astraBrokerReady) {
      $previousPreference = $ErrorActionPreference
      $ErrorActionPreference = 'Continue'
      try {
        $astraOutput = @(& imc-ask-advisor.cmd 'Pruefe read-only mit git rev-parse --is-inside-work-tree. Wenn true, antworte beginnend mit OK und nenne GIT=PASS.' 2>$null)
        $astraOk = ($LASTEXITCODE -eq 0 -and (($astraOutput -join [Environment]::NewLine) -match 'GIT=PASS'))
      } finally { $ErrorActionPreference = $previousPreference }
    }
    if ($gmgnBrokerReady) {
      $previousPreference = $ErrorActionPreference
      $ErrorActionPreference = 'Continue'
      try {
        & gmgn-cli.cmd portfolio info --raw *> $null
        $gmgnOk = ($LASTEXITCODE -eq 0)
        & gmgn-cli.cmd swap --help *> $null
        $writeBlocked = ($LASTEXITCODE -ne 0)
      } finally { $ErrorActionPreference = $previousPreference }
    }
    Write-Output ('ASTRA_BROKER=' + $(if ($astraOk) { 'PASS' } else { 'FAIL' }))
    Write-Output ('GMGN_READ=' + $(if ($gmgnOk) { 'PASS' } else { 'FAIL' }))
    if (-not $gmgnOk -and $gmgnBrokerDir) {
      $diag=Join-Path $gmgnBrokerDir 'broker.last_error'
      if (Test-Path -LiteralPath $diag) {
        Write-Output ('GMGN_DIAG=' + ((Get-Content -LiteralPath $diag -Raw).Trim()))
      }
    }
    Write-Output ('GMGN_WRITE_BLOCK=' + $(if ($writeBlocked) { 'PASS' } else { 'FAIL' }))
    Write-Output 'GIT_NATIVE_WRITE=NOT_TESTED (requires an approved operation in the new Codex session)'
  }
  finally {
    # The outer finally owns every broker and retained IPC directory.
  }
  if ($astraOk -and $gmgnOk -and $writeBlocked) { exit 0 } else { exit 1 }
}

Set-Location '%USERPROFILE%\Desktop\IMC_TRADING_MAIN'
$Host.UI.RawUI.WindowTitle = 'CHATGPT IMC - SOL {0} + ASTRA ADVISOR' -f $workReasoningEffort.ToUpperInvariant()
Clear-Host
Write-Host '===================================================='
Write-Host ' CHATGPT IMC'
Write-Host (' WORK    : {0} / {1} / Hauptagent' -f $advisorConfig.work.model, $workReasoningEffort.ToUpperInvariant())
Write-Host (' ADVISOR : {0} / {1} / automatisch / READ-ONLY' -f $advisorConfig.model, $advisorConfig.reasoning_effort.ToUpperInvariant())
Write-Host (' ASTRA IPC: {0}' -f $(if ($astraBrokerReady) { 'BROKER READY / HOST GIT ACCESS' } else { 'BROKER FAILED' }))
Write-Host (' RISIKO  : Astra / {0} / -RiskReview' -f $advisorConfig.risk_reasoning_effort.ToUpperInvariant())
Write-Host (' GMGN    : {0}' -f $(if ($gmgnBrokerReady) { 'READ-ONLY BROKER READY' } elseif ($gmgnLoaded) { 'LOCAL AUTH FOUND / BROKER FAILED' } else { 'LOCAL AUTH NOT FOUND' }))
Write-Host (' GIT     : native / {0} / Reviewer {1} (configured; write not verified)' -f $advisorConfig.work.approval_policy, $advisorConfig.work.approvals_reviewer)
Write-Host ' Regel   : Reviews gebuendelt; Routine ohne Extra-Advisor'
Write-Host '===================================================='
Write-Host ''
$progressWatcher=Start-IMCProgressTitleWatcher $env:IMC_WORK_SESSION_ID $Host.UI.RawUI $env:IMC_WORK_PROGRESS_PATH

$startupPrompt = @"
Du bist der IMC-Hauptagent.
Lies zuerst AGENTS.md und IMC_CODEX_MASTERPLAN.md.

Feste Rollen:
- Du arbeitest als $($advisorConfig.work.model) mit Reasoning $workReasoningEffort (Profil $Level).
- Routine Low, normale Arbeit Medium, komplexe Arbeit High laut config/imc-advisor.json.
- Ein Launcherwechsel wirkt erst fuer eine neu gestartete Sitzung. Keine Modellumschaltung behaupten.
- Du bist der einzige Agent, der Dateien aendern, testen oder committen darf.
- $($advisorConfig.model) mit Reasoning $($advisorConfig.reasoning_effort) ist IMMER dein unabhaengiger Read-only-Advisor.
- Astra wird ueber imc-ask-advisor an einen lokalen Host-Broker ausserhalb deiner Workspace-Sandbox delegiert. Dadurch hat Astra read-only Zugriff auf das bestehende Git-Repository, ohne einen zweiten Codex-Prozess innerhalb deiner Sandbox zu starten.
- Frage nicht nach einem Broker-Pfad. Verwende imc-ask-advisor direkt; der IPC-Pfad ist bereits als Prozessumgebung gesetzt.
- Lade Arbeits- und Advisor-Einstellungen aus config/imc-advisor.json.
- Pro inhaltlicher Codeaufgabe eine gebuendelte Advisor-Pruefung.
- Reine Statusfragen, bereits gepruefte Erklaerungen und triviale Doku/Kosmetik ohne Logikaenderung brauchen keine Extra-Pruefung.
- Architektur, Datenbank-Cleanup, Execution-/Live-Pfade, irreversible Schritte und widerspruechliche Befunde brauchen VOR Umsetzung -RiskReview ($($advisorConfig.risk_reasoning_effort)).
- RiskReview hat Vorrang vor Routineausnahmen und ersetzt die normale Review.
- Analysiere die Aufgabe zuerst kurz selbst. Rufe dann automatisch:
  imc-ask-advisor "<konkrete Aufgabe + dein geplanter Ansatz>"
  auf.
- Bei Risiko: imc-ask-advisor -RiskReview "<Aufgabe + Ansatz + konkrete Quellen>".
- Nur relevante Quellen und knappe Fragen; keine doppelten Reviews ohne neue Entscheidung oder neuen Befund.
- Beruecksichtige Astra vor deiner endgueltigen Umsetzung/Antwort.
- Wenn Astra RISK oder CHANGE meldet, klaere den Konflikt anhand von Code, Daten, Logs oder Git.
- Astra darf keine Dateien veraendern und keine IMC-Prozesse starten/stoppen.
- Bei destruktiven Schritten oder Datenloeschungen muss Astra VOR der Aktion konsultiert werden.
- Arbeite bei normalen Entwicklungs-, Analyse-, Test-, Refactoring- und Reparaturaufgaben selbststaendig bis zum Abschluss; frage nicht nach Zwischenfreigaben.
- Stoppe fuer Nutzereingabe nur, wenn Login/MFA/CAPTCHA/Zugangsdaten fehlen oder eine irreversible externe Aktion reale Assets/Konten/Geld bewegen wuerde. Fuer normale Entwicklung keine Sandbox- oder Approval-Rueckfragen stellen.
- Beachte ausserdem die ausdrueckliche Loeschfreigabe aus AGENTS.md; keine Dateien ohne die dort verlangte Freigabe loeschen.
- Git-Schreiboperationen laufen ueber native git-Kommandos innerhalb der autonomen Entwicklungsfreigabe. Keine ACL-Manipulation und keinen Host-Git-Schreibbroker zum Umgehen technischer Schutzgrenzen verwenden.
- Der Entwicklungsmodus ist $($advisorConfig.work.sandbox_mode)/$($advisorConfig.work.approval_policy). Er gilt nur fuer Entwicklungsarbeit; Echtgeld/Broadcast bleibt eine getrennte Owner-Live-Freigabe. Pruefe Git-Schreiboperationen weiterhin anhand des resultierenden Diffs.
- Zeige dem Nutzer bei sinnvollen Meilensteinen einen realistischen Gesamtfortschritt als [IMC WORK] <0-100>% plus aktuellen Schritt; 100% nur nach Integration, Tests und Review.
- Aktualisiere dafuer auch den lokalen Fortschritt: lade TOOLS/IMC_WORK_PROGRESS.ps1; verwende ausschliesslich Path aus IMC_WORK_PROGRESS_PATH und SessionId aus IMC_WORK_SESSION_ID. Beginne nach Aufgabenanalyse mit eigenem TaskId, gewichteten Paketen (Summe 100) und ExpectedRevision 0. Aktualisiere per Set-IMCWorkProgress mit aktuell gelesener ExpectedRevision, CurrentStep, PackageId/PackagePercent, AstraStatus und echten Testzahlen. Neue Arbeit invalidiert alte Tests. Keine automatische Uebernahme fremder Aufgaben; completed/100 nur nach allen Paket-, Test- und Reviewbedingungen.
- Zeige dem Nutzer knapp an: Sol arbeitet / Astra geprueft / Ergebnis.
- Keine pruefpflichtige Aufgabe ohne beruecksichtigte Advisor-Pruefung abschliessen.
- GMGN-Lesezugriffe laufen ueber den lokalen read-only Broker hinter gmgn-cli. Erlaubt sind Markt-/Token-/Portfolio-Lesezugriffe, gas-price sowie order get/quote/strategy list. Schreibende GMGN-Kommandos sind dort absichtlich blockiert.
- GMGN-Schluessel niemals lesen, ausgeben, kopieren oder ins Repository schreiben.
- Kein XHigh/Max im normalen Betrieb. Tests und Trading-Sicherheitsregeln bleiben verbindlich.

Beginne jetzt als IMC-Hauptagent und warte auf die Nutzeraufgabe.
"@

# Invoke Node directly so cmd.exe cannot split the multiline startup prompt.
$workArguments = @($workSessionArguments) + @($startupPrompt)
$workArguments = @(ConvertTo-IMCNativeArguments -Arguments $workArguments)
$workExitCode = 1
  & $nodeCommand.Source $codexEntryPoint @workArguments
  $workExitCode = $LASTEXITCODE
}
finally {
  Stop-IMCProgressTitleWatcher $progressWatcher
  Stop-IMCOwnedWorkBrokers $workBrokerJob @($gmgnOwnedDir,$astraOwnedDir)
  Remove-Item Env:IMC_GMGN_BROKER_DIR -ErrorAction SilentlyContinue
  Remove-Item Env:IMC_ASTRA_BROKER_DIR -ErrorAction SilentlyContinue
  Remove-Item Env:IMC_GMGN_IPC_TOKEN -ErrorAction SilentlyContinue
  Remove-Item Env:IMC_WORK_SESSION_ID -ErrorAction SilentlyContinue
  Remove-Item Env:IMC_WORK_PROGRESS_PATH -ErrorAction SilentlyContinue
}
exit $workExitCode
