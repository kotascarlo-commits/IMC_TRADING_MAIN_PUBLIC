param([ValidateSet('Normal','CancelFault','CloseFault','ProductionProbeRejected')][string]$Case='Normal')
$ErrorActionPreference='Stop';$repo=Split-Path -Parent $PSScriptRoot;$tools=Join-Path $repo 'TOOLS'
Add-Type -Path (Join-Path $tools 'GmgnSupervisorPipe.cs')
Add-Type -TypeDefinition 'using System;using System.Runtime.InteropServices;public static class OwnPipeCleanupProbe{[DllImport("kernel32.dll",SetLastError=true)]static extern bool GetHandleInformation(IntPtr h,out uint flags);public static bool OriginalHeld(IntPtr h){uint flags;return GetHandleInformation(h,out flags)&&(flags&1)==0;}public static Func<string,int> Make(string wanted){return delegate(string phase){return phase==wanted?5:0;};}}'
$scope=Join-Path (Join-Path $repo '.local-tools') ('pipe-native-'+[guid]::NewGuid().ToString('N'));$null=[IO.Directory]::CreateDirectory($scope)
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value;$name='imc-read-bootstrap-'+[guid]::NewGuid().ToString('N')
$pipe=if($Case -ceq 'ProductionProbeRejected'){[GmgnSupervisorPipe]::new($name,$sid,$true,1000)}else{[GmgnSupervisorPipe]::new($name,$sid,$true,1000,$scope)}
$flags=[Reflection.BindingFlags]'Instance,NonPublic';$staticFlags=[Reflection.BindingFlags]'Static,NonPublic'
$original=$pipe.GetType().GetField('nativeHandle',$flags).GetValue($pipe)
if($Case -cne 'Normal'){
 $phase=if($Case -ceq 'CloseFault'){'CLOSE'}else{'CANCEL'}
 $pipe.GetType().GetField('fixtureNativeFailure',$flags).SetValue($pipe,[OwnPipeCleanupProbe]::Make($phase))
}
$ioCode='';$operationsBefore=0;$buffersBefore=0;$pendingBefore=0;$client=$null
if($Case -ceq 'Normal'){
 $client=[IO.Pipes.NamedPipeClientStream]::new('.',$name,[IO.Pipes.PipeDirection]::InOut,[IO.Pipes.PipeOptions]::Asynchronous)
 try{
  $connect=$client.ConnectAsync(1000);$pipe.Accept();$connect.GetAwaiter().GetResult()
  $client.Write([byte[]]@(1,2,3),0,3)
  $received=$pipe.ReadMessage();if([Convert]::ToBase64String($received) -cne 'AQID'){throw 'OWN_PIPE_REAL_IO_CONTENT'}
  $pipe.WriteMessage([byte[]]@(4,5));$received=[byte[]]::new(2)
  if($client.Read($received,0,2) -ne 2 -or [Convert]::ToBase64String($received) -cne 'BAU='){throw 'OWN_PIPE_REAL_IO_REPLY'}
 }finally{$client.Dispose()}
}elseif($Case -ceq 'CancelFault'){
 # No client connects. Accept creates an actual outstanding overlapped operation;
 # the injected cancellation failure prevents its cancellation at the deadline.
 try{$pipe.Accept()}catch{$e=$_.Exception;while($e.InnerException){$e=$e.InnerException};$ioCode=$e.Message}
 if($ioCode -cne 'GMGN_PIPE_DEADLINE'){throw 'OWN_PIPE_PENDING_ACCEPT_NOT_REACHED'}
}
$operations=$pipe.GetType().GetField('operations',$flags).GetValue($pipe)
$operationsBefore=$operations.Count;$buffersBefore=$pipe.GetType().GetField('buffers',$flags).GetValue($pipe).Count
foreach($operation in $operations){if(-not $operation.IsCompleted){$pendingBefore++}}
if($Case -ceq 'Normal' -and ($operationsBefore -lt 3 -or $buffersBefore -lt 2 -or $pendingBefore)){throw 'OWN_PIPE_REAL_IO_NOT_COMPLETED'}
if($Case -ceq 'CancelFault' -and ($operationsBefore -ne 1 -or $pendingBefore -ne 1)){throw 'OWN_PIPE_PENDING_IO_NOT_HELD'}
$code='';try{$pipe.Dispose()}catch{$e=$_.Exception;while($e.InnerException){$e=$e.InnerException};$code=$e.Message}
$retained=$pipe.GetType().GetField('retained',$staticFlags).GetValue($null)
$contained=$retained.Contains($pipe)
if($Case -ceq 'Normal'){
 if($code -or $pipe.CleanupFault -or $contained -or $pipe.GetType().GetField('nativeHandle',$flags).GetValue($pipe) -ne [IntPtr]::Zero){throw 'OWN_PIPE_COMPLETED_RESOURCES_RETAINED'}
 foreach($field in @('operations','buffers')){if($pipe.GetType().GetField($field,$flags).GetValue($pipe).Count){throw 'OWN_PIPE_COMPLETED_BUFFERS_RETAINED'}}
}else{
 $expected=if($Case -ceq 'CloseFault'){'GMGN_PIPE_CLOSE_UNCONFIRMED'}elseif($Case -ceq 'ProductionProbeRejected'){'GMGN_PIPE_FIXTURE_FORBIDDEN'}else{''}
 if($code -cne $expected -or -not $contained -or -not [OwnPipeCleanupProbe]::OriginalHeld($original)){throw ('OWN_PIPE_UNCERTAIN_CLOSE_LOST_'+$code)}
 if($Case -cne 'ProductionProbeRejected' -and -not $pipe.CleanupFault){throw 'OWN_PIPE_CLEANUP_FAILURE_NOT_STICKY'}
 if($Case -ceq 'CancelFault' -and ($operations.Count -ne 1 -or $operations[0].IsCompleted)){throw 'OWN_PIPE_PENDING_IO_LOST'}
}
[pscustomobject]@{kind='PIPE_CLEANUP_BOUNDARY';case=$Case;pass=$true;code=$code;original_retained=$contained;operations_before_dispose=$operationsBefore;buffers_before_dispose=$buffersBefore;pending_before_dispose=$pendingBefore;injected_failure=($Case -cne 'Normal');native_failure_verified=$false;provider_calls=0;production_verified=$false;pipe_sha256=(Get-FileHash -LiteralPath (Join-Path $tools 'GmgnSupervisorPipe.cs')).Hash.ToLowerInvariant()}|ConvertTo-Json -Compress
