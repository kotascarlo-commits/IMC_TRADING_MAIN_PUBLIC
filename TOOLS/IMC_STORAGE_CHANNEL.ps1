# Cold native local channel. No process start, Source I/O, mutex or gate authority.
# Pending/unconfirmed native operations retain buffers and handles without retry.
# Channel Write completion is not peer processing or journal durability evidence.
if (-not ('IMCStorageChannel' -as [type])) {
Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text.RegularExpressions;
using System.Threading;

public sealed class IMCStorageChannel {
 [StructLayout(LayoutKind.Sequential)] struct Overlapped {
  public UIntPtr Internal,InternalHigh;public uint Offset,OffsetHigh;public IntPtr Event;
 }
 [StructLayout(LayoutKind.Sequential)] struct FileTime {public uint Low,High;}
 enum Ownership { Empty,Owned,Closing,Closed,Fault }
 sealed class Operation {
  public IntPtr Buffer,Overlap,Event;public Ownership EventState;
  public bool Submitted,Confirmed;public uint Length;
 }
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern IntPtr CreateNamedPipeW(string name,uint open,uint mode,uint instances,uint output,uint input,uint timeout,IntPtr security);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern IntPtr CreateFileW(string name,uint access,uint share,IntPtr security,uint disposition,uint flags,IntPtr template);
 [DllImport("kernel32.dll",SetLastError=true)] [return:MarshalAs(UnmanagedType.Bool)] static extern bool ConnectNamedPipe(IntPtr pipe,IntPtr overlap);
 [DllImport("kernel32.dll",SetLastError=true)] [return:MarshalAs(UnmanagedType.Bool)] static extern bool ReadFile(IntPtr pipe,IntPtr buffer,uint length,out uint bytes,IntPtr overlap);
 [DllImport("kernel32.dll",SetLastError=true)] [return:MarshalAs(UnmanagedType.Bool)] static extern bool WriteFile(IntPtr pipe,IntPtr buffer,uint length,out uint bytes,IntPtr overlap);
 [DllImport("kernel32.dll",SetLastError=true)] [return:MarshalAs(UnmanagedType.Bool)] static extern bool GetOverlappedResult(IntPtr pipe,IntPtr overlap,out uint bytes,[MarshalAs(UnmanagedType.Bool)] bool wait);
 [DllImport("kernel32.dll",SetLastError=true)] static extern uint WaitForSingleObject(IntPtr handle,uint milliseconds);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern IntPtr CreateEventW(IntPtr security,[MarshalAs(UnmanagedType.Bool)] bool manual,[MarshalAs(UnmanagedType.Bool)] bool initial,string name);
 [DllImport("kernel32.dll",SetLastError=true)] static extern IntPtr OpenProcess(uint access,[MarshalAs(UnmanagedType.Bool)] bool inherit,uint pid);
 [DllImport("kernel32.dll",SetLastError=true)] [return:MarshalAs(UnmanagedType.Bool)] static extern bool GetProcessTimes(IntPtr process,out FileTime birth,out FileTime exit,out FileTime kernel,out FileTime user);
 [DllImport("kernel32.dll",SetLastError=true)] [return:MarshalAs(UnmanagedType.Bool)] static extern bool GetExitCodeProcess(IntPtr process,out uint code);
 [DllImport("kernel32.dll",SetLastError=true)] [return:MarshalAs(UnmanagedType.Bool)] static extern bool GetNamedPipeClientProcessId(IntPtr pipe,out uint pid);
 [DllImport("kernel32.dll",SetLastError=true)] [return:MarshalAs(UnmanagedType.Bool)] static extern bool GetNamedPipeServerProcessId(IntPtr pipe,out uint pid);
 [DllImport("kernel32.dll",SetLastError=true)] [return:MarshalAs(UnmanagedType.Bool)] static extern bool CloseHandle(IntPtr handle);
 readonly Func<IntPtr,bool> closeNative=CloseHandle;
 delegate bool IOCall(IntPtr pipe,IntPtr buffer,uint length,out uint bytes,IntPtr overlap);
 delegate bool ResultCall(IntPtr pipe,IntPtr overlap,out uint bytes,bool wait);
 readonly IOCall writeNative=WriteFile,readNative=ReadFile;
 readonly ResultCall resultNative=GetOverlappedResult;
 readonly Func<IntPtr,uint,uint> waitEventNative=WaitForSingleObject;
 static readonly object poolLock=new object();static readonly List<IMCStorageChannel> retained=new List<IMCStorageChannel>();
 readonly object serial=new object();readonly Stopwatch clock=Stopwatch.StartNew();readonly double seconds;
 readonly long endTick;
 long lastTick;
 static readonly Func<long> constructorTicksNative=Stopwatch.GetTimestamp;
 readonly Func<long> ticksNative=constructorTicksNative;
 readonly bool server;readonly string name;
 IntPtr pipe,peer;Ownership pipeState,peerState;uint peerPid;ulong peerBirth;
 bool connected,busy,terminalStarted;int lifecycle;Operation current;
 bool fault {get{return Volatile.Read(ref lifecycle)==1;}}
 bool closed {get{return Volatile.Read(ref lifecycle)==2;}}
 void Fail(){Interlocked.CompareExchange(ref lifecycle,1,0);}
 public bool Faulted {get{return fault;}}
 public bool Closed {get{return closed;}}
 public static int RetainedCount {get{lock(poolLock)return retained.Count;}}
 static Exception Error(int code){return new InvalidOperationException("STORAGE_CHANNEL_NATIVE_"+code);}
 static bool Invalid(IntPtr handle){return handle==IntPtr.Zero || handle==new IntPtr(-1);}
 static void ABI() {
  if(IntPtr.Size!=8 || Marshal.SizeOf(typeof(Overlapped))!=32 ||
     Marshal.OffsetOf(typeof(Overlapped),"Offset").ToInt32()!=16 ||
     Marshal.OffsetOf(typeof(Overlapped),"Event").ToInt32()!=24 || Marshal.SizeOf(typeof(FileTime))!=8)
   throw new InvalidOperationException("STORAGE_CHANNEL_ABI");
 }
 IMCStorageChannel(string nonce,string role,double budget,bool asServer,long sharedEnd=0,long frequency=0) {
  ABI();if(!Regex.IsMatch(nonce??"",@"\A[a-f0-9]{32}\z") || !Regex.IsMatch(role??"",@"\A[a-z][a-z0-9_]{0,31}\z") ||
    Double.IsNaN(budget)||Double.IsInfinity(budget)||budget<=0||budget>21600)throw new InvalidOperationException("STORAGE_CHANNEL_CONFIG");
  if(sharedEnd!=0) {
   long now=ticksNative();
   if(!Stopwatch.IsHighResolution || now<0 || frequency!=Stopwatch.Frequency || sharedEnd<=0 || sharedEnd-now<=1 ||
      ((double)sharedEnd-now)>21600.0*frequency)throw new InvalidOperationException("STORAGE_CHANNEL_SHARED_DEADLINE");
   lastTick=now;
  }else if(frequency!=0)throw new InvalidOperationException("STORAGE_CHANNEL_SHARED_DEADLINE");
  endTick=sharedEnd;seconds=budget;server=asServer;name=@"\\.\pipe\imc-storage-"+nonce+"-"+role;
  lock(poolLock)retained.Add(this);
 }
 public static IMCStorageChannel CreateServer(string nonce,string role,double budget) {
  return OpenServer(new IMCStorageChannel(nonce,role,budget,true));
 }
 public static IMCStorageChannel CreateServerUntil(string nonce,string role,long sharedEnd,long frequency) {
  if(sharedEnd<=0 || frequency<=0)throw new InvalidOperationException("STORAGE_CHANNEL_SHARED_DEADLINE");
  return OpenServer(new IMCStorageChannel(nonce,role,21600,true,sharedEnd,frequency));
 }
 static IMCStorageChannel OpenServer(IMCStorageChannel group) {
  try {
   group.Check();
   // FIRST_PIPE_INSTANCE + OVERLAPPED; byte mode, PIPE_WAIT, reject remote.
   group.pipe=CreateNamedPipeW(group.name,0x40080003,8,1,131076,131076,0,IntPtr.Zero);
   if(Invalid(group.pipe)){int e=Marshal.GetLastWin32Error();group.pipe=IntPtr.Zero;throw Error(e);}
   group.pipeState=Ownership.Owned;group.Check();return group;
  }catch{group.Fail();throw;}
 }
 public static IMCStorageChannel ConnectClient(string nonce,string role,uint pid,long birth,double budget) {
  return OpenClient(new IMCStorageChannel(nonce,role,budget,false),pid,birth);
 }
 public static IMCStorageChannel ConnectClientUntil(string nonce,string role,uint pid,long birth,long sharedEnd,long frequency) {
  if(sharedEnd<=0 || frequency<=0)throw new InvalidOperationException("STORAGE_CHANNEL_SHARED_DEADLINE");
  return OpenClient(new IMCStorageChannel(nonce,role,21600,false,sharedEnd,frequency),pid,birth);
 }
 static IMCStorageChannel OpenClient(IMCStorageChannel group,uint pid,long birth) {
  try {
   group.BindPeer(pid,birth);
   group.Check();
   group.pipe=CreateFileW(group.name,0xC0000000,0,IntPtr.Zero,3,0x40000000,IntPtr.Zero);
   if(Invalid(group.pipe)){int e=Marshal.GetLastWin32Error();group.pipe=IntPtr.Zero;throw Error(e);}
   group.pipeState=Ownership.Owned;group.connected=true;group.PeerCheck();return group;
  }catch{group.Fail();throw;}
 }
 void Enter() {
  // Reject concurrent calls instead of waiting outside the shared deadline.
  if(!Monitor.TryEnter(serial)){Fail();throw new InvalidOperationException("STORAGE_CHANNEL_CONCURRENT");}
  if(busy){Fail();Monitor.Exit(serial);throw new InvalidOperationException("STORAGE_CHANNEL_REENTRANT");}
  busy=true;
 }
 void Leave(){busy=false;Monitor.Exit(serial);}
 void Check() {
  if(fault||closed)throw new InvalidOperationException("STORAGE_CHANNEL_UNCONFIRMED");
  TimeCheck();
  if(peerState==Ownership.Owned) {
   uint wait=WaitForSingleObject(peer,0);
   if(wait==0)throw new InvalidOperationException("STORAGE_CHANNEL_PEER_EXITED");
   if(wait!=258){int e=Marshal.GetLastWin32Error();throw Error(e);}
  }
 }
 public void BindPeer(uint pid,long birth) {
  Enter();try {
   Check();if(peerState!=Ownership.Empty || pid==0 || birth<=0)throw new InvalidOperationException("STORAGE_CHANNEL_PEER_BINDING");
   peer=OpenProcess(0x101000,false,pid);
   if(Invalid(peer)){int e=Marshal.GetLastWin32Error();peer=IntPtr.Zero;throw Error(e);}
   peerState=Ownership.Owned;peerPid=pid;peerBirth=(ulong)birth;
   FileTime actual,exit,kernel,user;
   if(!GetProcessTimes(peer,out actual,out exit,out kernel,out user)){int e=Marshal.GetLastWin32Error();throw Error(e);}
   if((((ulong)actual.High<<32)|actual.Low)!=peerBirth)throw new InvalidOperationException("STORAGE_CHANNEL_PEER_BIRTH");
   Check();
  }catch{Fail();throw;}finally{Leave();}
 }
 void PeerCheck() {
  Check();if(!connected || peerState!=Ownership.Owned)throw new InvalidOperationException("STORAGE_CHANNEL_NOT_CONNECTED");
  uint actual;bool ok=server?GetNamedPipeClientProcessId(pipe,out actual):GetNamedPipeServerProcessId(pipe,out actual);
  if(!ok){int e=Marshal.GetLastWin32Error();throw Error(e);}
  if(actual!=peerPid)throw new InvalidOperationException("STORAGE_CHANNEL_PEER_PID");
  Check();
 }
 Operation Allocate(uint length) {
  if(current!=null)throw new InvalidOperationException("STORAGE_CHANNEL_OPERATION_PENDING");
  Operation op=new Operation();current=op;op.Length=length;
  if(length>0)op.Buffer=Marshal.AllocHGlobal((int)length);
  op.Overlap=Marshal.AllocHGlobal(32);
  op.Event=CreateEventW(IntPtr.Zero,true,false,null);
  if(Invalid(op.Event)){int e=Marshal.GetLastWin32Error();op.Event=IntPtr.Zero;throw Error(e);}
  op.EventState=Ownership.Owned;
  Overlapped data=new Overlapped();data.Event=op.Event;Marshal.StructureToPtr(data,op.Overlap,false);return op;
 }
 uint Complete(Operation op,bool immediate,int code,bool connect,bool terminal=false) {
  if(!immediate && code!=997)throw Error(code);
  uint bytes=0;
  while(true) {
   if(terminal)CleanupCheck();else Check();
   if(!immediate) {
    uint wait=waitEventNative(op.Event,0);
    if(wait==258){Thread.Sleep(2);continue;}
    if(wait!=0){int e=Marshal.GetLastWin32Error();throw Error(e);}
   }
   if(resultNative(pipe,op.Overlap,out bytes,false))break;
   int error=Marshal.GetLastWin32Error();
   if(error==996){immediate=false;Thread.Sleep(2);continue;}
   throw Error(error);
  }
  op.Confirmed=true;if(terminal)CleanupCheck();else Check();return bytes; // Connect count is undefined.
 }
 void CleanupCheck() {
  if(fault||closed)throw new InvalidOperationException("STORAGE_CHANNEL_UNCONFIRMED");
  TimeCheck();
 }
 void TimeCheck() {
  if(endTick!=0) {
   long now=ticksNative();if(now<0 || now<lastTick)throw new InvalidOperationException("STORAGE_CHANNEL_CLOCK_REGRESSED");
   lastTick=now;if(now>=endTick-1)throw new TimeoutException("STORAGE_CHANNEL_DEADLINE");
  }else if(clock.Elapsed.TotalSeconds>=seconds)throw new TimeoutException("STORAGE_CHANNEL_DEADLINE");
 }
 void CloseOwned(ref IntPtr handle,ref Ownership state) {
  CleanupCheck();
  if(state!=Ownership.Owned)throw new InvalidOperationException("STORAGE_CHANNEL_CLOSE_STATE");
  state=Ownership.Closing;
  try{if(!closeNative(handle)){int e=Marshal.GetLastWin32Error();throw Error(e);}handle=IntPtr.Zero;state=Ownership.Closed;}
  catch{state=Ownership.Fault;Fail();throw;}
 }
 void Release(Operation op) {
  if(!op.Confirmed)throw new InvalidOperationException("STORAGE_CHANNEL_COMPLETION_UNCONFIRMED");
  CloseOwned(ref op.Event,ref op.EventState);
  if(op.Buffer!=IntPtr.Zero){CleanupCheck();Marshal.FreeHGlobal(op.Buffer);op.Buffer=IntPtr.Zero;}
  CleanupCheck();Marshal.FreeHGlobal(op.Overlap);op.Overlap=IntPtr.Zero;current=null;
 }
 public void Connect() {
  Enter();try {
   Check();if(terminalStarted || !server || connected || peerState!=Ownership.Owned)throw new InvalidOperationException("STORAGE_CHANNEL_CONNECT_STATE");
   Operation op=Allocate(0);Check();op.Submitted=true;
   bool immediate=ConnectNamedPipe(pipe,op.Overlap);int code=immediate?0:Marshal.GetLastWin32Error();
   if(!immediate && code==535)op.Confirmed=true; // Client arrived before Connect.
   else Complete(op,immediate,code,true);
   connected=true;PeerCheck();Release(op);
  }catch{Fail();throw;}finally{Leave();}
 }
 byte[] ReadExact(int length) {
  byte[] result=new byte[length];int offset=0;
  while(offset<length) {
   PeerCheck();Operation op=Allocate((uint)(length-offset));Check();op.Submitted=true;
   uint ignored;bool immediate=readNative(pipe,op.Buffer,op.Length,out ignored,op.Overlap);int code=immediate?0:Marshal.GetLastWin32Error();
   uint count=Complete(op,immediate,code,false);
   if(count==0 || count>op.Length)throw new InvalidOperationException("STORAGE_CHANNEL_READ_PROGRESS");
   Marshal.Copy(op.Buffer,result,offset,(int)count);offset+=(int)count;Release(op);PeerCheck();
  }
  return result;
 }
 public byte[] Read() {
  Enter();try {
   if(terminalStarted)throw new InvalidOperationException("STORAGE_CHANNEL_TERMINAL_CONSUMED");
   PeerCheck();byte[] header=ReadExact(4);
   uint length=(uint)(header[0]|header[1]<<8|header[2]<<16|header[3]<<24);
   if(length==0||length>131072)throw new InvalidOperationException("STORAGE_CHANNEL_FRAME_SIZE");
   byte[] body=ReadExact((int)length);PeerCheck();return body;
  }catch{Fail();throw;}finally{Leave();}
 }
 public void Write(byte[] body) {
  Enter();try {
   if(terminalStarted)throw new InvalidOperationException("STORAGE_CHANNEL_TERMINAL_CONSUMED");
   PeerCheck();if(body==null||body.Length==0||body.Length>131072)throw new InvalidOperationException("STORAGE_CHANNEL_FRAME_SIZE");
   byte[] stable=(byte[])body.Clone();int length=stable.Length;
   byte[] frame=new byte[length+4];for(int i=0;i<4;i++)frame[i]=(byte)((uint)length>>(i*8));Array.Copy(stable,0,frame,4,length);
   Operation op=Allocate((uint)frame.Length);Marshal.Copy(frame,0,op.Buffer,frame.Length);Check();op.Submitted=true;
   uint ignored;bool immediate=writeNative(pipe,op.Buffer,op.Length,out ignored,op.Overlap);int code=immediate?0:Marshal.GetLastWin32Error();
   uint count=Complete(op,immediate,code,false);
   if(count!=op.Length)throw new InvalidOperationException("STORAGE_CHANNEL_WRITE_PARTIAL");
   Release(op);PeerCheck();
  }catch{Fail();throw;}finally{Leave();}
 }
 // Root-only final command. Confirmed native Write is not Worker success.
 // Root still requires held ExitCode0 BEFORE Job shutdown and whole-Job cleanup.
 // No ordinary I/O after consumption; no resend on unknown completion.
 public void WriteTerminal(byte[] body) {
  Enter();try {
   PeerCheck();if(!server || terminalStarted || body==null || body.Length==0 || body.Length>131072)
    throw new InvalidOperationException("STORAGE_CHANNEL_TERMINAL_STATE");
   byte[] stable=(byte[])body.Clone();int length=stable.Length;
   byte[] frame=new byte[length+4];for(int i=0;i<4;i++)frame[i]=(byte)((uint)length>>(i*8));Array.Copy(stable,0,frame,4,length);
   Operation op=Allocate((uint)frame.Length);Marshal.Copy(frame,0,op.Buffer,frame.Length);
   PeerCheck();terminalStarted=true;op.Submitted=true;
   uint ignored;bool immediate=writeNative(pipe,op.Buffer,op.Length,out ignored,op.Overlap);int code=immediate?0:Marshal.GetLastWin32Error();
   uint count=Complete(op,immediate,code,false,true);
   if(count!=op.Length)throw new InvalidOperationException("STORAGE_CHANNEL_WRITE_PARTIAL");
   Release(op);CleanupCheck();
   uint wait=WaitForSingleObject(peer,0);
   if(wait==0) {
    uint exit;if(!GetExitCodeProcess(peer,out exit)){int e=Marshal.GetLastWin32Error();throw Error(e);}
    if(exit!=0)throw new InvalidOperationException("STORAGE_CHANNEL_TERMINAL_PEER_FAILED");
   }else if(wait!=258){int e=Marshal.GetLastWin32Error();throw Error(e);}
   CleanupCheck();
  }catch{Fail();throw;}finally{Leave();}
 }
 public void Close() {
  Enter();try {
   if(fault||closed||current!=null)throw new InvalidOperationException("STORAGE_CHANNEL_CLOSE_UNCONFIRMED");
   CleanupCheck();
   if(pipeState==Ownership.Owned)CloseOwned(ref pipe,ref pipeState);
   if(peerState==Ownership.Owned)CloseOwned(ref peer,ref peerState);
   CleanupCheck();
   if(Interlocked.CompareExchange(ref lifecycle,2,0)!=0)throw new InvalidOperationException("STORAGE_CHANNEL_UNCONFIRMED");
   lock(poolLock)retained.Remove(this);
  }catch{Fail();throw;}finally{Leave();}
 }
}
'@
}
