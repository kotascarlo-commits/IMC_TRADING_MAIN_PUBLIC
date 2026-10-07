param([Parameter(Mandatory=$true)][string]$ApprovedSdkManifestPath,[string]$FixtureScope,[string]$WarmActionChallenge,[string]$WarmHandoff,[string]$WarmLegacyFixture)
$ErrorActionPreference='Stop';$repo=Split-Path -Parent $PSScriptRoot
$approved=Get-Content -LiteralPath $ApprovedSdkManifestPath -Raw|ConvertFrom-Json
$sdk='%USERPROFILE%\AppData\Roaming\npm\node_modules\gmgn-cli\'
$base=@($approved.files|Where-Object {$_.path.StartsWith($sdk,[StringComparison]::OrdinalIgnoreCase) -or $_.path -ieq 'C:\Program Files\nodejs\node.exe' -or
 $_.path -cin @((Join-Path $repo 'TOOLS\GMGN-ReadCliBootstrap.mjs'),(Join-Path $repo 'TOOLS\GMGN-SignedQuoteProbe.mjs'),(Join-Path $repo 'TOOLS\GMGN-SingleSwapTransport.mjs'))})
if($base.Count -ne 169){throw 'GMGN_RECOVERY_BASE_INVENTORY'}
$files=[Collections.Generic.List[object]]::new()
foreach($file in $base){if((Get-FileHash -LiteralPath $file.path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $file.sha256){throw 'GMGN_RECOVERY_SDK_CHANGED'};$files.Add($file)}
foreach($name in @('GmgnRepairNamespace.cs','GmgnImmutableSources.cs','GmgnRepairProcessBinding.cs','GmgnLegacyCapture.cs','GmgnLegacyLifetimePins.cs','GmgnReadDirectoryIdentity.cs','GmgnReadProcessLifetime.cs','GmgnLegacyRetirement.cs','GmgnSupervisorReadContext.cs','GmgnReadClientIdentity.cs','GMGN-ReadClientBootstrap.ps1','GMGN-TrustedGeneration.ps1','GMGN-LegacyReady.ps1','GMGN-ReadSupervisorCommands.ps1','GMGN-SupervisorTask.ps1','GmgnScheduledActionIdentity.cs','GmgnSupervisorPipe.cs','GmgnSourcePinBinding.cs','GmgnPinTransferHandshake.cs','GmgnSupervisorSourceAdmission.cs','GmgnReadLocalAuth.cs','GmgnHandoffCompletion.cs','GmgnReadProofPin.cs')){
 $path=Join-Path $PSScriptRoot $name;$files.Add(@{path=$path;sha256=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()})
}
$planSource=Join-Path $PSScriptRoot 'GmgnReadInstallPlan.cs';$files.Add(@{path=$planSource;sha256=(Get-FileHash -LiteralPath $planSource -Algorithm SHA256).Hash.ToLowerInvariant()})
$authSource=Join-Path $PSScriptRoot 'GmgnLegacyRetirement.Auth.cs';$files.Add(@{path=$authSource;sha256=(Get-FileHash -LiteralPath $authSource -Algorithm SHA256).Hash.ToLowerInvariant()})
$warmSource=Join-Path $PSScriptRoot 'GmgnReadInstallPlan.Warm.cs';$files.Add(@{path=$warmSource;sha256=(Get-FileHash -LiteralPath $warmSource -Algorithm SHA256).Hash.ToLowerInvariant()})
$rootSource=Join-Path $PSScriptRoot 'GmgnReadInstallPlan.Root.cs';$files.Add(@{path=$rootSource;sha256=(Get-FileHash -LiteralPath $rootSource -Algorithm SHA256).Hash.ToLowerInvariant()})
$legacySource=Join-Path $PSScriptRoot 'GmgnReadInstallPlan.Legacy.cs';$files.Add(@{path=$legacySource;sha256=(Get-FileHash -LiteralPath $legacySource -Algorithm SHA256).Hash.ToLowerInvariant()})
$ownerSource=Join-Path $PSScriptRoot 'GmgnReadOwnerProof.cs';$files.Add(@{path=$ownerSource;sha256=(Get-FileHash -LiteralPath $ownerSource -Algorithm SHA256).Hash.ToLowerInvariant()})
$senderSource=Join-Path $PSScriptRoot 'GmgnReadInstallPlan.Sender.cs';$files.Add(@{path=$senderSource;sha256=(Get-FileHash -LiteralPath $senderSource -Algorithm SHA256).Hash.ToLowerInvariant()})
$retireSource=Join-Path $PSScriptRoot 'GmgnReadInstallPlan.Retire.cs';$files.Add(@{path=$retireSource;sha256=(Get-FileHash -LiteralPath $retireSource -Algorithm SHA256).Hash.ToLowerInvariant()})
$self=Join-Path $PSScriptRoot 'GMGN-ReadSupervisor.ps1';$selfHash=(Get-FileHash -LiteralPath $self -Algorithm SHA256).Hash.ToLowerInvariant();$files.Add(@{path=$self;sha256=$selfHash})
$inventory=[string[]]@('broker.ready','broker.last_error')
if($FixtureScope){if($FixtureScope -cnotmatch '\\[.]local-tools\\legacy-capture-native-[0-9a-f]{32}$'){throw 'GMGN_RECOVERY_FIXTURE_SCOPE'};$inventory=[string[]]@('broker.ready',(('a'*32)+'.response.json'))}
$directory=Join-Path (Join-Path $repo '.local-tools') ('gmgn-recovery-generation-'+[guid]::NewGuid().ToString('N'));$null=[IO.Directory]::CreateDirectory($directory)
$path=Join-Path $directory 'package.json'
$manifest=@{name='imc-independent-read-recovery-generation';schema_version=1;sdk_version='1.6.6';stage_contract='BOUNDED_READ_RECOVERY_WAIT_CODE';primary_code_included=$false;capture_inventory=$inventory;files=@($files)}
if($PSBoundParameters.ContainsKey('WarmActionChallenge')){
 if($WarmActionChallenge -cnotmatch '\A[0-9a-f]{64}\z'){throw 'GMGN_RECOVERY_WARM_CHALLENGE'}
 $manifest.warm_action_challenge=$WarmActionChallenge
}
if($PSBoundParameters.ContainsKey('WarmHandoff')){
 if(-not $PSBoundParameters.ContainsKey('WarmActionChallenge') -or -not $WarmHandoff -or $WarmHandoff.Length -gt 32768){throw 'GMGN_RECOVERY_WARM_HANDOFF'}
 # The native plan admits only an exact canonical contract from this pinned S1.
 $manifest.warm_handoff=$WarmHandoff
}
if($PSBoundParameters.ContainsKey('WarmLegacyFixture')){
 if(-not $PSBoundParameters.ContainsKey('WarmHandoff') -or -not $PSBoundParameters.ContainsKey('WarmActionChallenge') -or
  $FixtureScope -cnotmatch '\AC:\\Users\\<local-user>\\Desktop\\IMC_TRADING_MAIN\\[.]local-tools\\legacy-capture-native-[0-9a-f]{32}\z' -or
  -not $WarmLegacyFixture -or $WarmLegacyFixture.Length -gt 32768){throw 'GMGN_RECOVERY_WARM_LEGACY_FIXTURE'}
 $parser=[Web.Script.Serialization.JavaScriptSerializer]::new();$parser.MaxJsonLength=32768;$parser.RecursionLimit=4
 try{$contract=$parser.DeserializeObject($WarmLegacyFixture)}catch{throw 'GMGN_RECOVERY_WARM_LEGACY_FIXTURE'}
 if($contract -isnot [Collections.Generic.Dictionary[string,object]] -or $contract.Count -ne 4){throw 'GMGN_RECOVERY_WARM_LEGACY_FIXTURE'}
 $canonical=[Collections.Generic.SortedDictionary[string,string]]::new([StringComparer]::Ordinal)
 foreach($key in @('broker_native','parent_native','scope','source_directory')){
  if(-not $contract.ContainsKey($key) -or $contract[$key] -isnot [string] -or -not $contract[$key]){throw 'GMGN_RECOVERY_WARM_LEGACY_FIXTURE'}
  $canonical.Add($key,$contract[$key])
 }
 if($parser.Serialize($canonical) -cne $WarmLegacyFixture -or $canonical['scope'] -cne $FixtureScope -or
  [IO.Path]::GetFullPath($FixtureScope) -cne $FixtureScope -or $canonical['source_directory'] -cne (Join-Path $FixtureScope 'legacy')){throw 'GMGN_RECOVERY_WARM_LEGACY_FIXTURE'}
 foreach($key in @('broker_native','parent_native')){
  try{$native=$parser.DeserializeObject($canonical[$key])}catch{throw 'GMGN_RECOVERY_WARM_LEGACY_FIXTURE'}
  if($native -isnot [object[]] -or $native.Count -ne 4 -or @($native|Where-Object {$_ -isnot [string] -or -not $_}).Count -or
   $parser.Serialize($native) -cne $canonical[$key]){throw 'GMGN_RECOVERY_WARM_LEGACY_FIXTURE'}
 }
 $manifest.warm_legacy_fixture=$WarmLegacyFixture
}
[IO.File]::WriteAllText($path,($manifest|ConvertTo-Json -Depth 6),[Text.UTF8Encoding]::new($false))
[pscustomobject]@{manifest=$path;sha256=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant();self_sha256=$selfHash;files=$files.Count}
