# This entry must itself be read through a retained READ/share-READ stream,
# checked against the reviewed hash, then parsed/invoked from those bytes.
# It never reads SDK configuration or credential files.
param([string]$ManifestPath,[ValidatePattern('\A[0-9a-f]{64}\z')][string]$ExpectedGenerationSha256,[string]$FixtureScope)
$ErrorActionPreference='Stop'
if($FixtureScope -and $FixtureScope -cnotmatch '\AC:\\Users\\<local-user>\\Desktop\\IMC_TRADING_MAIN\\[.]local-tools\\legacy-capture-native-[0-9a-f]{32}\z'){throw 'GMGN_TRUSTED_FIXTURE_SCOPE'}
function Test-GmgnTrustedPublicConfig([string]$Path){
 foreach($name in @('imc-gmgn-read-broker.json','imc-execution.json')){
  if($Path -ceq ('%USERPROFILE%\Desktop\IMC_TRADING_MAIN\config\'+$name) -or
   ($FixtureScope -and $Path -ceq (Join-Path (Join-Path $FixtureScope 'config') $name))){return $true}
 };return $false
}
if(-not $global:IMCGmgnTrustedSnapshots){$global:IMCGmgnTrustedSnapshots=[Collections.Generic.List[object]]::new()}
$snapshot=[pscustomobject]@{ManifestPath=$ManifestPath;ManifestHash=$ExpectedGenerationSha256;Files=$null;
 Streams=[Collections.Generic.List[object]]::new();Bytes=@{};Faulted=$false;Complete=$false}
$global:IMCGmgnTrustedSnapshots.Add($snapshot)
function Read-TrustedPublicBytes([string]$Path,[string]$Expected){
 $canonical=[IO.Path]::GetFullPath($Path)
 if($canonical -cne $Path -or ($canonical -ine 'C:\Program Files\nodejs\node.exe' -and
  -not $canonical.StartsWith('%USERPROFILE%\',[StringComparison]::OrdinalIgnoreCase)) -or
  ($canonical -ine 'C:\Program Files\nodejs\node.exe' -and
   [IO.Path]::GetExtension($canonical) -notin @('.cs','.ps1','.js','.mjs','.cjs') -and [IO.Path]::GetFileName($canonical) -cne 'package.json' -and -not (Test-GmgnTrustedPublicConfig $canonical))){throw 'GMGN_TRUSTED_PUBLIC_PATH'}
 $ancestor=$canonical
 while($ancestor){
  if(([IO.File]::GetAttributes($ancestor) -band [IO.FileAttributes]::ReparsePoint) -ne 0){throw 'GMGN_TRUSTED_REPARSE_PATH'}
  $ancestor=[IO.Path]::GetDirectoryName($ancestor)
 }
 $stream=[IO.FileStream]::new($canonical,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
 $snapshot.Streams.Add($stream)
 $sha=[Security.Cryptography.SHA256]::Create()
 try{$hash=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
 if($hash -cne $Expected){throw 'GMGN_TRUSTED_GENERATION_CHANGED'}
 if([IO.Path]::GetExtension($canonical) -cin @('.ps1','.cs') -or [IO.Path]::GetFileName($canonical) -ceq 'package.json' -or (Test-GmgnTrustedPublicConfig $canonical)){
  if($stream.Length -gt 2097152){throw 'GMGN_TRUSTED_SOURCE_TOO_LARGE'}
  $stream.Position=0;$buffer=[byte[]]::new([int]$stream.Length);$offset=0
  while($offset -lt $buffer.Length){$n=$stream.Read($buffer,$offset,$buffer.Length-$offset);if($n -le 0){throw 'GMGN_TRUSTED_SOURCE_SHORT_READ'};$offset+=$n}
  $snapshot.Bytes.Add($canonical,$buffer)
 }
}
try{
 Read-TrustedPublicBytes $ManifestPath $ExpectedGenerationSha256
 $manifest=[Text.UTF8Encoding]::new($false,$true).GetString($snapshot.Bytes[$ManifestPath])|ConvertFrom-Json
 if($manifest.schema_version -ne 1 -or $manifest.sdk_version -cne '1.6.6' -or $manifest.files.Count -lt 169 -or $manifest.files.Count -gt 2048){throw 'GMGN_TRUSTED_MANIFEST'}
 $snapshot.Files=$manifest.files
 $seen=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
 foreach($file in $snapshot.Files){
  if(-not $seen.Add([string]$file.path) -or $file.sha256 -cnotmatch '\A[0-9a-f]{64}\z'){throw 'GMGN_TRUSTED_MANIFEST_MEMBER'}
  Read-TrustedPublicBytes $file.path $file.sha256
 }
 $snapshot.Complete=$true
 $snapshot
}catch{$snapshot.Faulted=$true;throw}
