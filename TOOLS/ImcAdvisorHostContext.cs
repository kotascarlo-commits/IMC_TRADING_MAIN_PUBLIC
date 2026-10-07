// Advisor-only lifetime validation. Does not grant execution or trading authority.
using System;using System.Collections.Generic;using System.Diagnostics;using System.IO;
using System.Runtime.InteropServices;using System.Security.AccessControl;using System.Security.Principal;
public sealed class ImcAdvisorDirectory {
 [StructLayout(LayoutKind.Sequential)]struct Security{public uint Length;public IntPtr Descriptor;[MarshalAs(UnmanagedType.Bool)]public bool Inherit;}
 [StructLayout(LayoutKind.Sequential)]struct Id{public ulong Volume;[MarshalAs(UnmanagedType.ByValArray,SizeConst=16)]public byte[] Bytes;}
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern bool CreateDirectoryW(string path,ref Security security);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern IntPtr CreateFileW(string p,uint access,uint share,IntPtr s,uint creation,uint flags,IntPtr template);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool GetFileInformationByHandleEx(IntPtr h,int k,ref Id id,uint n);
 [DllImport("advapi32.dll",CharSet=CharSet.Unicode)]static extern bool ConvertStringSecurityDescriptorToSecurityDescriptorW(string s,uint v,out IntPtr d,out uint n);
 [DllImport("advapi32.dll")]static extern uint GetSecurityInfo(IntPtr h,int t,uint i,out IntPtr o,out IntPtr g,out IntPtr d,out IntPtr s,out IntPtr descriptor);
 [DllImport("advapi32.dll")]static extern uint GetSecurityDescriptorLength(IntPtr d);
 [DllImport("kernel32.dll")]static extern IntPtr LocalFree(IntPtr d);
 readonly object sync=new object();readonly GmgnReadDirectoryIdentity directory;readonly IntPtr handle;readonly string sid;bool fault;
 static readonly List<ImcAdvisorDirectory> retained=new List<ImcAdvisorDirectory>();
 public static GmgnReadDirectoryIdentity CreateNew(string path){
  if(!System.Text.RegularExpressions.Regex.IsMatch(path??"",@"\AC:\\Users\\<local-user>\\AppData\\Local\\Temp\\imc-astra-independent-[0-9a-f]{32}\z"))throw new InvalidOperationException("ASTRA_DIRECTORY_PATH");
  string sid=WindowsIdentity.GetCurrent().User.Value;IntPtr d;uint n;
  if(!ConvertStringSecurityDescriptorToSecurityDescriptorW("O:"+sid+"D:P(A;;FA;;;"+sid+")(A;;FA;;;SY)",1,out d,out n))throw new InvalidOperationException("ASTRA_DIRECTORY_DESCRIPTOR");
  try{var security=new Security{Length=(uint)Marshal.SizeOf(typeof(Security)),Descriptor=d,Inherit=false};
   if(!CreateDirectoryW(path,ref security))throw new InvalidOperationException("ASTRA_DIRECTORY_CREATE_NEW");
  }finally{if(LocalFree(d)!=IntPtr.Zero)throw new InvalidOperationException("ASTRA_DIRECTORY_FREE");}
  return new GmgnReadDirectoryIdentity(path);
 }
 public ImcAdvisorDirectory(GmgnReadDirectoryIdentity original){
  lock(retained)retained.Add(this);try{
   if(original==null||original.GetType().Assembly!=GetType().Assembly)throw new InvalidOperationException("ASTRA_DIRECTORY_TYPE");
   directory=original;sid=WindowsIdentity.GetCurrent().User.Value;
   handle=CreateFileW(directory.Path,0x200a0,3,IntPtr.Zero,3,0x2200000,IntPtr.Zero);
   if(handle==IntPtr.Zero||handle==new IntPtr(-1))throw new InvalidOperationException("ASTRA_DIRECTORY_OPEN");Check();
  }catch{fault=true;throw;}
 }
 public void Check(){lock(sync)try{
  if(fault)throw new InvalidOperationException("ASTRA_DIRECTORY_FAULT");directory.Check();Id id=new Id();
  if(!GetFileInformationByHandleEx(handle,18,ref id,24)||id.Volume.ToString("x16")+":"+BitConverter.ToString(id.Bytes).Replace("-","").ToLowerInvariant()!=directory.Identity)throw new InvalidOperationException("ASTRA_DIRECTORY_CHANGED");
  IntPtr o,g,d,s,sd;if(GetSecurityInfo(handle,1,5,out o,out g,out d,out s,out sd)!=0||sd==IntPtr.Zero)throw new InvalidOperationException("ASTRA_DIRECTORY_SECURITY");
  try{uint n=GetSecurityDescriptorLength(sd);if(n==0||n>32768)throw new InvalidOperationException("ASTRA_DIRECTORY_SECURITY_SIZE");
   byte[] b=new byte[n];Marshal.Copy(sd,b,0,b.Length);var acl=new RawSecurityDescriptor(b,0);
   if(acl.Owner==null||acl.Owner.Value!=sid||(acl.ControlFlags&ControlFlags.DiscretionaryAclProtected)==0||
    (acl.ControlFlags&ControlFlags.DiscretionaryAclPresent)==0||acl.DiscretionaryAcl==null||acl.DiscretionaryAcl.Count!=2)throw new InvalidOperationException("ASTRA_DIRECTORY_ACL");
   var seen=new HashSet<string>();foreach(GenericAce raw in acl.DiscretionaryAcl){var ace=raw as CommonAce;
    if(ace==null||ace.IsCallback||ace.AceQualifier!=AceQualifier.AccessAllowed||ace.AceFlags!=AceFlags.None||ace.AccessMask!=0x1f01ff||
     (ace.SecurityIdentifier.Value!=sid&&ace.SecurityIdentifier.Value!="S-1-5-18")||!seen.Add(ace.SecurityIdentifier.Value))throw new InvalidOperationException("ASTRA_DIRECTORY_ACL");}
  }finally{if(LocalFree(sd)!=IntPtr.Zero)throw new InvalidOperationException("ASTRA_DIRECTORY_FREE");}
 }catch{fault=true;throw;}}
}
public sealed class ImcAdvisorHostContext {
 public const string Project=@"%USERPROFILE%\Desktop\IMC_TRADING_MAIN";
 static readonly string[] RequiredTools={"GmgnRepairNamespace.cs","GmgnImmutableSources.cs","GmgnReadDirectoryIdentity.cs",
  "GmgnScheduledActionIdentity.cs","ImcAdvisorHostContext.cs","IMC-AdvisorBroker.ps1","IMC-AdvisorTaskBootstrap.ps1","GMGN-SupervisorTask.ps1"};
 readonly object sync=new object();readonly GmgnImmutableSources sources;readonly GmgnScheduledActionIdentity action;
 readonly GmgnReadDirectoryIdentity directory;readonly ImcAdvisorDirectory protectedDirectory;readonly string[] self;readonly string fingerprint;bool fault,complete;
 static readonly List<ImcAdvisorHostContext> retained=new List<ImcAdvisorHostContext>();
 public ImcAdvisorHostContext(GmgnImmutableSources pins,GmgnScheduledActionIdentity originalAction,
  GmgnReadDirectoryIdentity brokerDirectory,string[] requiredPaths,string[] expectedHashes){
  lock(retained)retained.Add(this);
  try{
   if(pins==null||originalAction==null||brokerDirectory==null||pins.GetType()!=typeof(GmgnImmutableSources)||
    originalAction.GetType()!=typeof(GmgnScheduledActionIdentity)||brokerDirectory.GetType()!=typeof(GmgnReadDirectoryIdentity)||
    typeof(GmgnImmutableSources).Assembly!=GetType().Assembly||typeof(GmgnScheduledActionIdentity).Assembly!=GetType().Assembly||
    typeof(GmgnReadDirectoryIdentity).Assembly!=GetType().Assembly||!pins.IsAdvisorDomain)throw Error("ASTRA_CONTEXT_TYPES");
   sources=pins;action=originalAction;directory=brokerDirectory;
   var wanted=new HashSet<string>(StringComparer.Ordinal);
   foreach(string name in RequiredTools)wanted.Add(Project+"\\TOOLS\\"+name);
   wanted.Add(Project+@"\config\imc-advisor.json");wanted.Add(GmgnImmutableSources.AdvisorExecutable);
   if(requiredPaths==null||expectedHashes==null||requiredPaths.Length!=wanted.Count||expectedHashes.Length!=wanted.Count||pins.Count!=wanted.Count)
    throw Error("ASTRA_CONTEXT_CLOSURE");
   // Expected hashes come from the externally fixed task bootstrap, never the manifest being admitted.
   for(int i=0;i<requiredPaths.Length;i++)if(!wanted.Remove(requiredPaths[i])||!sources.Contains(requiredPaths[i])||
    sources.HashForPath(requiredPaths[i])!=expectedHashes[i])throw Error("ASTRA_CONTEXT_GENERATION");
   if(wanted.Count!=0)throw Error("ASTRA_CONTEXT_CLOSURE");
   self=action.ReadNativeBinding();if(self[0]!=Process.GetCurrentProcess().Id.ToString())throw Error("ASTRA_CONTEXT_NOT_SELF");
   if(!System.Text.RegularExpressions.Regex.IsMatch(directory.Path,@"\AC:\\Users\\<local-user>\\AppData\\Local\\Temp\\imc-astra-independent-[0-9a-f]{32}\z"))
    throw Error("ASTRA_CONTEXT_DIRECTORY");
   protectedDirectory=new ImcAdvisorDirectory(directory);fingerprint=sources.Fingerprint();complete=true;Check();
  }catch{fault=true;throw;}
 }
 static InvalidOperationException Error(string code){return new InvalidOperationException(code);}
 public void Check(){lock(sync)try{
  if(fault||!complete)throw Error("ASTRA_CONTEXT_FAULT");sources.Check();directory.Check();protectedDirectory.Check();action.Check();
  string[] current=action.ReadNativeBinding();for(int i=0;i<4;i++)if(current[i]!=self[i])throw Error("ASTRA_CONTEXT_SELF_CHANGED");
  if(Process.GetCurrentProcess().Id.ToString()!=self[0]||sources.Fingerprint()!=fingerprint)throw Error("ASTRA_CONTEXT_GENERATION_CHANGED");
 }catch{fault=true;throw;}}
 public string ReadConfig(){lock(sync){Check();string value=sources.ReadText(Project+@"\config\imc-advisor.json");Check();return value;}}
 public string ReadBrokerSource(){lock(sync){Check();string value=sources.ReadText(Project+@"\TOOLS\IMC-AdvisorBroker.ps1");Check();return value;}}
 public string Executable{get{lock(sync){Check();return GmgnImmutableSources.AdvisorExecutable;}}}
 public string BrokerDirectory{get{lock(sync){Check();return directory.Path;}}}
 public string[] ReadNativeBinding(){lock(sync){Check();return (string[])self.Clone();}}
 public void Abort(){lock(sync)fault=true;}
 public bool Faulted{get{lock(sync)return fault;}}
}
