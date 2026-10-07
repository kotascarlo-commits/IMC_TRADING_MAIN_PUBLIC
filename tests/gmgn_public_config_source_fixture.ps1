param([ValidateSet('Allowed','WrongScope','OtherConfig','Basename','InvalidScope')][string]$Case='Allowed')
$ErrorActionPreference='Stop';$repo=Split-Path -Parent $PSScriptRoot;$tools=Join-Path $repo 'TOOLS'
Add-Type -Path @((Join-Path $tools 'GmgnRepairNamespace.cs'),(Join-Path $tools 'GmgnImmutableSources.cs'))
$scope=Join-Path (Join-Path $repo '.local-tools') ('legacy-capture-native-'+[guid]::NewGuid().ToString('N'));$directory=Join-Path $scope 'config';$null=[IO.Directory]::CreateDirectory($directory)
$file=Join-Path $directory 'imc-gmgn-read-broker.json';$approved=$scope
switch -CaseSensitive($Case){
 'WrongScope'{$approved=Join-Path (Join-Path $repo '.local-tools') ('legacy-capture-native-'+[guid]::NewGuid().ToString('N'))}
 'OtherConfig'{$file=Join-Path $directory 'gmgn-credentials.json'}
 'Basename'{$file=Join-Path $scope 'imc-gmgn-read-broker.json'}
 'InvalidScope'{$approved=$directory}
}
# Every file here is an own dummy; no credential file is read or copied.
[IO.File]::WriteAllText($file,'{"own_public_fixture":true}',[Text.UTF8Encoding]::new($false))
$hash=(Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant();$sources=$null;$code=''
try{$sources=[GmgnImmutableSources]::new('%USERPROFILE%','C:\Program Files\nodejs\node.exe',[string[]]@($file),[string[]]@($hash),$approved)}catch{$cause=$_.Exception;while($cause.InnerException){$cause=$cause.InnerException};$code=$cause.Message}
if($Case -ceq 'Allowed'){
 if(-not $sources -or $sources.ReadText($file) -cne '{"own_public_fixture":true}'){throw 'OWN_CONFIG_ALLOWED_FAILED'}
}else{
 $expected=if($Case -ceq 'InvalidScope'){'GMGN_SOURCE_FIXTURE_SCOPE'}else{'GMGN_SOURCE_PATH_OR_HASH'}
 if($sources -or $code -cne $expected){throw ('OWN_CONFIG_NOT_REJECTED_'+$code)}
}
[pscustomobject]@{kind='PUBLIC_CONFIG_SOURCE_ALLOWLIST';case=$Case;pass=$true;code=$code;provider_calls=0;production_verified=$false;source_sha256=(Get-FileHash -LiteralPath (Join-Path $tools 'GmgnImmutableSources.cs')).Hash.ToLowerInvariant()}|ConvertTo-Json -Compress
