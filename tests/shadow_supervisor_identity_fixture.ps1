# All native/process functions in the existing fixture are isolated mocks.
. (Join-Path $PSScriptRoot 'test_learning_process_identity.ps1')
foreach($seconds in @('300','28800','43200','0','43201','-1','300.0','0300')){
 New-Fixture
 $script:supervisor.CommandLine=Line $shell @('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',$runner,'-ShadowDurationSeconds',$seconds)
 $valid=Test-IMCTransientLearningProcess $mlVenv $rows
 if($valid -ne ($seconds -in @('300','28800','43200'))){throw ('Bounded ancestry mismatch: '+$seconds)}
 Assert-Disposed
}
foreach($tail in @(@('-ShadowDurationSeconds','300','-ShadowDataOnly'),@('-ShadowDataOnly'),@('-ShadowDurationSeconds','0','-ShadowDataOnly'),@('-ShadowDurationSeconds','300','-Unexpected'))){
 New-Fixture
 $script:supervisor.CommandLine=Line $shell (@('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',$runner)+$tail)
 $valid=Test-IMCTransientLearningProcess $mlVenv $rows
 if($valid -ne ($tail.Count -eq 3 -and $tail[1] -ceq '300' -and $tail[2] -ceq '-ShadowDataOnly')){throw 'Data-only ancestry mismatch'}
 Assert-Disposed
}
foreach($budget in @('1','2147483648','6442450944','0','6442450945','06442450944','6.0','-1','9223372036854775808')){
 New-Fixture
 $script:supervisor.CommandLine=Line $shell @('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',$runner,'-ShadowDurationSeconds','40000','-ShadowDataOnly','-ShadowMaxStorageGrowthBytes',$budget,'-ShadowStorageBaselineBytes','42050474056')
 if((Test-IMCTransientLearningProcess $mlVenv $rows) -ne ($budget -in @('1','2147483648','6442450944'))){throw ('Budget ancestry mismatch: '+$budget)}
 Assert-Disposed
}
foreach($baseline in @('0','42050474056','9223372036854775807','-1','-2','042050474056','42050474056.0','9223372036854775808')){
 New-Fixture
 $script:supervisor.CommandLine=Line $shell @('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',$runner,'-ShadowDurationSeconds','40000','-ShadowDataOnly','-ShadowMaxStorageGrowthBytes','6442450944','-ShadowStorageBaselineBytes',$baseline)
 if((Test-IMCTransientLearningProcess $mlVenv $rows) -ne ($baseline -in @('0','42050474056','9223372036854775807'))){throw ('Baseline ancestry mismatch: '+$baseline)}
 Assert-Disposed
}
foreach($switch in @('-Unexpected','-ShadowDataOnly')){
 New-Fixture
 $script:supervisor.CommandLine=Line $shell @('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',$runner,'-ShadowDurationSeconds','40000',$switch,'-ShadowMaxStorageGrowthBytes','6442450944','-Unexpected','42050474056')
 if(Test-IMCTransientLearningProcess $mlVenv $rows){throw 'Unexpected budget switch accepted'}
 Assert-Disposed
}
'SHADOW_SUPERVISOR_IDENTITY_PASS'
