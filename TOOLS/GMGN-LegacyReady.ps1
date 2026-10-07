# Separate legacy Schema1 contract. Never admits a Schema2 read or any send.
function Assert-GmgnCapturedLegacyReady {
 param([Parameter(Mandatory=$true)][GmgnLegacyCapture]$Capture,
  [Parameter(Mandatory=$true)][GmgnLegacyLifetimePins]$Lifetime,
  [Parameter(Mandatory=$true)][string]$Directory,
  [string]$FixtureScope,[object]$Authentication)
 $ErrorActionPreference='Stop'
 if(-not $Capture.MatchesOwner($Lifetime)){throw 'GMGN_CAPTURE_READY_OWNER_MISMATCH'}
 $repo=Split-Path -Parent $PSScriptRoot
 $scripts=@((Join-Path $repo 'TOOLS\GMGN-ReadBroker.ps1'),(Join-Path $repo 'TOOLS\Start-IMC-Work.ps1'))
 if($FixtureScope){
  $fixture=[IO.Path]::GetFullPath($FixtureScope)
  if($fixture -cnotmatch '\\[.]local-tools\\legacy-capture-native-[0-9a-f]{32}$' -or
   $Directory -cne (Join-Path $fixture 'legacy')){throw 'GMGN_CAPTURE_READY_FIXTURE_SCOPE'}
  $scripts=@((Join-Path $fixture 'broker.ps1'),(Join-Path $fixture 'parent.ps1'))
 }elseif($Directory -cne '%USERPROFILE%\AppData\Local\Temp\imc-gmgn-broker-3904' -or
  $Lifetime.BrokerPid -ne 14552 -or $Lifetime.ParentPid -ne 3904 -or
  $Lifetime.BrokerBirth -cne '2026-10-02T15:14:56.6594833Z' -or $Lifetime.ParentBirth -cne '2026-10-02T15:14:56.3325126Z'){
  throw 'GMGN_CAPTURE_READY_PRODUCTION_ROLE'
 }
 $Lifetime.CheckAlive()
 try{$envelope=$Capture.ReadReady().TrimStart([char]0xfeff)|ConvertFrom-Json}catch{throw 'GMGN_CAPTURE_READY_JSON'}
 if(@($envelope.PSObject.Properties).Count -ne 2 -or
  @($envelope.PSObject.Properties.Name|Where-Object {$_ -cnotin @('body','mac')}).Count -or
  $envelope.body -isnot [string] -or $envelope.mac -isnot [string] -or
  $envelope.mac -cnotmatch '\A[0-9a-f]{64}\z'){throw 'GMGN_CAPTURE_READY_ENVELOPE'}
 # Only inherited local IPC authentication; no GMGN credential/config access.
 if($PSBoundParameters.ContainsKey('Authentication')){
  $expectedType=$Lifetime.GetType().Assembly.GetType('GmgnReadLocalAuth',$false,$false)
  if($null -eq $Authentication -or $null -eq $expectedType -or -not [object]::ReferenceEquals($Authentication.GetType(),$expectedType)){throw 'GMGN_CAPTURE_READY_AUTH_TYPE'}
  $Authentication.Check()
  if(-not $Authentication.Verify($envelope.body,$envelope.mac)){throw 'GMGN_CAPTURE_READY_AUTH'}
  $Authentication.Check()
 }else{
  $token=$env:IMC_GMGN_IPC_TOKEN
  if($token -cnotmatch '\A[0-9a-f]{64}\z'){throw 'GMGN_CAPTURE_READY_AUTH_UNAVAILABLE'}
  $hmac=[Security.Cryptography.HMACSHA256]::new([Text.Encoding]::ASCII.GetBytes($token))
  try{$actual=$hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes($envelope.body))}finally{$hmac.Dispose()}
  $difference=0;foreach($i in 0..31){$difference=$difference -bor ($actual[$i] -bxor [Convert]::ToByte($envelope.mac.Substring($i*2,2),16))}
  if($difference -ne 0){throw 'GMGN_CAPTURE_READY_AUTH'}
 }
 try{$ready=$envelope.body|ConvertFrom-Json}catch{throw 'GMGN_CAPTURE_READY_BODY'}
 $fields=@('schema_version','instance_id','directory','broker','parent')
 if(@($ready.PSObject.Properties).Count -ne 5 -or @($ready.PSObject.Properties.Name|Where-Object {$_ -cnotin $fields}).Count -or
  $ready.schema_version -isnot [int] -or $ready.schema_version -ne 1 -or
  $ready.instance_id -isnot [string] -or $ready.instance_id -cnotmatch '\A[0-9a-f]{32}\z' -or
  $ready.directory -isnot [string] -or $ready.directory -cne $Directory){throw 'GMGN_CAPTURE_READY_SCHEMA'}
 $bindings=$Lifetime.Bindings()
 foreach($index in 0..1){
  $role=@('broker','parent')[$index];$binding=$ready.$role;$native=$bindings[$index]
  $fields=@('pid','parent_pid','created_at','executable','script')
  if(@($binding.PSObject.Properties).Count -ne 5 -or @($binding.PSObject.Properties.Name|Where-Object {$_ -cnotin $fields}).Count -or
   $binding.pid -isnot [int] -or $binding.parent_pid -isnot [int] -or
   $binding.pid -ne [int]$native[0] -or $binding.parent_pid -ne [int]$native[1] -or
   $binding.executable -isnot [string] -or $binding.executable -ine $native[3] -or
   $binding.script -isnot [string] -or $binding.script -cne $scripts[$index] -or $binding.created_at -isnot [string]){throw 'GMGN_CAPTURE_READY_NATIVE_ROLE'}
  $date=[DateTimeOffset]::MinValue
  if(-not [DateTimeOffset]::TryParseExact($binding.created_at,'o',[Globalization.CultureInfo]::InvariantCulture,[Globalization.DateTimeStyles]::None,[ref]$date) -or
   [math]::Abs(($date.UtcDateTime-[DateTime]::Parse($native[2]).ToUniversalTime()).Ticks) -ge 10){throw 'GMGN_CAPTURE_READY_NATIVE_BIRTH'}
 }
 $Lifetime.CheckAlive();$Capture.BindValidatedReady($Capture.ReadySha256)
 return $ready.instance_id
}
