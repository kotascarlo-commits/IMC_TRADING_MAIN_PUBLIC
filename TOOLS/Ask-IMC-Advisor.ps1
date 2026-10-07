param(
  [Parameter(Position=0, ValueFromRemainingArguments=$true)][string[]]$Question,
  [switch]$RiskReview
)

$ErrorActionPreference = 'Stop'
$advisorConfig = & (Join-Path $PSScriptRoot 'Get-IMC-AdvisorConfig.ps1')
$advisorReasoningEffort = if ($RiskReview) { $advisorConfig.risk_reasoning_effort } else { $advisorConfig.reasoning_effort }
$advisorReasoningConfig = 'model_reasoning_effort="{0}"' -f $advisorReasoningEffort
$project = '%USERPROFILE%\Desktop\IMC_TRADING_MAIN'
if (-not $Question -or $Question.Count -eq 0) {
  $q = Read-Host 'Frage an Astra Advisor'
} else {
  $q = ($Question -join ' ')
}

$prompt = @"
Du bist ausschliesslich der unabhaengige IMC Astra Advisor.
Arbeite READ-ONLY. Aendere keine Dateien und starte/stoppe keine IMC-Prozesse.
Lies AGENTS.md und nur die fuer die konkrete Frage relevanten Quellen.
Den Masterplan nur lesen, wenn die Frage ihn betrifft. Keine breite Projektanalyse.
Du bist selbst der Advisor: keine weiteren Advisor oder Unteragenten konsultieren.
Pruefe Behauptungen anhand von Code, Git, Daten oder Logs statt zu raten.
Gib zuerst: OK, RISK oder CHANGE.
Danach: Befund, Begruendung und konkrete Empfehlung fuer den Sol-Hauptagenten.
Antworte moeglichst in hoechstens $($advisorConfig.max_response_words) Woertern.
Wesentliche Risiken muessen auch bei einer laengeren Antwort vollstaendig benannt werden.

Frage des Hauptagenten/Nutzers:
$q
"@

Write-Host ''
Write-Host ('=== ASK ASTRA ADVISOR | {0} / {1} / READ-ONLY ===' -f $advisorConfig.model, $advisorReasoningEffort.ToUpperInvariant())
# Stdin preserves the multiline prompt across the Windows batch launcher.
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$prompt | codex.cmd --search exec -C $project -m $advisorConfig.model -c $advisorReasoningConfig -s read-only --ephemeral -
exit $LASTEXITCODE
