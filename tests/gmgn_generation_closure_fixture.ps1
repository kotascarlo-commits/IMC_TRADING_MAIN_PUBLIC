param([ValidateSet('Normal','ProductionWarmFallback')][string]$Case='Normal')
$ErrorActionPreference='Stop';$repo=Split-Path -Parent $PSScriptRoot;$tools=Join-Path $repo 'TOOLS'
$approved=Join-Path (Join-Path $repo '.local-tools') 'gmgn-approved-public-generation-23\package.json'
$generation=& (Join-Path $tools 'New-GMGN-ReadGeneration.ps1') -ApprovedSdkManifestPath $approved
$bootstrap=Join-Path $tools 'GMGN-TrustedGeneration.ps1'
$pin=[IO.FileStream]::new($bootstrap,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
$reader=[IO.StreamReader]::new($pin,[Text.UTF8Encoding]::new($false,$true),$true,4096,$true)
try{$script=$reader.ReadToEnd()}finally{$reader.Dispose()}
$snapshot=& ([scriptblock]::Create($script)) $generation.manifest $generation.sha256
$types=@('GmgnRepairNamespace','GmgnImmutableSources','GmgnRepairProcessBinding','GmgnLegacyCapture','GmgnLegacyLifetimePins','GmgnReadDirectoryIdentity','GmgnReadProcessLifetime','GmgnLegacyRetirement','GmgnSupervisorReadContext','GmgnScheduledActionIdentity','GmgnSupervisorPipe','GmgnSourcePinBinding','GmgnPinTransferHandshake','GmgnSupervisorSourceAdmission','GmgnReadLocalAuth','GmgnHandoffCompletion','GmgnReadProofPin')
$types+=@('GmgnReadInstallPlan','GmgnReadOwnerProof')
$sources=[string[]]@($types|ForEach-Object {[Text.UTF8Encoding]::new($false,$true).GetString($snapshot.Bytes[(Join-Path $tools ($_+'.cs'))]).TrimStart([char]0xfeff)})
$sources=[string[]](@($sources)+@([Text.UTF8Encoding]::new($false,$true).GetString($snapshot.Bytes[(Join-Path $tools 'GmgnLegacyRetirement.Auth.cs')]).TrimStart([char]0xfeff)))
$sources=[string[]](@($sources)+@([Text.UTF8Encoding]::new($false,$true).GetString($snapshot.Bytes[(Join-Path $tools 'GmgnReadInstallPlan.Warm.cs')]).TrimStart([char]0xfeff)))
$sources=[string[]](@($sources)+@([Text.UTF8Encoding]::new($false,$true).GetString($snapshot.Bytes[(Join-Path $tools 'GmgnReadInstallPlan.Root.cs')]).TrimStart([char]0xfeff)))
$sources=[string[]](@($sources)+@([Text.UTF8Encoding]::new($false,$true).GetString($snapshot.Bytes[(Join-Path $tools 'GmgnReadInstallPlan.Legacy.cs')]).TrimStart([char]0xfeff)))
$sources=[string[]](@($sources)+@([Text.UTF8Encoding]::new($false,$true).GetString($snapshot.Bytes[(Join-Path $tools 'GmgnReadInstallPlan.Sender.cs')]).TrimStart([char]0xfeff)))
$sources=[string[]](@($sources)+@([Text.UTF8Encoding]::new($false,$true).GetString($snapshot.Bytes[(Join-Path $tools 'GmgnReadInstallPlan.Retire.cs')]).TrimStart([char]0xfeff)))
$compiler=[Microsoft.CSharp.CSharpCodeProvider]::new();$p=[CodeDom.Compiler.CompilerParameters]::new();$p.GenerateInMemory=$true
foreach($reference in @('System.dll','System.Core.dll','System.Web.Extensions.dll',[Management.Automation.PSObject].Assembly.Location)){$null=$p.ReferencedAssemblies.Add($reference)}
try{$compiled=$compiler.CompileAssemblyFromSource($p,$sources)}finally{$compiler.Dispose()}
if($compiled.Errors.HasErrors){$compiled.Errors|ForEach-Object {$_.ErrorText};throw 'OWN_GENERATION_COMPLETE_ASSEMBLY_COMPILE'}
foreach($namePart in $types){if(-not [object]::ReferenceEquals(($namePart -as [type]).Assembly,$compiled.CompiledAssembly)){throw 'OWN_GENERATION_NATIVE_PROVENANCE'}}
if($Case -ceq 'ProductionWarmFallback'){
 $code='';try{$null=[GmgnSupervisorReadContext]::new($null,$null,$null,$null,$null,$null,$null,$null,$null,$null)}catch{
  $e=$_.Exception;while($e.InnerException){$e=$e.InnerException};$code=$e.Message
 }
 $flags=[Reflection.BindingFlags]'Static,NonPublic'
 $contexts=[GmgnSupervisorReadContext].GetField('retained',$flags).GetValue($null)
 $factories=[GmgnReadProcessLifetime].GetField('retained',$flags).GetValue($null)
 if($code -cne 'GMGN_SUPERVISOR_ADMISSION_REQUIRED' -or $contexts.Count -ne 1 -or $contexts[0].State -cne 'FAULT' -or $factories.Count -ne 0){throw 'OWN_PRODUCTION_WARM_FALLBACK_ALLOWED'}
 [pscustomobject]@{pass=$true;case=$Case;code=$code;provider_calls=0;production_verified=$false}|ConvertTo-Json -Compress
 return
}
$paths=[string[]](@($snapshot.Files|ForEach-Object {$_.path})+@($generation.manifest))
$hashes=[string[]](@($snapshot.Files|ForEach-Object {$_.sha256})+@($generation.sha256))
$native=[GmgnImmutableSources]::new('%USERPROFILE%','C:\Program Files\nodejs\node.exe',$paths,$hashes)
$binding=[GmgnSourcePinBinding]::new($native)
if($binding.ReadManifestHash($generation.manifest) -cne $generation.sha256){throw 'OWN_GENERATION_MANIFEST_NOT_NATIVE_PINNED'}
$manifest=$native.ReadText($generation.manifest)|ConvertFrom-Json
if(@($manifest.files|Where-Object {$_.path -ceq $generation.manifest}).Count){throw 'OWN_GENERATION_MANIFEST_SELF_HASH_CYCLE'}
foreach($namePart in @('GMGN-SupervisorTask.ps1','GmgnScheduledActionIdentity.cs','GmgnSupervisorPipe.cs','GmgnSourcePinBinding.cs','GmgnPinTransferHandshake.cs','GmgnSupervisorSourceAdmission.cs','GmgnReadLocalAuth.cs','GmgnHandoffCompletion.cs')){
 if(-not $native.Contains((Join-Path $tools $namePart))){throw 'OWN_GENERATION_CODE_CHAIN_NOT_CLOSED'}
}
$native.Check();$binding.Check()
[pscustomobject]@{pass=$true;manifest_native_pinned=$true;manifest_members=$generation.files;native_members=$native.Count;generation=$generation.sha256;provider_calls=0;retirement_attempted=$false;production_verified=$false}|ConvertTo-Json -Compress
