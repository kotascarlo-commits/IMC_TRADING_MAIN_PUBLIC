param([ValidateSet('Normal','Fixture','Expired','Future','Frequency','Schema','LoadedHash','Frame','WrongRole','ExtraField','WrongType','Exact15','Over15','ClockFuture')][string]$Case='Normal')
$ErrorActionPreference='Stop';$repo=Split-Path -Parent $PSScriptRoot
. (Join-Path $repo 'TOOLS\GMGN-ReadSupervisorCommands.ps1')
$frequency=[Diagnostics.Stopwatch]::Frequency;$now=[Diagnostics.Stopwatch]::GetTimestamp()
$bindings=@(@('14552','3904','2026-10-02T15:14:56.6594833Z','OWN_EXE'),@('3904','4176','2026-10-02T15:14:56.3325126Z','OWN_EXE'))
$body=[pscustomobject]@{schema_version=1;kind='ORIGINAL_OWNER_RETIREMENT_EVIDENCE';owner_pid=3904;owner_birth=$bindings[1][2];broker_pid=14552;broker_birth=$bindings[0][2];broker_directory='%USERPROFILE%\AppData\Local\Temp\imc-gmgn-broker-3904';loaded_source_sha256='2243b01e0c24c60aa3bd6906486424a66007d33e063100fe949968ec01500ed6';loaded_cleanup_sha256='b16f53d6cc9d6e679c11d210196a4a84b945e419753c383d6ec67be5a5d6be50';original_script=(Join-Path $repo 'TOOLS\Start-IMC-Work.ps1');original_line=215;original_broker_handle_verified=$true;variables_changed=$false;provider_calls=0;observed_qpc=$now.ToString();qpc_frequency=$frequency.ToString()}
$fixture=$null;$code=''
switch($Case){
 'Fixture'{$fixture=Join-Path (Join-Path $repo '.local-tools') ('legacy-capture-native-'+('a'*32));$body.original_script=Join-Path $fixture 'parent.ps1';$body.broker_directory=Join-Path $fixture 'legacy';$body.loaded_source_sha256='a'*64;$body.original_line=1}
 'Expired'{$body.observed_qpc=($now-16*$frequency).ToString()}
 'Future'{$body.observed_qpc=($now+30*$frequency).ToString()}
 'Frequency'{$body.qpc_frequency=($frequency+1).ToString()}
 'Schema'{$body.schema_version=2}
 'LoadedHash'{$body.loaded_source_sha256='a'*64}
 'Frame'{$body.original_line=214}
 'WrongRole'{$body.owner_pid=3905}
 'ExtraField'{$body|Add-Member ignored 'UNEXPECTED'}
 'WrongType'{$body.original_broker_handle_verified='true'}
 'Exact15'{$body.observed_qpc=($now-15*$frequency).ToString()}
 'Over15'{$body.observed_qpc=($now-15*$frequency-1).ToString()}
 'ClockFuture'{$body.observed_qpc=($now+1).ToString()}
}
try{
 $result=if($Case -cin @('Exact15','Over15','ClockFuture')){Assert-GmgnOwnerProofFreshness $body $now}else{Assert-GmgnStageOwnerProofBody $body $bindings $fixture}
 if($result.Observed -ne [long]$body.observed_qpc -or $result.Frequency -ne $frequency){throw 'OWN_OWNER_CLOCK_NOT_RETURNED'}
}catch{$code=$_.Exception.Message}
$expected=switch($Case){'Expired'{'GMGN_STAGE_OWNER_PROOF_STALE'};'Future'{'GMGN_STAGE_OWNER_PROOF_STALE'};'Frequency'{'GMGN_STAGE_OWNER_PROOF_STALE'};'Over15'{'GMGN_STAGE_OWNER_PROOF_STALE'};'ClockFuture'{'GMGN_STAGE_OWNER_PROOF_STALE'};'Schema'{'GMGN_STAGE_OWNER_PROOF_BINDING'};'WrongRole'{'GMGN_STAGE_OWNER_PROOF_BINDING'};'WrongType'{'GMGN_STAGE_OWNER_PROOF_BINDING'};'LoadedHash'{'GMGN_STAGE_OWNER_LOADED_SOURCE'};'Frame'{'GMGN_STAGE_OWNER_LOADED_SOURCE'};'ExtraField'{'GMGN_STAGE_COMMAND_FIELDS'};default{''}}
if($code -cne $expected){throw ('OWN_OWNER_PROOF_WRONG_CODE_'+$code)}
[pscustomobject]@{pass=$true;case=$Case;code=$code;process_actions=0;provider_calls=0;production_verified=$false}|ConvertTo-Json -Compress
