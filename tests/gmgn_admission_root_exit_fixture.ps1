$ErrorActionPreference='Stop';$repo=Split-Path -Parent $PSScriptRoot;$tools=Join-Path $repo 'TOOLS'
. (Join-Path $tools 'GMGN-SupervisorTask.ps1')
Add-Type -Path @((Join-Path $tools 'GmgnRepairNamespace.cs'),(Join-Path $tools 'GmgnReadDirectoryIdentity.cs'),(Join-Path $tools 'GmgnRepairProcessBinding.cs'),(Join-Path $tools 'GmgnScheduledActionIdentity.cs'))
$proofPath=Join-Path (Join-Path $repo '.local-tools') ('admission-root-exit-'+[guid]::NewGuid().ToString('N')+'.json')
$info=[Diagnostics.ProcessStartInfo]::new((Join-Path $PSHOME 'powershell.exe'))
$info.UseShellExecute=$false;$info.CreateNoWindow=$true;$info.RedirectStandardOutput=$true;$info.RedirectStandardError=$true
$info.Arguments='-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+(Join-Path $PSScriptRoot 'gmgn_pin_ack_fixture.ps1')+'" -Case AdmissionRootExit -RootExitProofPath "'+$proofPath+'"'
$root=[Diagnostics.Process]::Start($info);$nativeRoot=[GmgnRepairProcessBinding]::Read($root)
$descriptor=$null;$child=$null;$task=$null
try{
 if(-not $root.WaitForExit(20000) -or $root.ExitCode -ne 0){throw ('OWN_ADMISSION_ROOT_FIXTURE_FAILED '+$root.StandardOutput.ReadToEnd()+$root.StandardError.ReadToEnd())}
 $descriptor=[IO.File]::ReadAllText($proofPath)|ConvertFrom-Json
 if(($descriptor.root -join ':') -cne ($nativeRoot -join ':') -or $descriptor.name -cnotmatch '\AIMC-READ-FIXTURE-[0-9a-f]{32}\z' -or $descriptor.scope -cnotmatch '\\[.]local-tools\\pin-ack-native-[0-9a-f]{32}$'){throw 'OWN_ADMISSION_OUTER_IDENTITY'}
 $directory=[GmgnReadDirectoryIdentity]::new($descriptor.scope)
 $child=[GmgnScheduledActionIdentity]::BindExpectedPeer([string[]]$descriptor.child,$descriptor.sid,$directory,$descriptor.directory_id)
 $service=New-Object -ComObject Schedule.Service;$service.Connect();$folder=$service.GetFolder('\');$task=$folder.GetTask($descriptor.name)
 Assert-GmgnSupervisorTaskDefinition $task.Definition $descriptor.sid $descriptor.exe $descriptor.arguments $repo
 $clock=[Diagnostics.Stopwatch]::StartNew();$admissionPath=Join-Path $descriptor.scope 'child-admission.json'
 while(-not [IO.File]::Exists($admissionPath) -and $clock.Elapsed.TotalSeconds -lt 5){Start-Sleep -Milliseconds 10}
 $admission=[IO.File]::ReadAllText($admissionPath)|ConvertFrom-Json
 if(-not $admission.pass -or -not $admission.original_root_exit -or -not $admission.root_exit_during_send -or -not $admission.independent_of_peer -or -not $admission.warm_origin_independent){throw 'OWN_ADMISSION_ROOT_EXIT_NOT_VERIFIED'}
}finally{
 if($descriptor -and $child -and $task){
  [IO.File]::WriteAllText((Join-Path $descriptor.scope 'outer-child-exit'),'OWN_CLEANUP')
  $clock=[Diagnostics.Stopwatch]::StartNew();while($child.ReadOriginalExitCode() -lt 0 -and $clock.Elapsed.TotalSeconds -lt 5){Start-Sleep -Milliseconds 10}
  if($child.ReadOriginalExitCode() -ne 0){throw 'OWN_ADMISSION_CHILD_EXIT_UNCONFIRMED'}
  $result=[IO.File]::ReadAllText((Join-Path $descriptor.scope 'child-result.json'))|ConvertFrom-Json
  if($result.code){throw ('OWN_ADMISSION_CHILD_FAILED_'+$result.code)}
  $clock.Restart();while($task.GetInstances(0).Count -and $clock.Elapsed.TotalSeconds -lt 5){Start-Sleep -Milliseconds 10}
  $current=$folder.GetTask($descriptor.name);Assert-GmgnSupervisorTaskDefinition $current.Definition $descriptor.sid $descriptor.exe $descriptor.arguments $repo
  if($current.GetInstances(0).Count){throw 'OWN_ADMISSION_TASK_STILL_RUNNING'}
  $folder.DeleteTask($descriptor.name,0)
  $absent=$false;try{$null=$folder.GetTask($descriptor.name)}catch{$e=$_.Exception;while($e.InnerException){$e=$e.InnerException};$absent=($e.HResult -band 65535) -eq 2}
  if(-not $absent){throw 'OWN_ADMISSION_TASK_DELETE_UNCONFIRMED'}
 }
}
[pscustomobject]@{pass=$true;warm_origin_independent=$admission.warm_origin_independent;root_native_exit=$root.ExitCode;child_native_exit=$child.ReadOriginalExitCode();task_deleted=$true;provider_calls=0;production_verified=$false}|ConvertTo-Json -Compress
