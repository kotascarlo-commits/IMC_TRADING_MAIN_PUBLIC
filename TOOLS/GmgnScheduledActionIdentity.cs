// Local scheduler action binding under the trusted Windows/owner boundary.
// No PID hints, command-line output, SDK, credential access or stop authority.
using System;using System.Collections.Generic;using System.Runtime.InteropServices;
using System.Security.Principal;using System.Text;
public sealed class GmgnScheduledActionIdentity {
 [StructLayout(LayoutKind.Sequential)]struct Basic{public IntPtr Exit,Peb,Affinity,Priority,Pid,Parent;}
 [StructLayout(LayoutKind.Sequential)]struct FileTime{public uint Low,High;}
 [StructLayout(LayoutKind.Sequential)]struct Unicode{public ushort Length,Maximum;public IntPtr Buffer;}
 [UnmanagedFunctionPointer(CallingConvention.Winapi)]delegate int Query(IntPtr h,int kind,IntPtr buffer,uint length,out uint actual);
 [DllImport("kernel32.dll",SetLastError=true)]static extern IntPtr OpenProcess(uint access,bool inherit,int pid);
 [DllImport("kernel32.dll",SetLastError=true)]static extern uint GetProcessId(IntPtr h);
 [DllImport("kernel32.dll")]static extern uint GetCurrentProcessId();
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool GetExitCodeProcess(IntPtr h,out uint code);
 [DllImport("kernel32.dll",SetLastError=true)]static extern uint WaitForSingleObject(IntPtr h,uint timeout);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool GetProcessTimes(IntPtr h,out FileTime birth,out FileTime exit,out FileTime kernel,out FileTime user);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern bool QueryFullProcessImageNameW(IntPtr h,uint flags,StringBuilder path,ref uint count);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode)]static extern IntPtr GetModuleHandleW(string name);
 [DllImport("kernel32.dll",CharSet=CharSet.Ansi)]static extern IntPtr GetProcAddress(IntPtr module,string name);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool CloseHandle(IntPtr handle);
 [DllImport("advapi32.dll",SetLastError=true)]static extern bool OpenProcessToken(IntPtr process,uint access,out IntPtr token);
 [DllImport("shell32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern IntPtr CommandLineToArgvW(string command,out int count);
 [DllImport("kernel32.dll")]static extern IntPtr LocalFree(IntPtr memory);
 static readonly List<GmgnScheduledActionIdentity> retained=new List<GmgnScheduledActionIdentity>();
 readonly object sync=new object();readonly IntPtr handle;readonly Query query;
 readonly int pid;readonly string sid,exe,arguments;readonly string[] expected;
 readonly GmgnReadDirectoryIdentity directory;readonly string directoryId;
 bool fault,complete;
 public GmgnScheduledActionIdentity(int enginePid,string ownerSid,string executable,string actionArguments,string earliestBirthUtc):this(enginePid,ownerSid,executable,actionArguments,earliestBirthUtc,false){}
 // Self binding cannot open a caller-selected PID. It proves lifetime and
 // exact arguments, not scheduler provenance or a fresh dispatch grant.
 public static GmgnScheduledActionIdentity BindCurrentAction(string ownerSid,string executable,string actionArguments){
  return new GmgnScheduledActionIdentity(checked((int)GetCurrentProcessId()),ownerSid,executable,actionArguments,null,true);
 }
 GmgnScheduledActionIdentity(int enginePid,string ownerSid,string executable,string actionArguments,string earliestBirthUtc,bool selfOnly){
  lock(retained)retained.Add(this);
  try{
   if(IntPtr.Size!=8||Marshal.SizeOf(typeof(Basic))!=48||Marshal.SizeOf(typeof(Unicode))!=16||enginePid<=0||
    String.IsNullOrEmpty(ownerSid)||String.IsNullOrEmpty(executable)||String.IsNullOrEmpty(actionArguments)||
    actionArguments.IndexOf('\0')>=0||actionArguments.Contains("$("))throw Fail("GMGN_ACTION_IDENTITY_INPUT");
   DateTime earliest=DateTime.MinValue;if(!selfOnly&&(!DateTime.TryParseExact(earliestBirthUtc,"o",System.Globalization.CultureInfo.InvariantCulture,
    System.Globalization.DateTimeStyles.RoundtripKind,out earliest)||earliest.Kind!=DateTimeKind.Utc||
    earliest>DateTime.UtcNow||DateTime.UtcNow-earliest>TimeSpan.FromSeconds(30)))throw Fail("GMGN_ACTION_START_WINDOW");
   if(selfOnly&&enginePid!=GetCurrentProcessId())throw Fail("GMGN_ACTION_NOT_SELF");
   pid=enginePid;sid=ownerSid;exe=executable;arguments=actionArguments;
   IntPtr address=GetProcAddress(GetModuleHandleW("ntdll.dll"),"NtQueryInformationProcess");
   if(address==IntPtr.Zero)throw Fail("GMGN_ACTION_NATIVE_QUERY_UNAVAILABLE");
   query=(Query)Marshal.GetDelegateForFunctionPointer(address,typeof(Query));
   // PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE; never inheritable.
   handle=OpenProcess(0x101000,false,pid);
   if(handle==IntPtr.Zero)throw Fail("GMGN_ACTION_ORIGINAL_OPEN");
   expected=ReadBinding();
   if(!selfOnly&&DateTime.ParseExact(expected[2],"o",System.Globalization.CultureInfo.InvariantCulture,
    System.Globalization.DateTimeStyles.RoundtripKind)<earliest)throw Fail("GMGN_ACTION_BIRTH_BEFORE_START");
   complete=true;Check();
  }catch{fault=true;throw;}
 }
 GmgnScheduledActionIdentity(string[] nativeSelf,string ownerSid,GmgnReadDirectoryIdentity heldDirectory,string expectedDirectoryId,string expectedArguments){
  lock(retained)retained.Add(this);
  try{
   if(IntPtr.Size!=8||nativeSelf==null||nativeSelf.Length!=4||heldDirectory==null||
    heldDirectory.GetType()!=typeof(GmgnReadDirectoryIdentity)||typeof(GmgnReadDirectoryIdentity).Assembly!=GetType().Assembly||
    String.IsNullOrEmpty(ownerSid)||String.IsNullOrEmpty(expectedDirectoryId))throw Fail("GMGN_ACTION_PEER_INPUT");
   expected=(string[])nativeSelf.Clone();foreach(string item in expected)if(String.IsNullOrEmpty(item))throw Fail("GMGN_ACTION_PEER_INPUT");
   if(!Int32.TryParse(expected[0],out pid)||pid<=0)throw Fail("GMGN_ACTION_PEER_INPUT");
   if(expectedArguments!=null&&(String.IsNullOrEmpty(expectedArguments)||expectedArguments.IndexOf('\0')>=0||expectedArguments.Contains("$(")))throw Fail("GMGN_ACTION_PEER_INPUT");
   sid=ownerSid;exe=expected[3];directory=heldDirectory;directoryId=expectedDirectoryId;arguments=expectedArguments;
   IntPtr address=GetProcAddress(GetModuleHandleW("ntdll.dll"),"NtQueryInformationProcess");
   if(address==IntPtr.Zero)throw Fail("GMGN_ACTION_NATIVE_QUERY_UNAVAILABLE");
   query=(Query)Marshal.GetDelegateForFunctionPointer(address,typeof(Query));
   handle=OpenProcess(0x101000,false,pid);if(handle==IntPtr.Zero)throw Fail("GMGN_ACTION_ORIGINAL_OPEN");
   complete=true;Check();
  }catch{fault=true;throw;}
 }
 public static GmgnScheduledActionIdentity BindExpectedPeer(string[] nativeSelf,string ownerSid,GmgnReadDirectoryIdentity heldDirectory,string expectedDirectoryId){
  return new GmgnScheduledActionIdentity(nativeSelf,ownerSid,heldDirectory,expectedDirectoryId,null);
 }
 public static GmgnScheduledActionIdentity BindExpectedPeerAction(string[] nativeSelf,string ownerSid,GmgnReadDirectoryIdentity heldDirectory,string expectedDirectoryId,string expectedArguments){
  if(expectedArguments==null)throw Fail("GMGN_ACTION_PEER_INPUT");
  return new GmgnScheduledActionIdentity(nativeSelf,ownerSid,heldDirectory,expectedDirectoryId,expectedArguments);
 }
 static InvalidOperationException Fail(string code){return new InvalidOperationException(code);}
 void Alive(){if(handle==IntPtr.Zero||WaitForSingleObject(handle,0)!=258||GetProcessId(handle)!=pid)throw Fail("GMGN_ACTION_ORIGINAL_ENDED");}
 string[] ReadBinding(){
  Alive();IntPtr buffer=Marshal.AllocHGlobal(48);
  try{
   uint size;int result=query(handle,0,buffer,48,out size);
   if(result!=0||size!=48)throw Fail("GMGN_ACTION_BASIC_QUERY");
   Basic basic=(Basic)Marshal.PtrToStructure(buffer,typeof(Basic));FileTime birth,exit,kernel,user;
   long parent=basic.Parent.ToInt64();
   if(basic.Pid.ToInt64()!=pid||parent<0||parent>Int32.MaxValue||!GetProcessTimes(handle,out birth,out exit,out kernel,out user))throw Fail("GMGN_ACTION_BASIC_BINDING");
   StringBuilder path=new StringBuilder(32768);uint length=(uint)path.Capacity;
   if(!QueryFullProcessImageNameW(handle,0,path,ref length)||length==0||length>=path.Capacity||
    !String.Equals(path.ToString(),exe,StringComparison.OrdinalIgnoreCase))throw Fail("GMGN_ACTION_EXECUTABLE");
   long time=(long)(((ulong)birth.High<<32)|birth.Low);Alive();
   return new string[]{pid.ToString(),parent.ToString(),DateTime.FromFileTimeUtc(time).ToString("o"),path.ToString()};
  }finally{Marshal.FreeHGlobal(buffer);}
 }
 string ReadCommand(){
  Alive();uint needed;int result=query(handle,60,IntPtr.Zero,0,out needed);
  // ProcessCommandLineInformation is version dependent. Unsupported ABI is
  // fail-closed, without a CIM, PEB-memory or candidate-process fallback.
  if((uint)result!=0xc0000004U||(needed<16||needed>131072))throw Fail("GMGN_ACTION_COMMAND_QUERY_UNAVAILABLE");
  IntPtr buffer=Marshal.AllocHGlobal((int)needed);
  try{
   uint actual;if(query(handle,60,buffer,needed,out actual)!=0||actual<16||actual>needed)throw Fail("GMGN_ACTION_COMMAND_QUERY");
   Unicode text=(Unicode)Marshal.PtrToStructure(buffer,typeof(Unicode));
   long offset=text.Buffer.ToInt64()-buffer.ToInt64();
   if(text.Length==0||(text.Length&1)!=0||text.Maximum<text.Length||offset<16||offset>actual||
    text.Length>actual-offset)throw Fail("GMGN_ACTION_COMMAND_ABI");
   string value=Marshal.PtrToStringUni(text.Buffer,text.Length/2);
   if(value.IndexOf('\0')>=0)throw Fail("GMGN_ACTION_COMMAND_ABI");Alive();return value;
  }finally{Marshal.FreeHGlobal(buffer);}
 }
 static string[] Split(string text){
  int count;IntPtr parsed=CommandLineToArgvW(text,out count);
  if(parsed==IntPtr.Zero)throw Fail("GMGN_ACTION_ARGUMENT_PARSE");
  try{if(count<1||count>32)throw Fail("GMGN_ACTION_ARGUMENT_PARSE");var values=new string[count];for(int i=0;i<count;i++)values[i]=Marshal.PtrToStringUni(Marshal.ReadIntPtr(parsed,i*IntPtr.Size));return values;}
  finally{if(LocalFree(parsed)!=IntPtr.Zero)throw Fail("GMGN_ACTION_ARGUMENT_FREE");}
 }
 void CheckCommand(){
  string[] actual=Split(ReadCommand()),wanted=Split("\""+exe+"\" "+arguments);
  if(actual.Length!=wanted.Length||!String.Equals(actual[0],wanted[0],StringComparison.OrdinalIgnoreCase))throw Fail("GMGN_ACTION_COMMAND_MISMATCH");
  for(int i=1;i<actual.Length;i++)if(actual[i]!=wanted[i])throw Fail("GMGN_ACTION_COMMAND_MISMATCH");
 }
 void CheckSid(){
  IntPtr token;if(!OpenProcessToken(handle,8,out token))throw Fail("GMGN_ACTION_TOKEN_QUERY");
  try{using(var identity=new WindowsIdentity(token)){if(identity.User==null||identity.User.Value!=sid)throw Fail("GMGN_ACTION_OWNER_MISMATCH");}}
  finally{if(!CloseHandle(token))throw Fail("GMGN_ACTION_TOKEN_CLOSE");}
 }
 public void Check(){lock(sync)try{
  if(fault||!complete)throw Fail("GMGN_ACTION_IDENTITY_FAULT");
  if(directory!=null){directory.Check();if(directory.Identity!=directoryId)throw Fail("GMGN_ACTION_PEER_DIRECTORY");}
  string[] current=ReadBinding();for(int i=0;i<4;i++)if(current[i]!=expected[i])throw Fail("GMGN_ACTION_ORIGINAL_CHANGED");
  CheckSid();if(arguments!=null)CheckCommand();Alive();
  if(directory!=null)directory.Check();
 }catch{fault=true;throw;}}
 public string[] ReadNativeBinding(){lock(sync){Check();return (string[])expected.Clone();}}
 public void CheckPeerPid(int kernelPeerPid){lock(sync)try{
  Check();if(kernelPeerPid!=pid)throw Fail("GMGN_ACTION_PIPE_PEER_MISMATCH");
  // The original process remains alive and pinned: its PID cannot rebind.
  Alive();
 }catch{fault=true;throw;}}
 public void Abort(){lock(sync)fault=true;}
 public long ReadOriginalExitCode(){lock(sync){
  if(handle==IntPtr.Zero)throw Fail("GMGN_ACTION_ORIGINAL_OPEN");
  uint wait=WaitForSingleObject(handle,0);if(wait==258)return -1;
  uint code;if(wait!=0||!GetExitCodeProcess(handle,out code))throw Fail("GMGN_ACTION_EXIT_UNCONFIRMED");return code;
 }}
 public string State{get{lock(sync)return complete&&!fault?"BOUND":"FAULT";}}
}
