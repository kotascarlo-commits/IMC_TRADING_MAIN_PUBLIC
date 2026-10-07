param([string]$Workspace,[string]$Python,[ValidateSet('Normal','Early','WrongPid','WrongBirth','Collision','ConnectTimeout','ReadTimeout','PeerExit','Oversize','Empty','Concurrent','CloseFault','CloseException','WriteTimeout','PartialWrite','ZeroRead','WaitFailed','IOIncomplete','CloseAfterDeadline','ConcurrentClose','PythonNormal','PythonReadTimeout','PythonWrongPid','PythonWrongBirth','PythonHeapFreeFalse','PythonPartialWrite','PythonCloseFalse','PythonCloseException','PythonConcurrentClose','PythonUntil','PythonUntilReadTimeout','Until','UntilReadTimeout')][string]$Case='Normal')
$ErrorActionPreference='Stop'
$Repo=Split-Path -Parent $PSScriptRoot
. (Join-Path $Repo 'TOOLS\IMC_STORAGE_CHANNEL.ps1')
. (Join-Path $Repo 'TOOLS\IMC_RUNTIME_JOB.ps1')
$nonce=[guid]::NewGuid().ToString('N')
$budget=if($Case -cin @('ConnectTimeout','ReadTimeout','WriteTimeout','CloseAfterDeadline')){2}else{15}
$endTick=0L;$frequency=[Diagnostics.Stopwatch]::Frequency
if($Case -cin @('PythonUntil','PythonUntilReadTimeout','Until','UntilReadTimeout')) {
    $duration=if($Case -cin @('PythonUntil','Until')){10}else{2}
    $endTick=[Diagnostics.Stopwatch]::GetTimestamp()+$frequency*$duration
    $server=[IMCStorageChannel]::CreateServerUntil($nonce,'fixture',$endTick,$frequency)
}else{$server=[IMCStorageChannel]::CreateServer($nonce,'fixture',$budget)}
if($Case -ceq 'Collision') {
    try{$null=[IMCStorageChannel]::CreateServer($nonce,'fixture',15);throw 'fixture_collision_allowed'}
    # With nMaxInstances=1 this Windows build rejects at the limit (231)
    # before checking FIRST_PIPE_INSTANCE (5). Both forbid the second server.
    catch{if($_.Exception.GetBaseException().Message -cnotin @('STORAGE_CHANNEL_NATIVE_5','STORAGE_CHANNEL_NATIVE_231')){throw}}
    $server.Close()
    if([IMCStorageChannel]::RetainedCount -ne 1){throw 'fixture_failed_constructor_lost'}
    Write-Output 'COLLISION_REJECTED';exit 0
}
$job=[IMCRuntimeJob]::new();$shell=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$childScript=Join-Path $PSScriptRoot 'storage_channel_child.ps1'
$parentBirth=[Diagnostics.Process]::GetCurrentProcess().StartTime.ToUniversalTime().ToFileTimeUtc()
$childArgs=[string[]]@('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',$childScript,
    '-Library',(Join-Path $Repo 'TOOLS\IMC_STORAGE_CHANNEL.ps1'),'-Nonce',$nonce,'-ParentId',[string]$PID,
    '-ParentBirth',[string]$parentBirth,'-Workspace',$Workspace,'-Case',$Case)
if($Case.StartsWith('Python')) {
    $shell=$Python;$childArgs=[string[]]@('-I',(Join-Path $PSScriptRoot 'storage_channel_python_child.py'),$nonce,
        [string]$PID,[string]$parentBirth,$Workspace,$Case)
    if($endTick -ne 0){$childArgs+=[string[]]@([string]$endTick,[string]$frequency)}
}elseif($endTick -ne 0){$childArgs+=[string[]]@('-EndTick',[string]$endTick,'-Frequency',[string]$frequency)}
$child=$job.Start($shell,$childArgs,$Workspace)
try {
    $job.ObserveMember($child)
    $actual=[Diagnostics.Process]::GetProcessById($child);$birth=$actual.StartTime.ToUniversalTime().ToFileTimeUtc();$actual.Dispose()
    if($Case.StartsWith('Python')) {
        $server.BindPeer($child,$birth)
        if($Case -cne 'PythonWrongBirth'){$server.Connect()}
        if($Case -cin @('PythonNormal','PythonUntil','PythonCloseFalse','PythonCloseException','PythonConcurrentClose')) {
            $server.Write([Text.Encoding]::UTF8.GetBytes('parent phase request'))
            if([Text.Encoding]::UTF8.GetString($server.Read()) -cne 'worker completion'){throw 'fixture_python_reply_mismatch'}
            $server.Write([Text.Encoding]::UTF8.GetBytes('exit permitted'))
        }elseif($Case -cin @('PythonHeapFreeFalse','PythonPartialWrite')) {
            $server.Write([Text.Encoding]::UTF8.GetBytes('parent phase request'))
        }
        if($Case -cin @('PythonNormal','PythonUntil')) {
            $wait=[Diagnostics.Stopwatch]::StartNew()
            while($job.Running($child) -and $wait.Elapsed.TotalSeconds -lt 5){Start-Sleep -Milliseconds 5}
            if($job.Running($child) -or $job.ExitCode($child) -ne 0){throw 'fixture_python_exit_failed'}
        }else {
            $wait=[Diagnostics.Stopwatch]::StartNew()
            while(-not [IO.File]::Exists((Join-Path $Workspace 'python-fault.json')) -and $wait.Elapsed.TotalSeconds -lt 5){Start-Sleep -Milliseconds 5}
            if(-not [IO.File]::Exists((Join-Path $Workspace 'python-fault.json')) -or -not $job.Running($child)){throw 'fixture_python_fault_not_held'}
            $report=Get-Content (Join-Path $Workspace 'python-fault.json') -Raw | ConvertFrom-Json
            if($report.retained -ne $true){throw 'fixture_python_native_memory_lost'}
        }
        if($Case -ceq 'PythonUntilReadTimeout') {
            try{$server.Close();throw 'fixture_shared_deadline_reset'}
            catch{if($_.Exception.GetBaseException().Message -cne 'STORAGE_CHANNEL_DEADLINE'){throw}}
            if(-not $server.Faulted -or $server.Closed -or [IMCStorageChannel]::RetainedCount -ne 1){throw 'fixture_shared_deadline_not_retained'}
        }else{$server.Close()}
        Write-Output 'NATIVE_PYTHON_CLIENT_PASS';exit 0
    }
    $expectedPid=if($Case -ceq 'WrongPid'){$PID}else{$child}
    $expectedBirth=if($Case -ceq 'WrongPid'){$parentBirth}else{$birth}
    if($Case -ceq 'WrongBirth'){$expectedBirth++}
    if($Case -ceq 'WrongBirth') {
        try{$server.BindPeer($expectedPid,$expectedBirth);throw 'fixture_wrong_birth_admitted'}
        catch{if($_.Exception.GetBaseException().Message -cne 'STORAGE_CHANNEL_PEER_BIRTH'){throw}}
    }else {
        $server.BindPeer($expectedPid,$expectedBirth)
        if($Case -cin @('Early','PeerExit','Concurrent')) {
            $wait=[Diagnostics.Stopwatch]::StartNew()
            while(-not [IO.File]::Exists((Join-Path $Workspace 'connected')) -and $wait.Elapsed.TotalSeconds -lt 10){Start-Sleep -Milliseconds 5}
            if(-not [IO.File]::Exists((Join-Path $Workspace 'connected'))){throw 'fixture_client_not_connected'}
        }
        if($Case -ceq 'PeerExit') {
            while($job.Running($child)){Start-Sleep -Milliseconds 5}
            try{$server.Connect();throw 'fixture_exited_peer_admitted'}
            catch{if($_.Exception.GetBaseException().Message -cne 'STORAGE_CHANNEL_PEER_EXITED'){throw}}
        }elseif($Case -ceq 'ConnectTimeout') {
            try{$server.Connect();throw 'fixture_connect_timeout_admitted'}
            catch{if($_.Exception.GetBaseException().Message -cne 'STORAGE_CHANNEL_DEADLINE'){throw}}
        }elseif($Case -ceq 'WrongPid') {
            try{$server.Connect();throw 'fixture_wrong_pid_admitted'}
            catch{if($_.Exception.GetBaseException().Message -cne 'STORAGE_CHANNEL_PEER_PID'){throw}}
        }else {
            $server.Connect()
            if($Case -ceq 'ZeroRead') {
                $wait=[Diagnostics.Stopwatch]::StartNew()
                while(-not [IO.File]::Exists((Join-Path $Workspace 'data-written')) -and $wait.Elapsed.TotalSeconds -lt 5){Start-Sleep -Milliseconds 5}
                if(-not [IO.File]::Exists((Join-Path $Workspace 'data-written'))){throw 'fixture_zero_read_no_peer_data'}
            }
            if($Case -cin @('PartialWrite','ZeroRead','WaitFailed','IOIncomplete')) {
                Add-Type -TypeDefinition @'
using System;using System.Reflection;using System.Runtime.InteropServices;
public static class ChannelIOFixture {
 public static int Calls;
 static Type ChannelType;
 [DllImport("kernel32.dll",SetLastError=true)] static extern void SetLastError(uint error);
 public static bool Partial(IntPtr pipe,IntPtr buffer,uint length,out uint bytes,IntPtr overlap) {
  Calls++;object[] args=new object[]{pipe,buffer,length/2,(uint)0,overlap};
  bool result=(bool)ChannelType.GetMethod("WriteFile",BindingFlags.Static|BindingFlags.NonPublic).Invoke(null,args);
  int error=Marshal.GetLastWin32Error();bytes=(uint)args[3];SetLastError((uint)error);return result;
 }
 public static bool Zero(IntPtr pipe,IntPtr buffer,uint length,out uint bytes,IntPtr overlap) {
  Calls++;object[] args=new object[]{pipe,buffer,(uint)0,(uint)0,overlap};
  bool result=(bool)ChannelType.GetMethod("ReadFile",BindingFlags.Static|BindingFlags.NonPublic).Invoke(null,args);
  int error=Marshal.GetLastWin32Error();bytes=(uint)args[3];SetLastError((uint)error);return result;
 }
 public static bool Incomplete(IntPtr pipe,IntPtr overlap,out uint bytes,bool wait) {
  Calls++;if(Calls==1){bytes=0;SetLastError(996);return false;}
  object[] args=new object[]{pipe,overlap,(uint)0,wait};
  bool result=(bool)ChannelType.GetMethod("GetOverlappedResult",BindingFlags.Static|BindingFlags.NonPublic).Invoke(null,args);
  int error=Marshal.GetLastWin32Error();bytes=(uint)args[2];SetLastError((uint)error);return result;
 }
 public static uint Failed(IntPtr handle,uint milliseconds){Calls++;SetLastError(6);return UInt32.MaxValue;}
 public static void Attach(object group,string name,string method) {
  ChannelType=group.GetType();
  FieldInfo field=group.GetType().GetField(name,BindingFlags.Instance|BindingFlags.NonPublic);
  field.SetValue(group,Delegate.CreateDelegate(field.FieldType,typeof(ChannelIOFixture).GetMethod(method)));
 }
}
'@
                switch($Case) {
                    'PartialWrite' {[ChannelIOFixture]::Attach($server,'writeNative','Partial')}
                    'ZeroRead' {[ChannelIOFixture]::Attach($server,'readNative','Zero')}
                    'WaitFailed' {[ChannelIOFixture]::Attach($server,'waitEventNative','Failed')}
                    'IOIncomplete' {[ChannelIOFixture]::Attach($server,'resultNative','Incomplete')}
                }
            }
            if($Case -ceq 'CloseAfterDeadline') {
                Start-Sleep -Milliseconds 2100
                try{$server.Close();throw 'fixture_close_after_deadline_admitted'}
                catch{if($_.Exception.GetBaseException().Message -cne 'STORAGE_CHANNEL_DEADLINE'){throw}}
                foreach($name in @('pipeState','peerState')) {
                    if($server.GetType().GetField($name,[Reflection.BindingFlags]'Instance,NonPublic').GetValue($server).ToString() -cne 'Owned'){throw 'fixture_deadline_closed_handle'}
                }
            }elseif($Case -cin @('ReadTimeout','UntilReadTimeout')) {
                try{$null=$server.Read();throw 'fixture_read_timeout_admitted'}
                catch{if($_.Exception.GetBaseException().Message -cne 'STORAGE_CHANNEL_DEADLINE'){throw}}
            }elseif($Case -ceq 'WriteTimeout') {
                try{while($true){$server.Write([byte[]]::new(131072))}}
                catch{if($_.Exception.GetBaseException().Message -cne 'STORAGE_CHANNEL_DEADLINE'){throw}}
            }elseif($Case -ceq 'PartialWrite') {
                try{$server.Write([byte[]]@(1,2,3,4));throw 'fixture_partial_write_admitted'}
                catch{if($_.Exception.GetBaseException().Message -cne 'STORAGE_CHANNEL_WRITE_PARTIAL'){throw}}
                if([ChannelIOFixture]::Calls -ne 1){throw 'fixture_partial_write_retried'}
            }elseif($Case -cin @('ZeroRead','WaitFailed')) {
                $expected=if($Case -ceq 'ZeroRead'){'STORAGE_CHANNEL_READ_PROGRESS'}else{'STORAGE_CHANNEL_NATIVE_6'}
                try{$null=$server.Read();throw 'fixture_invalid_native_result_admitted'}
                catch{if($_.Exception.GetBaseException().Message -cne $expected){throw}}
            }elseif($Case -cin @('Oversize','Empty')) {
                try{$null=$server.Read();throw 'fixture_malformed_frame_admitted'}
                catch{if($_.Exception.GetBaseException().Message -cne 'STORAGE_CHANNEL_FRAME_SIZE'){throw}}
            }elseif($Case -ceq 'Concurrent') {
                Add-Type -TypeDefinition @'
using System;using System.Threading;
public static class ChannelConcurrentFixture {
 public static void Run(object group) {
  Exception failure=null;Thread read=new Thread(delegate(){try{group.GetType().GetMethod("Read").Invoke(group,null);}catch(Exception e){failure=e.GetBaseException();}});
  read.Start();Thread.Sleep(100);
  try{group.GetType().GetMethod("Write").Invoke(group,new object[]{new byte[]{1}});throw new Exception("fixture_concurrent_allowed");}
  catch(Exception e){if(e.GetBaseException().Message!="STORAGE_CHANNEL_CONCURRENT")throw;}
  if(!read.Join(5000)||failure==null||failure.Message!="STORAGE_CHANNEL_UNCONFIRMED")throw new Exception("fixture_pending_not_retained");
 }
}
'@
                [ChannelConcurrentFixture]::Run($server)
            }else {
                $server.Write([Text.Encoding]::UTF8.GetBytes('parent phase request'))
                $reply=[Text.Encoding]::UTF8.GetString($server.Read())
                if($reply -cne 'worker completion'){throw 'fixture_reply_mismatch'}
                $server.Write([Text.Encoding]::UTF8.GetBytes('exit permitted'))
                if($Case -ceq 'ConcurrentClose') {
                    Add-Type -TypeDefinition @'
using System;using System.Reflection;using System.Threading;
public static class ChannelCloseRaceFixture {
 public static void Run(object group) {
  Type type=group.GetType();MethodInfo native=type.GetMethod("CloseHandle",BindingFlags.Static|BindingFlags.NonPublic);
  ManualResetEvent entered=new ManualResetEvent(false),release=new ManualResetEvent(false);int calls=0;Exception error=null;
  Func<IntPtr,bool> injected=delegate(IntPtr handle){calls++;bool actual=(bool)native.Invoke(null,new object[]{handle});
   if(!actual)throw new Exception("fixture_actual_close_failed");entered.Set();if(!release.WaitOne(5000))throw new Exception("fixture_close_barrier_timeout");return actual;};
  type.GetField("closeNative",BindingFlags.Instance|BindingFlags.NonPublic).SetValue(group,injected);
  Thread closer=new Thread(delegate(){try{type.GetMethod("Close").Invoke(group,null);}catch(Exception e){error=e.GetBaseException();}});closer.Start();
  if(!entered.WaitOne(5000))throw new Exception("fixture_close_not_entered");
  try{type.GetMethod("Write").Invoke(group,new object[]{new byte[]{1}});throw new Exception("fixture_concurrent_close_admitted");}
  catch(Exception e){if(e.GetBaseException().Message!="STORAGE_CHANNEL_CONCURRENT")throw;}
  finally{release.Set();}
  if(!closer.Join(5000)||error==null||error.Message!="STORAGE_CHANNEL_UNCONFIRMED"||calls!=1)throw new Exception("fixture_close_race_lost");
  if(type.GetField("pipeState",BindingFlags.Instance|BindingFlags.NonPublic).GetValue(group).ToString()!="Closed"||
     type.GetField("peerState",BindingFlags.Instance|BindingFlags.NonPublic).GetValue(group).ToString()!="Owned")throw new Exception("fixture_remaining_peer_lost");
  entered.Dispose();release.Dispose();
 }
}
'@
                    [ChannelCloseRaceFixture]::Run($server)
                }elseif($Case -cin @('CloseFault','CloseException')) {
                    Add-Type -TypeDefinition @'
using System;using System.Reflection;
public static class ChannelCloseFixture {
 public static int Calls;
 public static void Attach(object group,bool after) {
  Type t=group.GetType();MethodInfo close=t.GetMethod("CloseHandle",BindingFlags.Static|BindingFlags.NonPublic);
  Func<IntPtr,bool> f=delegate(IntPtr h){Calls++;if(after){if(!(bool)close.Invoke(null,new object[]{h}))throw new Exception("fixture_close_failed");throw new Exception("fixture_after_actual_close");}return false;};
  t.GetField("closeNative",BindingFlags.Instance|BindingFlags.NonPublic).SetValue(group,f);
 }
}
'@
                    [ChannelCloseFixture]::Attach($server,($Case -ceq 'CloseException'))
                    try{$server.Close();throw 'fixture_bad_close_admitted'}
                    catch{if($_.Exception.GetBaseException().Message -notmatch 'STORAGE_CHANNEL_NATIVE_|fixture_after_actual_close'){throw}}
                    try{$server.Close();throw 'fixture_bad_close_retried'}catch{if($_.Exception.GetBaseException().Message -cne 'STORAGE_CHANNEL_CLOSE_UNCONFIRMED'){throw}}
                    if([ChannelCloseFixture]::Calls -ne 1){throw 'fixture_native_close_reused'}
                }else{$server.Close()}
            }
        }
    }
    if($Case -cin @('Normal','Early','IOIncomplete','Until')) {
        $wait=[Diagnostics.Stopwatch]::StartNew();while($job.Running($child) -and $wait.Elapsed.TotalSeconds -lt 5){Start-Sleep -Milliseconds 5}
        if($job.Running($child) -or $job.ExitCode($child) -ne 0 -or -not $server.Closed -or $server.Faulted){throw 'fixture_peer_success_not_confirmed'}
        Write-Output 'NATIVE_CHANNEL_PAIR_PASS'
    }else {
        if(-not $server.Faulted -or $server.Closed -or [IMCStorageChannel]::RetainedCount -ne 1){throw 'fixture_fault_group_not_retained'}
        if($Case -cin @('ConnectTimeout','ReadTimeout','UntilReadTimeout','WriteTimeout','Concurrent','WaitFailed')) {
            $operation=$server.GetType().GetField('current',[Reflection.BindingFlags]'Instance,NonPublic').GetValue($server)
            if(-not $operation){throw 'fixture_native_operation_not_retained'}
            foreach($fieldName in @('Event','Overlap')) {
                if($operation.GetType().GetField($fieldName).GetValue($operation) -eq [IntPtr]::Zero){throw 'fixture_pending_native_memory_released'}
            }
            if($Case -cne 'ConnectTimeout' -and $operation.GetType().GetField('Buffer').GetValue($operation) -eq [IntPtr]::Zero){throw 'fixture_pending_buffer_released'}
            if($operation.GetType().GetField('Confirmed').GetValue($operation) -or -not $operation.GetType().GetField('Submitted').GetValue($operation)) {
                throw 'fixture_pending_state_invented'
            }
        }
        try{$server.Close();throw 'fixture_fault_close_admitted'}catch{if($_.Exception.GetBaseException().Message -cne 'STORAGE_CHANNEL_CLOSE_UNCONFIRMED'){throw}}
        Write-Output 'NATIVE_CHANNEL_FAULT_RETAINED'
    }
}finally {
    $job.ShutdownWithin(10000)
    if(-not $job.CleanupConfirmed){throw 'fixture_native_job_cleanup_unconfirmed'}
}
