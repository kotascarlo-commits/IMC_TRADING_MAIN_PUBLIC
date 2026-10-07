param([switch]$RiskReview)

$ErrorActionPreference = 'Stop'
$advisorConfig = & (Join-Path $PSScriptRoot 'Get-IMC-AdvisorConfig.ps1')
$advisorReasoningEffort = if ($RiskReview) { $advisorConfig.risk_reasoning_effort } else { $advisorConfig.reasoning_effort }
$advisorReasoningConfig = 'model_reasoning_effort="{0}"' -f $advisorReasoningEffort
Set-Location '%USERPROFILE%\Desktop\IMC_TRADING_MAIN'
$Host.UI.RawUI.WindowTitle = 'IMC ASTRA ADVISOR - {0} / {1}' -f $advisorConfig.model, $advisorReasoningEffort.ToUpperInvariant()
Clear-Host
Write-Host '===================================================='
Write-Host ' IMC ASTRA ADVISOR'
Write-Host (' Model:     {0}' -f $advisorConfig.model)
Write-Host (' Reasoning: {0}' -f $advisorReasoningEffort.ToUpperInvariant())
Write-Host ' Rolle:     unabhaengiger Pruefer / Ask Advisor'
Write-Host ' Rechte:    READ-ONLY'
Write-Host '===================================================='
Write-Host ''
$prompt = @"
Du bist der unabhaengige IMC Astra Advisor.
Lies zuerst AGENTS.md und nur die fuer die Frage relevanten Quellen.
Den Masterplan nur lesen, wenn die Frage ihn betrifft. Keine breite Projektanalyse.
Keine weiteren Advisor oder Unteragenten konsultieren.
Deine Aufgabe ist PRUEFEN, nicht eigenstaendig umbauen.
Fuehre keine Tests aus und starte/stoppe keine IMC-Prozesse.
Lies, kopiere oder veroeffentliche niemals Credentials oder private Keys.
Kontrolliere Architektur, Datenbank-Cleanup, Lernsystem, Strategie,
Execution-Pfade und riskante Entscheidungen des Haupt-Codex.
Trace Behauptungen bis zu Code, Daten oder Logs.
Gib bei Reviews zuerst ein kurzes Urteil: OK, RISK oder CHANGE.
Antworte moeglichst in hoechstens $($advisorConfig.max_response_words) Woertern,
ohne wesentliche Risiken wegzulassen.
Aendere keine Dateien. Ausschliesslich der Sol-Hauptagent arbeitet schreibend.
"@
codex.cmd -m $advisorConfig.model -c $advisorReasoningConfig -s read-only -a never --search $prompt
