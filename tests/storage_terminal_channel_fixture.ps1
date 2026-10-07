param([string]$Workspace,[ValidateSet('FastExit','NonzeroExit','UnknownCompletion','PartialWrite','Replay')][string]$Case)
$ErrorActionPreference='Stop'
$repo=Split-Path -Parent $PSScriptRoot
. (Join-Path $repo 'TOOLS\IMC_STORAGE_CHANNEL.ps1')
. (Join-Path $repo 'TOOLS\IMC_RUNTIME_JOB.ps1')
Add-Type -TypeDefinition @'
using System;using System.Reflection;using System.Runtime.InteropServices;using System.Diagnostics;using System.Threading;
public static class TerminalChannelFixture {
 public static int Writes;static Type type;static IntPtr peer;static string mode;
 [DllImport("kernel32.dll",SetLastError=true)]static extern void SetLastError(uint error);
 [DllImport("kernel32.dll")]static extern uint WaitForSingleObject(IntPtr h,uint milliseconds);
 public static bool Write(IntPtr pipe,IntPtr buffer,uint length,out uint bytes,IntPtr overlap) {
  Writes++;object[] args={pipe,buffer,mode=="PartialWrite"?length/2:length,(uint)0,overlap};
  bool result=(bool)type.GetMethod("WriteFile",BindingFlags.Static|BindingFlags.NonPublic).Invoke(null,args);
  int error=Marshal.GetLastWin32Error();bytes=(uint)args[3];SetLastError((uint)error);return result;
 }
 public static bool Result(IntPtr pipe,IntPtr overlap,out uint bytes,bool wait) {
  object[] args={pipe,overlap,(uint)0,wait};
  bool result=(bool)type.GetMethod("GetOverlappedResult",BindingFlags.Static|BindingFlags.NonPublic).Invoke(null,args);
  int error=Marshal.GetLastWin32Error();bytes=(uint)args[2];
  if(result && mode!="PartialWrite") {
   if(WaitForSingleObject(peer,5000)!=0)throw new Exception("terminal_fixture_peer_not_exited");
   if(mode=="UnknownCompletion"){bytes=0;SetLastError(996);return false;}
  }
  SetLastError((uint)error);return result;
 }
 public static void Attach(object channel,string selected) {
  mode=selected;type=channel.GetType();BindingFlags flags=BindingFlags.Instance|BindingFlags.NonPublic;
  peer=(IntPtr)type.GetField("peer",flags).GetValue(channel);
  foreach(string field in new string[]{"writeNative","resultNative"}) {
   var f=type.GetField(field,flags);f.SetValue(channel,Delegate.CreateDelegate(f.FieldType,
    typeof(TerminalChannelFixture).GetMethod(field=="writeNative"?"Write":"Result")));
  }
 }
}
'@
$nonce=[guid]::NewGuid().ToString('N');$server=[IMCStorageChannel]::CreateServer($nonce,'terminal_fixture',10)
$job=[IMCRuntimeJob]::new();$child=0
try {
 $self=[Diagnostics.Process]::GetCurrentProcess();$birth=$self.StartTime.ToUniversalTime().ToFileTimeUtc()
 $child=$job.Start((Join-Path $PSHOME 'powershell.exe'),[string[]]@('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
    '-File',(Join-Path $PSScriptRoot 'storage_terminal_channel_child.ps1'),'-Library',(Join-Path $repo 'TOOLS\IMC_STORAGE_CHANNEL.ps1'),
    '-Nonce',$nonce,'-ParentId',[string]$PID,'-ParentBirth',[string]$birth,'-Case',$Case),$Workspace)
 $job.ObserveMember($child);$held=[Diagnostics.Process]::GetProcessById($child);$null=$held.Handle
 $server.BindPeer($child,$held.StartTime.ToUniversalTime().ToFileTimeUtc());$server.Connect()
 $server.Write([Text.Encoding]::UTF8.GetBytes('ready'))
 if([Text.Encoding]::UTF8.GetString($server.Read()) -cne 'ready ack'){throw 'terminal_fixture_ready'}
 [TerminalChannelFixture]::Attach($server,$Case)
 $expected=switch($Case){'NonzeroExit'{'STORAGE_CHANNEL_TERMINAL_PEER_FAILED'};'UnknownCompletion'{'STORAGE_CHANNEL_DEADLINE'};'PartialWrite'{'STORAGE_CHANNEL_WRITE_PARTIAL'};default{$null}}
 if($expected){
  try{$server.WriteTerminal([Text.Encoding]::UTF8.GetBytes('finish'));throw 'terminal_fixture_fault_admitted'}
  catch{if($_.Exception.GetBaseException().Message -cne $expected){throw}}
 }else{
  $server.WriteTerminal([Text.Encoding]::UTF8.GetBytes('finish'))
  if(-not $held.WaitForExit(1000) -or $held.ExitCode -ne 0){throw 'terminal_fixture_exit0_unconfirmed'}
  if($Case -ceq 'Replay'){
   try{$server.WriteTerminal([Text.Encoding]::UTF8.GetBytes('finish'));throw 'terminal_fixture_replayed'}
   catch{if($_.Exception.GetBaseException().Message -cnotin @('STORAGE_CHANNEL_TERMINAL_CONSUMED','STORAGE_CHANNEL_PEER_EXITED')){throw}}
  }else{$server.Close();if(-not $server.Closed -or $server.Faulted){throw 'terminal_fixture_success_cleanup'}}
 }
 if([TerminalChannelFixture]::Writes -ne 1){throw 'terminal_fixture_send_count'}
 if($Case -cne 'FastExit'){
  if(-not $server.Faulted -or $server.Closed){throw 'terminal_fixture_fault_not_retained'}
  try{$server.Write([Text.Encoding]::UTF8.GetBytes('retry'));throw 'terminal_fixture_retry_admitted'}catch{}
  if([TerminalChannelFixture]::Writes -ne 1){throw 'terminal_fixture_retry_sent'}
 }
 if($Case -ceq 'UnknownCompletion'){
  $operation=$server.GetType().GetField('current',[Reflection.BindingFlags]'Instance,NonPublic').GetValue($server)
  if(-not $operation -or $operation.Confirmed -or -not $operation.Submitted -or $operation.Buffer -eq [IntPtr]::Zero){throw 'terminal_fixture_unknown_memory_lost'}
 }
 'NATIVE_TERMINAL_CHANNEL_FIXTURE_PASS'
}finally{
 $job.ShutdownWithin(10000)
 if(-not $job.CleanupConfirmed){throw 'terminal_fixture_job_cleanup_unconfirmed'}
}
