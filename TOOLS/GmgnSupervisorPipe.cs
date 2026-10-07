// Minimal local transport. NOT an authenticated source-pin/IPC handshake.
using System;using System.Collections.Generic;using System.Diagnostics;using System.IO;
using System.IO.Pipes;using System.Runtime.InteropServices;using System.Security.Principal;
using System.Text;using System.Threading.Tasks;using Microsoft.Win32.SafeHandles;
public sealed class GmgnSupervisorPipe:IDisposable {
 [StructLayout(LayoutKind.Sequential)]struct Security{public uint Length;public IntPtr Descriptor;[MarshalAs(UnmanagedType.Bool)]public bool Inherit;}
 [DllImport("advapi32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern bool ConvertStringSecurityDescriptorToSecurityDescriptorW(string s,uint version,out IntPtr descriptor,out uint bytes);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern IntPtr CreateNamedPipeW(string name,uint open,uint mode,uint instances,uint output,uint input,uint timeout,ref Security security);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern IntPtr CreateFileW(string name,uint access,uint share,IntPtr security,uint creation,uint flags,IntPtr template);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool SetNamedPipeHandleState(SafePipeHandle h,ref uint mode,IntPtr maximum,IntPtr timeout);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool GetNamedPipeClientProcessId(SafePipeHandle h,out uint pid);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool GetNamedPipeServerProcessId(SafePipeHandle h,out uint pid);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool CancelIoEx(SafePipeHandle h,IntPtr overlapped);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool CloseHandle(IntPtr handle);
 [DllImport("kernel32.dll")]static extern IntPtr LocalFree(IntPtr value);
 static readonly List<GmgnSupervisorPipe> retained=new List<GmgnSupervisorPipe>();
 readonly object sync=new object();readonly Stopwatch clock=Stopwatch.StartNew();readonly int milliseconds;
 readonly bool server;readonly List<Task> operations=new List<Task>();readonly List<byte[]> buffers=new List<byte[]>();
 readonly string fixtureScope;Func<string,int> fixtureNativeFailure=null;
 SafePipeHandle handle;PipeStream stream;IntPtr descriptor,nativeHandle;bool fault,connected,disposed,cleanupFault,handshakeClaimed;
 public GmgnSupervisorPipe(string name,string ownerSid,bool createServer,int timeoutMilliseconds):this(name,ownerSid,createServer,timeoutMilliseconds,null){}
 public GmgnSupervisorPipe(string name,string ownerSid,bool createServer,int timeoutMilliseconds,string ownFixtureScope){
  lock(retained)retained.Add(this);server=createServer;milliseconds=timeoutMilliseconds;
  try{
   if(ownFixtureScope!=null&&!System.Text.RegularExpressions.Regex.IsMatch(ownFixtureScope,@"\AC:\\Users\\<local-user>\\Desktop\\IMC_TRADING_MAIN\\[.]local-tools\\pipe-native-[0-9a-f]{32}\z"))throw Fail("GMGN_PIPE_FIXTURE_SCOPE");
   fixtureScope=ownFixtureScope;
   if(!System.Text.RegularExpressions.Regex.IsMatch(name??"",@"\Aimc-read-bootstrap-[0-9a-f]{32}\z")||
    timeoutMilliseconds<100||timeoutMilliseconds>30000||
    new SecurityIdentifier(ownerSid).Value!=WindowsIdentity.GetCurrent().User.Value)throw Fail("GMGN_PIPE_INPUT");
   string path=@"\\.\pipe\"+name;IntPtr native;
   if(server){
    uint bytes;string sddl="D:P(A;;0x0012019b;;;"+ownerSid+")(A;;0x0012019b;;;SY)";
    if(!ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl,1,out descriptor,out bytes))throw Fail("GMGN_PIPE_SECURITY");
    Security security=new Security{Length=(uint)Marshal.SizeOf(typeof(Security)),Descriptor=descriptor,Inherit=false};
    // First instance, overlapped duplex; message mode; reject remote clients.
    native=CreateNamedPipeW(path,0x40080003,0x0e,1,32768,32768,5000,ref security);
   }else{
    // Individual read/write/attribute/control/synchronize rights, NOT generic
    // write (which includes FILE_CREATE_PIPE_INSTANCE). No inherited handles.
    native=CreateFileW(path,0x0012019b,0,IntPtr.Zero,3,0x40000000,IntPtr.Zero);
   }
   if(native==IntPtr.Zero||native==new IntPtr(-1))throw Fail("GMGN_PIPE_NATIVE_OPEN");
   nativeHandle=native;
   // Native close is separately checked after all overlapped operations end.
   handle=new SafePipeHandle(native,false);
   if(server)stream=new NamedPipeServerStream(PipeDirection.InOut,true,false,handle);
   else{
    uint mode=2;if(!SetNamedPipeHandleState(handle,ref mode,IntPtr.Zero,IntPtr.Zero))throw Fail("GMGN_PIPE_MESSAGE_MODE");
    stream=new NamedPipeClientStream(PipeDirection.InOut,true,true,handle);connected=true;
   }
   // Wrapping an existing handle initializes the managed read-mode cache to
   // Byte. Set both the native mode and the stream's managed mode explicitly.
   stream.ReadMode=PipeTransmissionMode.Message;
   if(descriptor!=IntPtr.Zero){if(LocalFree(descriptor)!=IntPtr.Zero)throw Fail("GMGN_PIPE_SECURITY_FREE");descriptor=IntPtr.Zero;}
   Check();
  }catch{Fault();throw;}
 }
 static InvalidOperationException Fail(string code){return new InvalidOperationException(code);}
 int FixtureFailure(string phase){
  if(fixtureNativeFailure==null)return 0;
  if(fixtureScope==null)throw Fail("GMGN_PIPE_FIXTURE_FORBIDDEN");
  return fixtureNativeFailure(phase);
 }
 int Remaining(){long left=milliseconds-clock.ElapsedMilliseconds;if(left<=0)throw Fail("GMGN_PIPE_DEADLINE");return (int)left;}
 void Check(){if(fault||disposed||handle==null||handle.IsInvalid||handle.IsClosed)throw Fail("GMGN_PIPE_FAULT");Remaining();}
 void Fault(){
  fault=true;
  if(handle!=null&&!handle.IsInvalid&&!handle.IsClosed){
   if(FixtureFailure("CANCEL")!=0)cleanupFault=true;
   else if(!CancelIoEx(handle,IntPtr.Zero)&&Marshal.GetLastWin32Error()!=1168)cleanupFault=true;
   // Pending operations, buffers and their stream remain strongly held if
   // native cancellation cannot be confirmed within this cleanup bound.
   var wait=Stopwatch.StartNew();
   foreach(Task task in operations){while(!task.IsCompleted&&wait.ElapsedMilliseconds<2000)System.Threading.Thread.Sleep(1);if(!task.IsCompleted)cleanupFault=true;}
  }
 }
 void Await(Task task){
  operations.Add(task);
  int left=Remaining();
  if(!((IAsyncResult)task).AsyncWaitHandle.WaitOne(left))throw Fail("GMGN_PIPE_DEADLINE");
  try{task.GetAwaiter().GetResult();}catch(IOException){throw Fail("GMGN_PIPE_DISCONNECTED");}Check();
 }
 Task Begin(Func<Task> operation){
  try{return operation();}catch(IOException){
   cleanupFault=true;Fault();throw Fail("GMGN_PIPE_DISCONNECTED");
  }catch{
   // No returned Task means its completion cannot be tracked reliably.
   cleanupFault=true;Fault();throw;
  }
 }
 public void Accept(){lock(sync)try{
  Check();if(!server||connected)throw Fail("GMGN_PIPE_ACCEPT_STATE");
  var named=(NamedPipeServerStream)stream;
  Await(Begin(delegate{return Task.Factory.FromAsync(named.BeginWaitForConnection,named.EndWaitForConnection,null);}));connected=true;Check();
 }catch{Fault();throw;}}
 public int ReadKernelPeerPid(){lock(sync)try{
  Check();if(!connected)throw Fail("GMGN_PIPE_NOT_CONNECTED");uint peer;
  bool ok=server?GetNamedPipeClientProcessId(handle,out peer):GetNamedPipeServerProcessId(handle,out peer);
  if(!ok||peer==0||peer>Int32.MaxValue)throw Fail("GMGN_PIPE_KERNEL_PEER");Check();return (int)peer;
 }catch{Fault();throw;}}
 public void WriteMessage(byte[] value){lock(sync)try{
  Check();if(!connected||value==null||value.Length==0||value.Length>32768)throw Fail("GMGN_PIPE_MESSAGE_SIZE");
  byte[] copy=(byte[])value.Clone();buffers.Add(copy);Await(Begin(delegate{return stream.WriteAsync(copy,0,copy.Length);}));
 }catch{Fault();throw;}}
 public byte[] ReadMessage(){lock(sync)try{
  Check();if(!connected)throw Fail("GMGN_PIPE_NOT_CONNECTED");
  using(var output=new MemoryStream()){
   do{
    // One extra byte detects oversize messages without accepting truncation.
    int room=32769-(int)output.Length;if(room<=0)throw Fail("GMGN_PIPE_MESSAGE_SIZE");
    byte[] bytes=new byte[Math.Min(room,4096)];buffers.Add(bytes);Task<int> read=(Task<int>)Begin(delegate{return stream.ReadAsync(bytes,0,bytes.Length);});Await(read);
    int count=read.GetAwaiter().GetResult();if(count==0)throw Fail("GMGN_PIPE_DISCONNECTED");
    output.Write(bytes,0,count);if(output.Length>32768)throw Fail("GMGN_PIPE_MESSAGE_SIZE");
   }while(!stream.IsMessageComplete);
   Check();return output.ToArray();
  }
 }catch{Fault();throw;}}
 public void Abort(){lock(sync)Fault();}
 public void Dispose(){lock(sync){
  if(disposed)return;Fault();if(cleanupFault)return;
  try{
   if(stream!=null)stream.Dispose();else if(handle!=null)handle.Dispose();
   if(nativeHandle!=IntPtr.Zero){if(FixtureFailure("CLOSE")!=0||!CloseHandle(nativeHandle))throw Fail("GMGN_PIPE_CLOSE_UNCONFIRMED");nativeHandle=IntPtr.Zero;}
   if(descriptor!=IntPtr.Zero){if(LocalFree(descriptor)!=IntPtr.Zero)throw Fail("GMGN_PIPE_SECURITY_FREE");descriptor=IntPtr.Zero;}
  }catch{cleanupFault=true;throw;}
  disposed=true;
  operations.Clear();buffers.Clear();stream=null;handle=null;
  lock(retained)retained.Remove(this);
 }}
 public string State{get{lock(sync)return fault||disposed?"FAULT":connected?"CONNECTED":"LISTENING";}}
 public bool CleanupFault{get{lock(sync)return cleanupFault;}}
 internal bool IsServerSide{get{return server;}}
 internal void ClaimHandshake(){lock(sync)try{
  Check();if(!connected)throw Fail("GMGN_PIPE_NOT_CONNECTED");
  if(handshakeClaimed)throw Fail("GMGN_PIPE_HANDSHAKE_CLAIMED");handshakeClaimed=true;Check();
 }catch{Fault();throw;}}
}
