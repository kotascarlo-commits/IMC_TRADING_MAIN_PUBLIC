$ErrorActionPreference='Stop';$repo=Split-Path -Parent $PSScriptRoot;$tools=Join-Path $repo 'TOOLS'
. (Join-Path $tools 'GMGN-SupervisorTask.ps1')
$names=@('GmgnRepairNamespace','GmgnReadDirectoryIdentity','GmgnReadLocalAuth','GmgnScheduledActionIdentity')
$pins=[Collections.Generic.List[object]]::new();$sources=[Collections.Generic.List[string]]::new();$inventory=@()
foreach($name in $names){
 $path=Join-Path $tools ($name+'.cs');$pin=[IO.FileStream]::new($path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read);$pins.Add($pin)
 $sha=[Security.Cryptography.SHA256]::Create();try{$hash=[BitConverter]::ToString($sha.ComputeHash($pin)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
 $pin.Position=0;$r=[IO.StreamReader]::new($pin,[Text.UTF8Encoding]::new($false,$true),$true,4096,$true);try{$sources.Add($r.ReadToEnd())}finally{$r.Dispose()}
 $inventory+=@{path=$path;sha256=$hash}
}
$compiler=[Microsoft.CSharp.CSharpCodeProvider]::new();$p=[CodeDom.Compiler.CompilerParameters]::new();$p.GenerateInMemory=$true
foreach($reference in @('System.dll','System.Core.dll')){$null=$p.ReferencedAssemblies.Add($reference)}
try{$compiled=$compiler.CompileAssemblyFromSource($p,$sources.ToArray())}finally{$compiler.Dispose()}
if($compiled.Errors.HasErrors){throw 'OWN_AUTH_TASK_SOURCE_COMPILE'}
$directory=Join-Path (Join-Path $repo '.local-tools') ('gmgn-read-auth-'+[guid]::NewGuid().ToString('N'))
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$auth=[GmgnReadLocalAuth]::CreateNew($directory,$sid,[Text.Encoding]::ASCII.GetBytes('f'*64));$binding=$auth.ReadPublicBinding()
$bindingEncoded=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($binding|ConvertTo-Json -Compress)))
$inventoryEncoded=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($inventory|ConvertTo-Json -Compress)))
$body='OWN_LOCAL_IPC_OWNER_TASK';$expected=$auth.Sign($body)
$worker=@'
$ErrorActionPreference='Stop';$pins=[Collections.Generic.List[object]]::new();$sources=[Collections.Generic.List[string]]::new()
try{
 $inventory=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:OWN_AUTH_INVENTORY))|ConvertFrom-Json
 foreach($file in $inventory){
  $pin=[IO.FileStream]::new($file.path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read);$pins.Add($pin)
  $sha=[Security.Cryptography.SHA256]::Create();try{$hash=[BitConverter]::ToString($sha.ComputeHash($pin)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
  if($hash -cne $file.sha256){throw 'OWN_AUTH_TASK_SOURCE_CHANGED'}
  $pin.Position=0;$r=[IO.StreamReader]::new($pin,[Text.UTF8Encoding]::new($false,$true),$true,4096,$true);try{$sources.Add($r.ReadToEnd())}finally{$r.Dispose()}
 }
 $compiler=[Microsoft.CSharp.CSharpCodeProvider]::new();$p=[CodeDom.Compiler.CompilerParameters]::new();$p.GenerateInMemory=$true
 foreach($reference in @('System.dll','System.Core.dll')){$null=$p.ReferencedAssemblies.Add($reference)}
 try{$compiled=$compiler.CompileAssemblyFromSource($p,$sources.ToArray())}finally{$compiler.Dispose()}
 if($compiled.Errors.HasErrors){throw 'OWN_AUTH_TASK_COMPILE'}
 $b=[string[]]([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:OWN_AUTH_BINDING))|ConvertFrom-Json)
 $auth=[GmgnReadLocalAuth]::OpenExisting($b[1],$b[2],$b[3],$b[4],$b[5])
 if($auth.Sign('OWN_LOCAL_IPC_OWNER_TASK') -cne $env:OWN_AUTH_EXPECTED){throw 'OWN_AUTH_TASK_HMAC_MISMATCH'}
 $auth.Check();[IO.File]::WriteAllText((Join-Path $b[1] 'owner-task-result.json'),(@{pass=$true;pid=$PID;owner_sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value;file_id=$auth.ReadPublicBinding()[4]}|ConvertTo-Json -Compress))
}finally{foreach($pin in $pins){$pin.Dispose()}}
'@
# Only public native bindings, source hashes and a fixture HMAC cross the
# bootstrap boundary. The fake IPC key stays inside DPAPI/private objects.
$launch='$env:OWN_AUTH_INVENTORY="'+$inventoryEncoded+'";$env:OWN_AUTH_BINDING="'+$bindingEncoded+'";$env:OWN_AUTH_EXPECTED="'+$expected+'";'+$worker
$args='-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -EncodedCommand '+[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($launch))
$service=New-Object -ComObject Schedule.Service;$service.Connect();$folder=$service.GetFolder('\');$name='IMC-READ-FIXTURE-'+[guid]::NewGuid().ToString('N');$exe=Join-Path $PSHOME 'powershell.exe'
$definition=New-GmgnSupervisorTaskDefinition $service $sid $exe $args $repo;$created=$false;$task=$null;$identity=$null
try{
 try{$task=Register-GmgnSupervisorTask $folder $name $definition $sid $exe $args $repo;$created=$true}catch{if($_.Exception.Data.Contains('GmgnCreatedTask')){$task=$_.Exception.Data['GmgnCreatedTask'];$created=$true};throw}
 $earliest=[DateTime]::UtcNow.ToString('o');$instance=Start-GmgnSupervisorTask $task $sid $exe $args $repo;$guid=$instance.InstanceGuid;$instance.Refresh();$engine=[int]$instance.EnginePID
 $identity=Bind-GmgnSupervisorTaskAction $instance $task $guid $engine $sid $exe $args $repo $earliest
 $clock=[Diagnostics.Stopwatch]::StartNew();while($identity.ReadOriginalExitCode() -lt 0 -and $clock.Elapsed.TotalSeconds -lt 10){Start-Sleep -Milliseconds 10}
 if($identity.ReadOriginalExitCode() -ne 0){throw 'OWN_AUTH_TASK_EXIT_UNCONFIRMED'}
 $proof=[IO.File]::ReadAllText((Join-Path $directory 'owner-task-result.json'))|ConvertFrom-Json
 if(-not $proof.pass -or $proof.pid -ne $engine -or $proof.owner_sid -cne $sid -or $proof.file_id -cne $binding[4]){throw 'OWN_AUTH_TASK_BINDING_MISMATCH'}
}finally{
 if($created){
  if(-not $identity){throw 'OWN_AUTH_TASK_NATIVE_IDENTITY_MISSING'}
  $clock=[Diagnostics.Stopwatch]::StartNew();while($identity.ReadOriginalExitCode() -lt 0 -and $clock.Elapsed.TotalSeconds -lt 10){Start-Sleep -Milliseconds 10}
  if($identity.ReadOriginalExitCode() -lt 0){throw 'OWN_AUTH_TASK_RETAINED'}
  $clock.Restart();while($task.GetInstances(0).Count -and $clock.Elapsed.TotalSeconds -lt 5){Start-Sleep -Milliseconds 10}
  $current=$folder.GetTask($name);Assert-GmgnSupervisorTaskDefinition $current.Definition $sid $exe $args $repo
  if($current.GetInstances(0).Count){throw 'OWN_AUTH_TASK_CLEANUP_ACTIVE'}
  $folder.DeleteTask($name,0);$absent=$false;try{$null=$folder.GetTask($name)}catch{$e=$_.Exception;while($e.InnerException){$e=$e.InnerException};$absent=($e.HResult -band 65535) -eq 2}
  if(-not $absent){throw 'OWN_AUTH_TASK_DELETE_UNCONFIRMED'}
 }
 foreach($pin in $pins){$pin.Dispose()}
}
[pscustomobject]@{pass=$true;owner_task_dpapi_verified=$true;child_native_exit=$identity.ReadOriginalExitCode();task_deleted=$true;provider_calls=0;production_verified=$false}|ConvertTo-Json -Compress
