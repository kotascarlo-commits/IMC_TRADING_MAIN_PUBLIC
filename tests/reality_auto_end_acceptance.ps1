$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
. (Join-Path $root 'TOOLS\IMC_T_Command.ps1')
$python=Join-Path $root '.venv\Scripts\python.exe'
$entry=Join-Path $root 'RUNTIME\reality_runtime.py'
imc t start 1m
$first=& $python $entry status | ConvertFrom-Json
$deadline=[DateTime]::UtcNow.AddSeconds(85)
do {
    $s=& $python $entry status | ConvertFrom-Json
    if($s.status -eq 'COMPLETED' -and $s.cleanup_confirmed){break}
    Start-Sleep -Milliseconds 500
}while([DateTime]::UtcNow -lt $deadline)
if($s.run_id -ne $first.run_id -or $s.stop_reason -ne 'DURATION_ELAPSED' -or $s.status -ne 'COMPLETED' -or
    -not $s.cleanup_confirmed -or $s.remaining_seconds -ne 0 -or $s.open_position -or $s.actual_duration_seconds -lt 60){
    & $python $entry stop | Out-Null
    throw 'REALITY_AUTO_END_OR_CLEANUP_FAILED'
}
$r=@{status='PASS';passed=5;failed=0;skipped=0;run_id=$s.run_id;duration='1m';actual_seconds=$s.actual_duration_seconds;
    stop_reason=$s.stop_reason;cleanup_confirmed=$s.cleanup_confirmed;remaining_seconds=$s.remaining_seconds;
    evidence='NATIVE_REAL_CLOCK_CONTROL_ONLY';broadcast=$false;signing=$false}
[IO.File]::WriteAllText((Join-Path $root 'RUNTIME\reality-native-auto-end.json'),($r|ConvertTo-Json),[Text.UTF8Encoding]::new($false))
$r|ConvertTo-Json
