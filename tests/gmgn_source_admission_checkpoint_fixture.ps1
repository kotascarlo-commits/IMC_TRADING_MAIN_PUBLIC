param([ValidateSet('Normal','BadManifest','UnpinnedManifest','Generation','Schema','FileDigest','DirectoryDigest','Self0','Self1','Self2','Self3','NullSelf','ShortSelf','NullDigests','ShortDigests','SourceAbort','PinAbort','NativeFileAfterCheck','NativeDirectoryAfterCheck','ClaimNormal','ClaimManifest','ClaimGeneration','ClaimSchema','ClaimFiles','ClaimDirectories','ClaimNull','ClaimShort','ClaimAndNative')][string]$Case='Normal')
$ErrorActionPreference='Stop';$repo=Split-Path -Parent $PSScriptRoot;$tools=Join-Path $repo 'TOOLS'
$types=@('GmgnRepairNamespace','GmgnImmutableSources','GmgnReadDirectoryIdentity','GmgnRepairProcessBinding','GmgnSourcePinBinding','GmgnSupervisorSourceAdmission')
Add-Type -Path @($types|ForEach-Object {Join-Path $tools ($_+'.cs')})
$scope=Join-Path (Join-Path $repo '.local-tools') ('source-admission-native-'+[guid]::NewGuid().ToString('N'));$null=[IO.Directory]::CreateDirectory($scope)
$manifest=Join-Path $scope 'package.json';$member=Join-Path $scope 'member.mjs'
[IO.File]::WriteAllText($manifest,'{"own_only":true}');[IO.File]::WriteAllText($member,'// OWN native checkpoint')
$source=[GmgnImmutableSources]::new('%USERPROFILE%','C:\Program Files\nodejs\node.exe',[string[]]@($manifest,$member),[string[]]@((Get-FileHash $manifest).Hash.ToLowerInvariant(),(Get-FileHash $member).Hash.ToLowerInvariant()))
$pins=[GmgnSourcePinBinding]::new($source);$self=[GmgnRepairProcessBinding]::Read([Diagnostics.Process]::GetCurrentProcess());$digests=$pins.ReadDigests();$generation=$source.HashForPath($manifest)
$usedManifest=$manifest;$flags=[Reflection.BindingFlags]'Instance,NonPublic';$code='';$admission=$null
switch($Case){
 'BadManifest'{$usedManifest=$member}
 'UnpinnedManifest'{$directory=Join-Path $scope 'other';$null=[IO.Directory]::CreateDirectory($directory);$usedManifest=Join-Path $directory 'package.json';[IO.File]::WriteAllText($usedManifest,'{}')}
 'Generation'{$generation='0'*64}
 'Schema'{$digests[0]='2'}
 'FileDigest'{$digests[1]='0'*64}
 'DirectoryDigest'{$digests[2]='0'*64}
 'Self0'{$self[0]='0'}
 'Self1'{$self[1]='0'}
 'Self2'{$self[2]='2000-01-01T00:00:00.0000000Z'}
 'Self3'{$self[3]='C:\invalid.exe'}
 'NullSelf'{$self=$null}
 'ShortSelf'{$self=[string[]]@('1')}
 'NullDigests'{$digests=$null}
 'ShortDigests'{$digests=[string[]]@('1')}
}
try{
 $constructor=[GmgnSupervisorSourceAdmission].GetConstructors($flags)[0]
 $arguments=[object[]]::new(5);$arguments[0]=$pins.PSObject.BaseObject;$arguments[1]=[string]$usedManifest;$arguments[2]=[string]$generation;$arguments[3]=[string[]]$self;$arguments[4]=[string[]]$digests
 $admission=$constructor.Invoke($arguments);$admission.Check()
 switch($Case){
  'SourceAbort'{$source.Abort()}
  'PinAbort'{$pins.Abort()}
  'NativeFileAfterCheck'{$entries=$source.GetType().GetField('members',$flags).GetValue($source);$entries[0].GetType().GetField('Identity',$flags).SetValue($entries[0],('0'*16+':'+('0'*32)))}
  'NativeDirectoryAfterCheck'{$directories=$pins.GetType().GetField('directories',$flags).GetValue($pins);$directories[0].GetType().GetField('identity',$flags).SetValue($directories[0],('0'*16+':'+('0'*32)))}
 }
 if($Case -clike 'Claim*'){
  $claims=$admission.GetType().GetField('digests',$flags).GetValue($admission)
  switch($Case){
   'ClaimManifest'{$admission.GetType().GetField('manifest',$flags).SetValue($admission,$member)}
   'ClaimGeneration'{$admission.GetType().GetField('generation',$flags).SetValue($admission,('0'*64))}
   'ClaimSchema'{$claims[0]='2'};'ClaimFiles'{$claims[1]='0'*64};'ClaimDirectories'{$claims[2]='0'*64}
   'ClaimNull'{$admission.GetType().GetField('digests',$flags).SetValue($admission,$null)}
   'ClaimShort'{$admission.GetType().GetField('digests',$flags).SetValue($admission,[string[]]@('1'))}
   'ClaimAndNative'{$claims[1]='0'*64;$source.Abort()}
  }
  $readClaims=$admission.GetType().GetMethod('ReadValidatedClaims',$flags)
  $snapshot=$readClaims.Invoke($admission,[object[]]@($pins.PSObject.BaseObject))
  if($Case -ceq 'ClaimNormal'){$snapshot[1]='CALLER_CHANGE';$next=$readClaims.Invoke($admission,[object[]]@($pins.PSObject.BaseObject));if($next[1] -cne $generation){throw 'OWN_SCALAR_CLAIM_CALLER_MUTATION'}}
 }else{$admission.Check()}
}catch{$e=$_.Exception;while($e.InnerException){$e=$e.InnerException};$code=$e.Message}
$expected=switch($Case){
 'BadManifest'{'GMGN_PIN_BINDING_MANIFEST'};'UnpinnedManifest'{'GMGN_SOURCE_NOT_PINNED'};'Generation'{'GMGN_SOURCE_ADMISSION_GENERATION'}
 'Schema'{'GMGN_SOURCE_ADMISSION_DIGEST'};'FileDigest'{'GMGN_SOURCE_ADMISSION_DIGEST'};'DirectoryDigest'{'GMGN_SOURCE_ADMISSION_DIGEST'}
 {$_ -clike 'Self*'}{'GMGN_SOURCE_ADMISSION_SELF'}
 'NullSelf'{'GMGN_SOURCE_ADMISSION_INPUT'};'ShortSelf'{'GMGN_SOURCE_ADMISSION_INPUT'};'NullDigests'{'GMGN_SOURCE_ADMISSION_INPUT'};'ShortDigests'{'GMGN_SOURCE_ADMISSION_INPUT'}
 'SourceAbort'{'GMGN_SOURCE_LOCKED'};'PinAbort'{'GMGN_PIN_BINDING_FAULT'};'NativeFileAfterCheck'{'GMGN_SOURCE_NATIVE_CHANGED'};'NativeDirectoryAfterCheck'{'GMGN_DIRECTORY_CHANGED'};default{''}
 'ClaimManifest'{'GMGN_PIN_BINDING_MANIFEST'};'ClaimGeneration'{'GMGN_SOURCE_ADMISSION_GENERATION'}
 'ClaimSchema'{'GMGN_SOURCE_ADMISSION_DIGEST'};'ClaimFiles'{'GMGN_SOURCE_ADMISSION_DIGEST'};'ClaimDirectories'{'GMGN_SOURCE_ADMISSION_DIGEST'}
 'ClaimNull'{'GMGN_SOURCE_ADMISSION_INPUT'};'ClaimShort'{'GMGN_SOURCE_ADMISSION_INPUT'};'ClaimAndNative'{'GMGN_SOURCE_LOCKED'}
}
if($code -cne $expected){throw ('OWN_CHECKPOINT_UNEXPECTED_'+$code)}
$shouldFaultPins=$Case -cin @('BadManifest','UnpinnedManifest','SourceAbort','PinAbort','NativeFileAfterCheck','NativeDirectoryAfterCheck','ClaimAndNative')
if($pins.Faulted -ne $shouldFaultPins){throw 'OWN_CHECKPOINT_FAULT_OWNERSHIP'}
if($admission -and $code){
 if(-not $admission.Faulted){throw 'OWN_CHECKPOINT_ADMISSION_NOT_FAULTED'}
 $repeat='';try{$admission.Check()}catch{$e=$_.Exception;while($e.InnerException){$e=$e.InnerException};$repeat=$e.Message}
 if($repeat -cne 'GMGN_SOURCE_ADMISSION_FAULT'){throw 'OWN_CHECKPOINT_REOPENED'}
}
[pscustomobject]@{pass=$true;case=$Case;code=$code;pins_faulted=$pins.Faulted;provider_calls=0;process_actions=0;production_verified=$false}|ConvertTo-Json -Compress
