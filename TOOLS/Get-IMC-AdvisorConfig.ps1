$configPath = Join-Path $PSScriptRoot '..\config\imc-advisor.json'
$advisorConfig = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 -ErrorAction Stop |
  ConvertFrom-Json -ErrorAction Stop

if ($advisorConfig.model -isnot [string] -or $advisorConfig.model -ne 'gpt-6-astra') {
  throw "Invalid advisor model in ${configPath}: expected gpt-6-astra."
}
foreach ($advisorField in @('reasoning_effort', 'risk_reasoning_effort')) {
  if ($advisorConfig.$advisorField -isnot [string] -or $advisorConfig.$advisorField -notin @('low', 'medium', 'high')) {
    throw "Invalid ${advisorField} in ${configPath}: expected low, medium or high."
  }
}
if ($advisorConfig.work.model -isnot [string] -or $advisorConfig.work.model -ne 'gpt-6.1-sol') {
  throw "Invalid work model in ${configPath}: expected gpt-6.1-sol."
}
foreach ($workLevel in @('routine', 'standard', 'complex')) {
  if ($advisorConfig.work.$workLevel -isnot [string] -or $advisorConfig.work.$workLevel -notin @('low', 'medium', 'high')) {
    throw "Invalid work.${workLevel} in ${configPath}: expected low, medium or high."
  }
}
if ($advisorConfig.max_response_words -isnot [int] -or $advisorConfig.max_response_words -lt 80 -or $advisorConfig.max_response_words -gt 500) {
  throw "Invalid max_response_words in ${configPath}: expected an integer from 80 to 500."
}

$advisorConfig
