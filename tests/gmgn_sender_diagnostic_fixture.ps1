param([string]$Case='Known')
$ErrorActionPreference='Stop';$repo=Split-Path -Parent $PSScriptRoot;$tools=Join-Path $repo 'TOOLS'
$names=@('GmgnRepairNamespace','GmgnImmutableSources','GmgnRepairProcessBinding','GmgnReadDirectoryIdentity','GmgnScheduledActionIdentity','GmgnReadLocalAuth','GmgnReadInstallPlan','GmgnReadProofPin','GmgnSupervisorPipe','GmgnSourcePinBinding','GmgnPinTransferHandshake','GmgnSupervisorSourceAdmission','GmgnHandoffCompletion','GmgnLegacyLifetimePins','GmgnLegacyCapture','GmgnLegacyRetirement','GmgnReadOwnerProof')
$parts=@('GmgnReadInstallPlan.Warm.cs','GmgnReadInstallPlan.Root.cs','GmgnReadInstallPlan.Legacy.cs','GmgnReadInstallPlan.Sender.cs','GmgnReadInstallPlan.Retire.cs','GmgnLegacyRetirement.Auth.cs')
Add-Type -Path @(@($names|ForEach-Object{Join-Path $tools ($_+'.cs')})+@($parts|ForEach-Object{Join-Path $tools $_})) -ReferencedAssemblies @('System.dll','System.Core.dll','System.Web.Extensions.dll',[Management.Automation.PSObject].Assembly.Location)
$bait=[Exception]::new('SYNTHETIC_PRIVATE_SENDER_DIAGNOSTIC');$code='GMGN_PROOF_WRITE_FLUSH'
$value=switch($Case){
 'Known'{[InvalidOperationException]::new($code)}
 'Wrapped'{[Reflection.TargetInvocationException]::new([InvalidOperationException]::new($code))}
 'Suffix'{[Exception]::new($code+' SYNTHETIC_PRIVATE_SENDER_DIAGNOSTIC')}
 'InnerData'{$e=[InvalidOperationException]::new($code,$bait);$e.Data['private']='SYNTHETIC_PRIVATE_SENDER_DIAGNOSTIC';$e}
 'Depth7'{$e=[Exception]::new($code);foreach($i in 1..7){$e=[Exception]::new('SYNTHETIC_PRIVATE_SENDER_DIAGNOSTIC',$e)};$e}
 'Depth8'{$e=[Exception]::new($code);foreach($i in 1..8){$e=[Exception]::new('SYNTHETIC_PRIVATE_SENDER_DIAGNOSTIC',$e)};$e}
 'Null'{$null};default{$bait}
}
$method=[GmgnReadInstallPlan+RootRetirementSender].GetMethod('NormalizeFailure',[Reflection.BindingFlags]'Static,NonPublic')
$mapped=$method.Invoke($null,[object[]]@($value));$expected=if($Case -cin @('Known','Wrapped','InnerData','Depth7')){$code}else{'GMGN_ROOT_SENDER_OPERATION'}
if($mapped.Message -cne $expected -or $mapped.InnerException -or $mapped.Data.Count){throw 'OWN_SENDER_DIAGNOSTIC_LEAK'}
[pscustomobject]@{pass=$true;case=$Case;code=$mapped.Message;provider_calls=0;production_verified=$false}|ConvertTo-Json -Compress
