param([string]$Library,[string]$Nonce,[int]$ParentId,[long]$ParentBirth,[string]$Workspace,[string]$Case,[long]$EndTick=0,[long]$Frequency=0)
$ErrorActionPreference='Stop'
. $Library
if($Case -ceq 'ConnectTimeout'){Start-Sleep -Seconds 20;exit 0}
if($EndTick -ne 0){$client=[IMCStorageChannel]::ConnectClientUntil($Nonce,'fixture',$ParentId,$ParentBirth,$EndTick,$Frequency)}
else{$client=[IMCStorageChannel]::ConnectClient($Nonce,'fixture',$ParentId,$ParentBirth,15)}
[IO.File]::WriteAllText((Join-Path $Workspace 'connected'),'native client connected')
if($Case -ceq 'ZeroRead'){$client.Write([byte[]]@(1));[IO.File]::WriteAllText((Join-Path $Workspace 'data-written'),'native Write completed');Start-Sleep -Seconds 20;exit 0}
if($Case -cin @('ReadTimeout','UntilReadTimeout','Concurrent','WriteTimeout','PartialWrite','WaitFailed','CloseAfterDeadline')){Start-Sleep -Seconds 20;exit 0}
if($Case -ceq 'PeerExit') { $client.Close();exit 0 }
if($Case -cin @('Oversize','Empty')) {
    # Deliberately bypass framing only in the isolated malicious peer fixture.
    $pipe=$client.GetType().GetField('pipe',[Reflection.BindingFlags]'Instance,NonPublic').GetValue($client)
    Add-Type -TypeDefinition @'
using System;using System.Runtime.InteropServices;
public static class ChannelMalformedFixture {
 [StructLayout(LayoutKind.Sequential)] struct O {public UIntPtr A,B;public uint C,D;public IntPtr Event;}
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool WriteFile(IntPtr p,IntPtr b,uint n,out uint c,IntPtr o);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool GetOverlappedResult(IntPtr p,IntPtr o,out uint c,bool wait);
 [DllImport("kernel32.dll",SetLastError=true)] static extern IntPtr CreateEvent(IntPtr s,bool manual,bool initial,string name);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool CloseHandle(IntPtr h);
 public static void Write(IntPtr pipe,bool empty) {
  // Unmanaged storage is never reclaimed on an unconfirmed operation.
  IntPtr buffer=Marshal.AllocHGlobal(4),overlap=Marshal.AllocHGlobal(32);
  byte[] b=empty?new byte[4]:new byte[]{1,0,2,0};Marshal.Copy(b,0,buffer,4);
  O o=new O();o.Event=CreateEvent(IntPtr.Zero,true,false,null);
  if(o.Event==IntPtr.Zero)throw new Exception("fixture_malformed_event");
  Marshal.StructureToPtr(o,overlap,false);uint count;
  if(!WriteFile(pipe,buffer,4,out count,overlap) && Marshal.GetLastWin32Error()!=997)throw new Exception("fixture_malformed_write");
  if(!GetOverlappedResult(pipe,overlap,out count,true)||count!=4)throw new Exception("fixture_malformed_complete");
  if(!CloseHandle(o.Event))throw new Exception("fixture_malformed_close");
  Marshal.FreeHGlobal(buffer);Marshal.FreeHGlobal(overlap);
 }
}
'@
    [ChannelMalformedFixture]::Write($pipe,($Case -ceq 'Empty'))
    Start-Sleep -Seconds 20;exit 0
}
$request=[Text.UTF8Encoding]::new($false,$true).GetString($client.Read())
if($request -cne 'parent phase request'){throw 'fixture_request_mismatch'}
$client.Write([Text.Encoding]::UTF8.GetBytes('worker completion'))
$finish=[Text.Encoding]::UTF8.GetString($client.Read())
if($finish -cne 'exit permitted'){throw 'fixture_final_mismatch'}
$client.Close()
if(-not $client.Closed -or $client.Faulted -or [IMCStorageChannel]::RetainedCount -ne 0){throw 'fixture_client_close_unconfirmed'}
