param([ValidateSet('Normal','ReverseOrder','SourceFault','BindingFault','NativeFileIds','Culture','Duplicate')][string]$Case='Normal')
$ErrorActionPreference='Stop';$repo=Split-Path -Parent $PSScriptRoot;$tools=Join-Path $repo 'TOOLS'
Add-Type -Path @((Join-Path $tools 'GmgnRepairNamespace.cs'),(Join-Path $tools 'GmgnImmutableSources.cs'),(Join-Path $tools 'GmgnReadDirectoryIdentity.cs'),(Join-Path $tools 'GmgnSourcePinBinding.cs'))
$scope=Join-Path (Join-Path $repo '.local-tools') ('pin-binding-native-'+[guid]::NewGuid().ToString('N'));$null=[IO.Directory]::CreateDirectory($scope)
$a=Join-Path $scope 'a.mjs';$b=Join-Path $scope 'b.mjs'
[IO.File]::WriteAllText($a,'// OWN identical public bytes.');[IO.File]::WriteAllText($b,'// OWN identical public bytes.')
$paths=[string[]]@($a,$b);$hashes=[string[]]@($paths|ForEach-Object {(Get-FileHash -LiteralPath $_).Hash.ToLowerInvariant()})
$source=[GmgnImmutableSources]::new('%USERPROFILE%','C:\Program Files\nodejs\node.exe',$paths,$hashes)
$binding=[GmgnSourcePinBinding]::new($source);$original=$binding.ReadDigests();$code=''
switch($Case){
 'Normal'{
  $v=$binding.ReadNativeVectors();$snapshot=$v[0]|ConvertFrom-Json
  if($snapshot.schema_version -ne 1 -or $snapshot.files.Count -ne 2 -or $snapshot.namespace_leaves.Count -ne 1){throw 'OWN_PIN_BINDING_VECTOR_SHAPE'}
  if($snapshot.files[0].native_identity -ceq $snapshot.files[1].native_identity -or $snapshot.files[0].sha256 -cne $snapshot.files[1].sha256){throw 'OWN_PIN_BINDING_NATIVE_IDS_MISSING'}
 }
 'ReverseOrder'{
  $reversed=[GmgnImmutableSources]::new('%USERPROFILE%','C:\Program Files\nodejs\node.exe',[string[]]@($b,$a),[string[]]@($hashes[1],$hashes[0]))
  $other=[GmgnSourcePinBinding]::new($reversed)
  if(($binding.ReadNativeVectors() -join "`n") -cne ($other.ReadNativeVectors() -join "`n")){throw 'OWN_PIN_BINDING_ORDER_DEPENDENT'}
 }
 'NativeFileIds'{
  $source.GetType().GetField('members',[Reflection.BindingFlags]'Instance,NonPublic').GetValue($source)|ForEach-Object {
   $id=$_.GetType().GetField('Identity',[Reflection.BindingFlags]'Instance,NonPublic').GetValue($_)
   if($id -cnotmatch '\A[0-9a-f]{16}:[0-9a-f]{32}\z'){throw 'OWN_PIN_BINDING_FILE_ID_FORMAT'}
  }
 }
 'Culture'{
  [Threading.Thread]::CurrentThread.CurrentCulture=[Globalization.CultureInfo]::GetCultureInfo('de-DE')
  if(($binding.ReadDigests() -join ':') -cne ($original -join ':')){throw 'OWN_PIN_BINDING_CULTURE_DEPENDENT'}
 }
 'Duplicate'{
  try{$null=[GmgnImmutableSources]::new('%USERPROFILE%','C:\Program Files\nodejs\node.exe',[string[]]@($a,$a),[string[]]@($hashes[0],$hashes[0]))}catch{$e=$_.Exception;while($e.InnerException){$e=$e.InnerException};$code=$e.Message}
  if($code -cne 'GMGN_SOURCE_PATH_OR_HASH'){throw 'OWN_PIN_BINDING_DUPLICATE_ADMITTED'}
 }
 default{
  if($Case -ceq 'SourceFault'){$source.Abort()}else{$binding.Abort()}
  try{$null=$binding.ReadDigests()}catch{$e=$_.Exception;while($e.InnerException){$e=$e.InnerException};$code=$e.Message}
  $expected=if($Case -ceq 'SourceFault'){'GMGN_SOURCE_LOCKED'}else{'GMGN_PIN_BINDING_FAULT'}
  if($code -cne $expected -or -not $binding.Faulted){throw 'OWN_PIN_BINDING_FAULT_MASKED'}
  $repeat='';try{$binding.Check()}catch{$e=$_.Exception;while($e.InnerException){$e=$e.InnerException};$repeat=$e.Message}
  if($repeat -cne 'GMGN_PIN_BINDING_FAULT'){throw 'OWN_PIN_BINDING_REOPENED'}
 }
}
[pscustomobject]@{kind='NATIVE_SOURCE_PIN_BINDING';case=$Case;pass=$true;code=$code;provider_calls=0;production_verified=$false;source_sha256=(Get-FileHash -LiteralPath (Join-Path $tools 'GmgnImmutableSources.cs')).Hash.ToLowerInvariant();binding_sha256=(Get-FileHash -LiteralPath (Join-Path $tools 'GmgnSourcePinBinding.cs')).Hash.ToLowerInvariant()}|ConvertTo-Json -Compress
