$ErrorActionPreference='Stop';$repo=Split-Path -Parent $PSScriptRoot
. (Join-Path $repo 'TOOLS\GMGN-ReadSupervisorCommands.ps1')
$scope=Join-Path (Join-Path $repo '.local-tools') ('gmgn-read-recovery-'+[guid]::NewGuid().ToString('N'));$null=[IO.Directory]::CreateDirectory($scope)
[IO.File]::WriteAllText((Join-Path $scope 'retire.request.json'),'{}',[Text.UTF8Encoding]::new($false))
$code='';try{Invoke-GmgnStageRetireCommand $null $scope ('a'*64) $null}catch{$code=$_.Exception.Message}
if($code -cne 'GMGN_STAGE_PRODUCTION_HANDOVER_NOT_ADMITTED'){throw 'OWN_PRODUCTION_RETIRE_BLOCK_FAILED'}
if(@(Get-ChildItem -LiteralPath $scope -File).Count -ne 1){throw 'OWN_PRODUCTION_RETIRE_BLOCK_MUTATED'}
[pscustomobject]@{kind='PRODUCTION_RETIRE_NOT_ADMITTED';pass=$true;code=$code;provider_calls=0;process_actions=0}|ConvertTo-Json -Compress
