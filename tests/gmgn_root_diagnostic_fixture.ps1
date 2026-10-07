param([ValidateSet('Known','WrappedKnown','Unknown','PrefixSuffix','KnownWithInner','Null','DeepLimit')][string]$Case='Known')
$ErrorActionPreference='Stop';$repo=Split-Path -Parent $PSScriptRoot;$tools=Join-Path $repo 'TOOLS'
$names=@('GmgnRepairNamespace','GmgnImmutableSources','GmgnRepairProcessBinding','GmgnReadDirectoryIdentity','GmgnScheduledActionIdentity','GmgnReadLocalAuth','GmgnReadInstallPlan','GmgnSupervisorPipe','GmgnSourcePinBinding','GmgnPinTransferHandshake','GmgnSupervisorSourceAdmission','GmgnHandoffCompletion')
Add-Type -Path @(@($names|ForEach-Object {Join-Path $tools ($_+'.cs')})+@(Join-Path $tools 'GmgnReadInstallPlan.Warm.cs')+@(Join-Path $tools 'GmgnReadInstallPlan.Root.cs')) -ReferencedAssemblies @('System.dll','System.Core.dll','System.Web.Extensions.dll',[Management.Automation.PSObject].Assembly.Location)
$fake=[InvalidOperationException]::new('SYNTHETIC_PRIVATE_DIAGNOSTIC')
$errorValue=switch($Case){
 'Known'{[InvalidOperationException]::new('GMGN_TASK_ACTION')}
 'WrappedKnown'{[Reflection.TargetInvocationException]::new([InvalidOperationException]::new('GMGN_TASK_ACTION'))}
 'Unknown'{$fake}
 'PrefixSuffix'{[InvalidOperationException]::new('GMGN_TASK_ACTION SYNTHETIC_PRIVATE_DIAGNOSTIC')}
 'KnownWithInner'{[InvalidOperationException]::new('GMGN_ROOT_TASK_SCHEDULER_CONNECT',$fake)}
 'Null'{$null}
 'DeepLimit'{$value=[InvalidOperationException]::new('GMGN_TASK_ACTION');foreach($i in 0..8){$value=[Exception]::new('SYNTHETIC_PRIVATE_DIAGNOSTIC',$value)};$value}
}
$method=[GmgnReadInstallPlan+RootTaskBinding].GetMethod('NormalizeDefinitionFailure',[Reflection.BindingFlags]'Static,NonPublic')
$mapped=$method.Invoke($null,[object[]]@($errorValue))
$expected=switch($Case){'Known'{'GMGN_TASK_ACTION'};'WrappedKnown'{'GMGN_TASK_ACTION'};'KnownWithInner'{'GMGN_ROOT_TASK_SCHEDULER_CONNECT'};default{'GMGN_ROOT_TASK_DEFINITION_OPERATION'}}
if($mapped -isnot [InvalidOperationException] -or $mapped.Message -cne $expected -or $mapped.InnerException -or $mapped.Data.Count){throw 'OWN_ROOT_DIAGNOSTIC_NOT_SANITIZED'}
[pscustomobject]@{pass=$true;case=$Case;code=$mapped.Message;inner_retained=$false;provider_calls=0;production_verified=$false}|ConvertTo-Json -Compress
