// Healthy read-only admission, independent of debugger repair. No order send.
using System;using System.Collections.Generic;using System.Diagnostics;using System.IO;using System.Runtime.InteropServices;using System.Text;using System.Text.RegularExpressions;using System.Threading;
public sealed class GmgnReadProcessLifetime {
 [StructLayout(LayoutKind.Sequential)]struct Basic{public IntPtr Exit,Peb,Affinity,Priority,Pid,Parent;}
 [StructLayout(LayoutKind.Sequential)]struct Time{public uint Low,High;}
 [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)]struct Startup{public uint Size;public string Reserved,Desktop,Title;public uint X,Y,Width,Height,CharsX,CharsY,Fill,Flags;public ushort Show,ReservedSize;public IntPtr ReservedBytes,In,Out,Error;}
 [StructLayout(LayoutKind.Sequential)]struct PI{public IntPtr Process,Thread;public uint Id,ThreadId;}
 [StructLayout(LayoutKind.Sequential)]struct StartupEx{public Startup Startup;public IntPtr Attributes;}
 [StructLayout(LayoutKind.Sequential)]struct Security{public int Length;public IntPtr Descriptor;[MarshalAs(UnmanagedType.Bool)]public bool Inherit;}
 [StructLayout(LayoutKind.Sequential)]struct JobBasic{public long PerProcess,PerJob;public uint Flags;public UIntPtr Min,Max;public uint Count;public UIntPtr Affinity;public uint Priority,Scheduling;}
 [StructLayout(LayoutKind.Sequential)]struct JobIO{public ulong ReadCount,WriteCount,OtherCount,ReadBytes,WriteBytes,OtherBytes;}
 [StructLayout(LayoutKind.Sequential)]struct JobExtended{public JobBasic Basic;public JobIO IO;public UIntPtr ProcessMemory,JobMemory,PeakProcess,PeakJob;}
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern IntPtr CreateJobObject(IntPtr security,string name);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool SetInformationJobObject(IntPtr job,int kind,ref JobExtended value,uint bytes);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool TerminateJobObject(IntPtr job,uint code);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern bool CreateProcessW(string app,StringBuilder cmd,IntPtr pa,IntPtr ta,bool inherit,uint flags,IntPtr env,string cwd,ref StartupEx startup,out PI info);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool InitializeProcThreadAttributeList(IntPtr list,int count,uint flags,ref UIntPtr bytes);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool UpdateProcThreadAttribute(IntPtr list,uint flags,UIntPtr key,IntPtr value,UIntPtr bytes,IntPtr previous,IntPtr returned);
 [DllImport("kernel32.dll")]static extern void DeleteProcThreadAttributeList(IntPtr list);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool CreatePipe(out IntPtr read,out IntPtr write,ref Security sa,uint bytes);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool SetHandleInformation(IntPtr h,uint mask,uint flags);
 [DllImport("kernel32.dll",SetLastError=true)]static extern uint ResumeThread(IntPtr h);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool GetExitCodeProcess(IntPtr h,out uint code);
 [DllImport("kernel32.dll",SetLastError=true)]static extern IntPtr OpenProcess(uint access,bool inherit,uint pid);
 [DllImport("ntdll.dll")]static extern int NtQueryInformationProcess(IntPtr h,int c,out Basic b,uint n,out uint actual);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool GetProcessTimes(IntPtr h,out Time b,out Time e,out Time k,out Time u);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern bool QueryFullProcessImageNameW(IntPtr h,uint flags,StringBuilder p,ref uint n);
 [DllImport("kernel32.dll",SetLastError=true)]static extern uint WaitForSingleObject(IntPtr h,uint milliseconds);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool TerminateProcess(IntPtr h,uint code);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool CloseHandle(IntPtr h);
 internal sealed class Child{internal IntPtr Handle,Thread;internal readonly IntPtr[] Pipes=new IntPtr[6];internal StreamReader Out,Error;internal StreamWriter In;internal string Phase="PRESTART";internal bool ExitConfirmed,Disposed,DisposeRequested,TicketIssued,Assigned,TerminationRequested;internal int ExitCode,Waiters;}
 public sealed class ReadTicket:IDisposable{
  readonly GmgnReadProcessLifetime owner;internal readonly Child child;
  internal ReadTicket(GmgnReadProcessLifetime o,Child c){owner=o;child=c;}
  public StreamReader StandardOutput{get{return child.Out;}}
  public StreamReader StandardError{get{return child.Error;}}
  public StreamWriter StandardInput{get{return child.In;}}
  public bool HasExited{get{return owner.TicketExited(child);}}
  public int ExitCode{get{lock(owner.sync){if(!owner.TicketExited(child))throw new InvalidOperationException("GMGN_READ_NATIVE_NOT_EXITED");return child.ExitCode;}}}
  public bool WaitForExit(int milliseconds){return owner.TicketWait(child,milliseconds);}
  public void Kill(){lock(owner.sync)owner.StopChild(child);}
  public void Dispose(){owner.DisposeTicket(child);}
 }
 static readonly List<GmgnReadProcessLifetime> retained=new List<GmgnReadProcessLifetime>();
 readonly object sync=new object();readonly GmgnImmutableSources sources;readonly string node,entry,bootstrap,prefix;
 readonly IntPtr parent,job;readonly List<Child> children=new List<Child>(),resources=new List<Child>();
 bool fault,complete,cleanupFault,jobStopIssued;readonly Thread observer;
 // Only scoped own fixtures may inject a failure after native handle capture.
 Action afterCreateFixtureProbe=null;
 Action afterGenerationTicketFixtureProbe=null;
 public GmgnReadProcessLifetime(GmgnImmutableSources publicSources,string nodeExe,string cliEntry,string readBootstrap,int parentPid,string parentBirth,string parentExecutable){
  lock(retained)retained.Add(this);
  try{
   if(publicSources==null||publicSources.GetType()!=typeof(GmgnImmutableSources)||publicSources.GetType().Assembly!=GetType().Assembly||
    parentPid<=0||String.IsNullOrEmpty(parentExecutable)||nodeExe!=@"C:\Program Files\nodejs\node.exe"||String.IsNullOrEmpty(cliEntry)||String.IsNullOrEmpty(readBootstrap)||
    cliEntry.IndexOf('"')>=0||readBootstrap.IndexOf('"')>=0)throw new InvalidOperationException("GMGN_READ_NATIVE_INPUT");
   sources=publicSources;node=nodeExe;entry=cliEntry;bootstrap=readBootstrap;
   sources.Check();foreach(string p in new string[]{node,entry,bootstrap})if(!sources.Contains(p))throw new InvalidOperationException("GMGN_READ_NATIVE_UNPINNED");
   parent=OpenProcess(0x100400,false,(uint)parentPid);if(parent==IntPtr.Zero)throw new InvalidOperationException("GMGN_READ_NATIVE_PARENT_OPEN");
   // PID/birth/executable are checked on exactly the private held object.
   Basic b;uint actual;Time created,e,k,u;var path=new StringBuilder(32768);uint count=(uint)path.Capacity;
   if(Exited(parent)||NtQueryInformationProcess(parent,0,out b,(uint)Marshal.SizeOf(typeof(Basic)),out actual)!=0||actual!=Marshal.SizeOf(typeof(Basic))||b.Pid.ToInt64()!=parentPid||
    !GetProcessTimes(parent,out created,out e,out k,out u)||!QueryFullProcessImageNameW(parent,0,path,ref count)||count==0||count>=path.Capacity||
    !String.Equals(path.ToString(),parentExecutable,StringComparison.OrdinalIgnoreCase)||
    DateTime.FromFileTimeUtc((long)(((ulong)created.High<<32)|created.Low)).ToString("o")!=parentBirth||Exited(parent))throw new InvalidOperationException("GMGN_READ_NATIVE_PARENT_BINDING");
   job=CreateJobObject(IntPtr.Zero,null);if(job==IntPtr.Zero)throw Failure("GMGN_READ_NATIVE_JOB_CREATE");
   var limits=new JobExtended();limits.Basic.Flags=0x2000;
   if(!SetInformationJobObject(job,9,ref limits,(uint)Marshal.SizeOf(typeof(JobExtended))))throw Failure("GMGN_READ_NATIVE_JOB_LIMIT");
   prefix="--import \""+new Uri(bootstrap).AbsoluteUri+"\" \""+entry+"\" ";ParentAlive();
   observer=new Thread(Observe);observer.IsBackground=true;observer.Name="IMC-GMGN-read-parent";complete=true;observer.Start();
  }catch{fault=true;throw;}
 }
 static bool Exited(IntPtr h){uint w=WaitForSingleObject(h,0);if(w==0)return true;if(w==258)return false;throw new InvalidOperationException("GMGN_READ_NATIVE_WAIT");}
 void ParentAlive(){if(parent==IntPtr.Zero||Exited(parent))throw new InvalidOperationException("GMGN_READ_NATIVE_PARENT_ENDED");}
 static bool Match(string value,string pattern){return Regex.IsMatch(value,pattern,RegexOptions.CultureInvariant);}
 static bool ReadArgs(string text){
  string[] a=text.Split(' ');if(a.Length<3||a.Length>64)return false;
  foreach(string arg in a)if(arg.Length==0||arg.Length>4096||!Match(arg,@"\A[A-Za-z0-9_./+=:-]+\z"))return false;
  string command=a[0]+" "+a[1];if(command=="portfolio info")return a.Length==3&&a[2]=="--raw";
  string[] allowed,required;
  switch(command){
   case "portfolio holdings":allowed=new string[]{"--chain","--wallet","--limit","--cursor","--hide-airdrop","--hide-abnormal","--hide-closed"};required=new string[]{"--chain","--wallet"};break;
   case "order get":allowed=required=new string[]{"--chain","--order-id"};break;
   case "order quote":allowed=required=new string[]{"--chain","--from","--input-token","--output-token","--amount","--slippage"};break;
   default:return false;
  }
  var options=new Dictionary<string,string>(StringComparer.Ordinal);
  for(int i=2;i<a.Length;i++){
   string key=a[i];if(options.ContainsKey(key))return false;
   if(key=="--raw"){options.Add(key,"true");continue;}
   if(Array.IndexOf(allowed,key)<0||++i>=a.Length)return false;string value=a[i];
   switch(key){
    case "--chain":if(value!="sol")return false;break;
    case "--wallet":case "--from":case "--input-token":case "--output-token":if(!Match(value,@"\A[1-9A-HJ-NP-Za-km-z]{32,44}\z"))return false;break;
    case "--amount":ulong amount;if(!Match(value,@"\A[1-9][0-9]{0,19}\z")||!UInt64.TryParse(value,out amount))return false;break;
    case "--slippage":if(value!="1")return false;break;
    case "--order-id":if(!Match(value,@"\A[A-Za-z0-9_-]{1,128}\z"))return false;break;
    case "--limit":if(!Match(value,@"\A(?:[1-9]|[1-4][0-9]|50)\z"))return false;break;
    case "--cursor":if(!Match(value,@"\A[A-Za-z0-9_+/=-]{1,2048}\z"))return false;break;
    default:if(value!="true"&&value!="false")return false;break;
   }options.Add(key,value);
  }
  if(!options.ContainsKey("--raw"))return false;foreach(string key in required)if(!options.ContainsKey(key))return false;
  return command!="order quote"||options["--input-token"]!=options["--output-token"];
 }
 void StopChild(Child child){if(child.ExitConfirmed||child.Disposed||child.TerminationRequested)return;if(child.Handle==IntPtr.Zero){cleanupFault=true;throw new InvalidOperationException("GMGN_READ_NATIVE_CHILD_UNKNOWN");}
  if(!Exited(child.Handle)){bool sent=TerminateProcess(child.Handle,125);if(sent)child.TerminationRequested=true;else if(!Exited(child.Handle)){cleanupFault=true;throw new InvalidOperationException("GMGN_READ_NATIVE_CHILD_TERMINATE");}}
 }
 void StopChildren(){foreach(Child child in children)try{StopChild(child);}catch{cleanupFault=true;}}
 void Fault(){fault=true;if(job!=IntPtr.Zero&&!jobStopIssued){
  if(TerminateJobObject(job,125)){jobStopIssued=true;foreach(Child child in children)if(child.Assigned)child.TerminationRequested=true;}else cleanupFault=true;
 }StopChildren();}
 bool TicketExited(Child child){lock(sync){if(child.ExitConfirmed)return true;if(child.Handle==IntPtr.Zero)return false;
  if(!Exited(child.Handle))return false;uint code;if(!GetExitCodeProcess(child.Handle,out code))throw Failure("GMGN_READ_NATIVE_EXIT_CODE");child.ExitCode=unchecked((int)code);child.ExitConfirmed=true;child.Phase="EXITED";return true;}}
 bool TicketWait(Child child,int milliseconds){if(milliseconds<0||milliseconds>30000)throw new InvalidOperationException("GMGN_READ_NATIVE_WAIT_INPUT");
  lock(sync){if(child.ExitConfirmed)return true;if(child.Handle==IntPtr.Zero||child.Disposed||child.DisposeRequested)throw new InvalidOperationException("GMGN_READ_NATIVE_TICKET_UNKNOWN");child.Waiters++;}
  // Process stays private/alive. Reap never disposes it; this wait must not
  // hold sync and prevent the parent observer from canceling the child.
  try{uint result=WaitForSingleObject(child.Handle,(uint)milliseconds);if(result==0)return TicketExited(child);if(result==258)return false;throw Failure("GMGN_READ_NATIVE_WAIT");}
  finally{lock(sync){child.Waiters--;if(child.Waiters==0&&child.DisposeRequested&&child.ExitConfirmed)CloseTicket(child);}}
 }
 void CloseTicket(Child child){if(child.Disposed)return;if(!child.ExitConfirmed||child.Waiters!=0)throw new InvalidOperationException("GMGN_READ_NATIVE_TICKET_CLOSE_ORDER");
  Exception error=null;
  foreach(IDisposable stream in new IDisposable[]{child.In,child.Out,child.Error})if(stream!=null)try{stream.Dispose();}catch(Exception e){if(error==null)error=e;}
  try{CloseResources(child);}catch(Exception e){if(error==null)error=e;}
  bool closed=child.Handle==IntPtr.Zero&&child.Thread==IntPtr.Zero;foreach(IntPtr pipe in child.Pipes)if(pipe!=IntPtr.Zero)closed=false;
  if(closed){child.Disposed=true;children.Remove(child);resources.Remove(child);}
  if(error!=null){cleanupFault=true;Fault();throw new InvalidOperationException("GMGN_READ_NATIVE_TICKET_CLEANUP",error);}
 }
 void DisposeTicket(Child child){if(!TicketExited(child)){lock(sync)StopChild(child);if(!TicketWait(child,2000)){lock(sync){cleanupFault=true;Fault();}throw new InvalidOperationException("GMGN_READ_NATIVE_CHILD_EXIT_UNCONFIRMED");}}
  lock(sync){if(child.Disposed)return;child.DisposeRequested=true;if(child.Waiters==0)CloseTicket(child);}}
 void Reap(){for(int i=children.Count-1;i>=0;i--){Child child=children[i];if(TicketExited(child)){if(child.TicketIssued)children.RemoveAt(i);else CloseTicket(child);}}}
 void Observe(){while(true){lock(sync)try{Reap();if(!fault){ParentAlive();sources.Check();}}catch{Fault();}Thread.Sleep(100);}}
 internal ReadTicket StartReadWithinGeneration(GmgnImmutableSources firstGeneration,string[] cliArguments){
  ReadTicket ticket=null;
  try{lock(sync){
   if(firstGeneration==null||firstGeneration.GetType()!=typeof(GmgnImmutableSources)||firstGeneration.GetType().Assembly!=GetType().Assembly||Object.ReferenceEquals(firstGeneration,sources))throw new InvalidOperationException("GMGN_READ_NATIVE_GENERATION_CONTEXT");
   // Observer and dispatch both acquire Factory before a Source lock.
   firstGeneration.InHealthyGeneration(delegate{sources.InHealthyGeneration(delegate{
    ticket=StartRead(cliArguments);ticket.child.TicketIssued=false;
    if(afterGenerationTicketFixtureProbe!=null){
     if(!Match(entry,@"\\[.]local-tools\\(?:legacy-capture-native-[0-9a-f]{32}\\primary|healthy-read-native-[0-9a-f]{32})\\entry[.]mjs\z"))throw new InvalidOperationException("GMGN_READ_NATIVE_FIXTURE_SCOPE");
     afterGenerationTicketFixtureProbe();
    }
   });});
   ticket.child.TicketIssued=true;return ticket;
  }}catch{
   lock(sync){if(ticket!=null)ticket.child.TicketIssued=false;Fault();}
   // The caller never received this ticket. Cleanup must not wait while any
   // Factory or Source lock is held; Reap can also collect the unissued child.
   if(ticket!=null)try{ticket.Dispose();}catch{lock(sync){cleanupFault=true;Fault();}}
   throw;
  }
 }
 public ReadTicket StartRead(string[] cliArguments){lock(sync)try{
  if(fault||!complete)throw new InvalidOperationException("GMGN_READ_NATIVE_FAULT");ParentAlive();sources.Check();Reap();
  if(resources.Count!=0)throw new InvalidOperationException("GMGN_READ_NATIVE_ACTIVE_CHILD");
  if(cliArguments==null)throw new InvalidOperationException("GMGN_READ_NATIVE_COMMAND_BLOCKED");
  string[] args=(string[])cliArguments.Clone();foreach(string arg in args)if(arg==null)throw new InvalidOperationException("GMGN_READ_NATIVE_COMMAND_BLOCKED");
  string command=String.Join(" ",args);if(!ReadArgs(command)||command.Split(' ').Length!=args.Length)throw new InvalidOperationException("GMGN_READ_NATIVE_COMMAND_BLOCKED");
  ParentAlive();sources.Check();Child child=new Child();children.Add(child);resources.Add(child);
  // OS parent exit can occur between checks. A read already started then is
  // canceled through its private original handle, never admitted as successful.
  try{
   SetupPipes(child);var start=new StartupEx();start.Startup.Size=(uint)Marshal.SizeOf(typeof(StartupEx));start.Startup.Flags=0x100;start.Startup.In=child.Pipes[0];start.Startup.Out=child.Pipes[3];start.Startup.Error=child.Pipes[5];
   PI info;IntPtr environment=IntPtr.Zero,attributes=IntPtr.Zero,jobList=IntPtr.Zero,handleList=IntPtr.Zero;bool initialized=false;
   try{
    UIntPtr bytes=UIntPtr.Zero;bool sized=InitializeProcThreadAttributeList(IntPtr.Zero,2,0,ref bytes);int sizeError=Marshal.GetLastWin32Error();
    if(sized||sizeError!=122||bytes.ToUInt64()==0||bytes.ToUInt64()>1048576)throw Failure("GMGN_READ_NATIVE_ATTRIBUTES_SIZE",sizeError);
    attributes=Marshal.AllocHGlobal((int)bytes.ToUInt64());if(!InitializeProcThreadAttributeList(attributes,2,0,ref bytes))throw Failure("GMGN_READ_NATIVE_ATTRIBUTES_INIT");initialized=true;
    jobList=Marshal.AllocHGlobal(IntPtr.Size);Marshal.WriteIntPtr(jobList,job);
    if(!UpdateProcThreadAttribute(attributes,0,new UIntPtr(0x2000D),jobList,new UIntPtr((uint)IntPtr.Size),IntPtr.Zero,IntPtr.Zero))throw Failure("GMGN_READ_NATIVE_JOB_ATTRIBUTE");
    handleList=Marshal.AllocHGlobal(IntPtr.Size*3);Marshal.WriteIntPtr(handleList,0,child.Pipes[0]);Marshal.WriteIntPtr(handleList,IntPtr.Size,child.Pipes[3]);Marshal.WriteIntPtr(handleList,IntPtr.Size*2,child.Pipes[5]);
    if(!UpdateProcThreadAttribute(attributes,0,new UIntPtr(0x20002),handleList,new UIntPtr((uint)(IntPtr.Size*3)),IntPtr.Zero,IntPtr.Zero))throw Failure("GMGN_READ_NATIVE_HANDLE_ATTRIBUTE");
    start.Attributes=attributes;environment=Marshal.StringToHGlobalUni(ReadEnvironment());
    bool created=CreateProcessW(node,new StringBuilder("\""+node+"\" "+prefix+command),IntPtr.Zero,IntPtr.Zero,true,0x08080404,environment,null,ref start,out info);
    int error=Marshal.GetLastWin32Error();
    // Capture original handles immediately after native creation, before any
    // wrappers, callbacks, further validation or ResumeThread.
    child.Handle=info.Process;child.Thread=info.Thread;
    if(!created){child.Phase=child.Handle==IntPtr.Zero&&child.Thread==IntPtr.Zero?"NOT_STARTED":"UNKNOWN";if(child.Phase=="UNKNOWN")cleanupFault=true;throw Failure("GMGN_READ_NATIVE_START_FAILED",error);}
    child.Phase="PINNED_SUSPENDED";child.Assigned=true;
    if(afterCreateFixtureProbe!=null){if(!Match(entry,@"\\[.]local-tools\\healthy-read-native-[0-9a-f]{32}\\entry[.]mjs\z"))throw new InvalidOperationException("GMGN_READ_NATIVE_FIXTURE_SCOPE");afterCreateFixtureProbe();}
   }finally{if(initialized)DeleteProcThreadAttributeList(attributes);if(attributes!=IntPtr.Zero)Marshal.FreeHGlobal(attributes);if(jobList!=IntPtr.Zero)Marshal.FreeHGlobal(jobList);if(handleList!=IntPtr.Zero)Marshal.FreeHGlobal(handleList);if(environment!=IntPtr.Zero)Marshal.FreeHGlobal(environment);}
   if(child.Handle==IntPtr.Zero||child.Thread==IntPtr.Zero){child.Phase="UNKNOWN";cleanupFault=true;throw new InvalidOperationException("GMGN_READ_NATIVE_CREATION_HANDLES");}
   Close(ref child.Pipes[0]);Close(ref child.Pipes[3]);Close(ref child.Pipes[5]);
   child.In=new StreamWriter(Stream(child.Pipes[1],FileAccess.Write),new UTF8Encoding(false));child.In.AutoFlush=true;
   child.Out=new StreamReader(Stream(child.Pipes[2],FileAccess.Read),Encoding.UTF8,false,4096);
   child.Error=new StreamReader(Stream(child.Pipes[4],FileAccess.Read),Encoding.UTF8,false,4096);
   ParentAlive();sources.Check();if(ResumeThread(child.Thread)!=1)throw Failure("GMGN_READ_NATIVE_RESUME");child.Phase="RUNNING";Close(ref child.Thread);
  }catch{
   if(child.Phase=="PRESTART"||child.Phase=="NOT_STARTED"){try{CloseResources(child);children.Remove(child);resources.Remove(child);}catch{child.Phase="UNKNOWN";cleanupFault=true;}}
   else if(child.Handle==IntPtr.Zero){child.Phase="UNKNOWN";cleanupFault=true;}
   throw;
  }
  ParentAlive();sources.Check();ReadTicket ticket=new ReadTicket(this,child);child.TicketIssued=true;return ticket;
 }catch{Fault();throw;}}
 public void Check(){lock(sync)try{if(!complete||fault||cleanupFault)throw new InvalidOperationException("GMGN_READ_NATIVE_FAULT");ParentAlive();sources.Check();Reap();}catch{Fault();throw;}}
 public void Abort(){lock(sync)Fault();}
 public string[] Status(){lock(sync)return new string[]{fault?"FAULT":"READ_ENABLED",cleanupFault.ToString(),children.Count.ToString(),resources.Count.ToString()};}
 static InvalidOperationException Failure(string code,int error){var e=new InvalidOperationException(code);e.Data["native_error"]=error;return e;}
 static InvalidOperationException Failure(string code){return Failure(code,Marshal.GetLastWin32Error());}
 void Close(ref IntPtr handle){if(handle==IntPtr.Zero)return;if(!CloseHandle(handle)){cleanupFault=true;throw Failure("GMGN_READ_NATIVE_RESOURCE_CLOSE");}handle=IntPtr.Zero;}
 void CloseResources(Child child){Exception error=null;try{Close(ref child.Thread);}catch(Exception e){error=e;}foreach(int i in new int[]{0,1,2,3,4,5})try{Close(ref child.Pipes[i]);}catch(Exception e){if(error==null)error=e;}try{Close(ref child.Handle);}catch(Exception e){if(error==null)error=e;}if(error!=null)throw error;}
 static FileStream Stream(IntPtr handle,FileAccess access){return new FileStream(new Microsoft.Win32.SafeHandles.SafeFileHandle(handle,false),access,4096,false);}
 void SetupPipes(Child child){var security=new Security();security.Length=Marshal.SizeOf(typeof(Security));security.Inherit=true;
  foreach(int first in new int[]{0,2,4})if(!CreatePipe(out child.Pipes[first],out child.Pipes[first+1],ref security,0))throw Failure("GMGN_READ_NATIVE_PIPE");
  foreach(int server in new int[]{1,2,4})if(!SetHandleInformation(child.Pipes[server],1,0))throw Failure("GMGN_READ_NATIVE_PIPE_INHERIT");
 }
 string ReadEnvironment(){
  // Only explicit system/profile variables. No provider keys, arbitrary
  // environment enumeration, inherited preloads or credential copying.
  var values=new SortedDictionary<string,string>(StringComparer.OrdinalIgnoreCase);
  foreach(string key in new string[]{"SystemRoot","WINDIR","USERPROFILE","APPDATA","LOCALAPPDATA","TEMP","TMP","PATH","PATHEXT","COMSPEC"}){
   string value=Environment.GetEnvironmentVariable(key);if(value!=null)values.Add(key,value);
  }
  values["GMGN_RATE_LIMIT_AUTO_RETRY_MAX_WAIT_MS"]="0";values["IMC_GMGN_PUBLIC_GENERATION"]=sources.PathsJson();
  var block=new StringBuilder();foreach(var pair in values)block.Append(pair.Key).Append('=').Append(pair.Value).Append('\0');return block.Append('\0').ToString();
 }
}
