// Immutable public proof bytes, not a receipt-admission or signing authority.
using System;using System.Collections.Generic;using System.IO;using System.Runtime.InteropServices;
using System.Security.AccessControl;using System.Security.Cryptography;using System.Security.Principal;using System.Text;
public sealed class GmgnReadProofPin {
 [StructLayout(LayoutKind.Sequential)]struct Security{public uint Length;public IntPtr Descriptor;[MarshalAs(UnmanagedType.Bool)]public bool Inherit;}
 [StructLayout(LayoutKind.Sequential)]struct Standard{public long Allocation,Bytes;public uint Links;public byte Pending,Directory;}
 [StructLayout(LayoutKind.Sequential)]struct Tag{public uint Attributes,Reparse;}
 [StructLayout(LayoutKind.Sequential)]struct Id{public ulong Volume;[MarshalAs(UnmanagedType.ByValArray,SizeConst=16)]public byte[] Bytes;}
 [DllImport("advapi32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern bool ConvertStringSecurityDescriptorToSecurityDescriptorW(string s,uint v,out IntPtr d,out uint n);
 [DllImport("advapi32.dll")]static extern uint GetSecurityInfo(IntPtr h,int type,uint info,out IntPtr o,out IntPtr g,out IntPtr d,out IntPtr s,out IntPtr descriptor);
 [DllImport("advapi32.dll")]static extern uint GetSecurityDescriptorLength(IntPtr descriptor);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern bool CreateDirectoryW(string path,ref Security security);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern IntPtr CreateFileW(string p,uint a,uint share,IntPtr security,uint creation,uint flags,IntPtr template);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool GetFileInformationByHandleEx(IntPtr h,int k,ref Standard v,uint n);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool GetFileInformationByHandleEx(IntPtr h,int k,ref Tag v,uint n);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool GetFileInformationByHandleEx(IntPtr h,int k,ref Id v,uint n);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern uint GetFinalPathNameByHandleW(IntPtr h,StringBuilder p,uint n,uint flags);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool GetHandleInformation(IntPtr h,out uint flags);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool ReadFile(IntPtr h,byte[] b,uint n,out uint read,IntPtr o);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool WriteFile(IntPtr h,byte[] b,uint n,out uint written,IntPtr o);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool SetFilePointerEx(IntPtr h,long distance,out long position,uint method);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool FlushFileBuffers(IntPtr h);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool CloseHandle(IntPtr h);
 [DllImport("kernel32.dll")]static extern IntPtr LocalFree(IntPtr allocation);
 sealed class Handle{internal IntPtr Value;internal bool CloseAttempted,CloseUncertain;}
 static readonly List<GmgnReadProofPin> retained=new List<GmgnReadProofPin>();
 static Action<string> fixtureProbe=null;
 readonly object sync=new object();readonly List<Handle> handles=new List<Handle>();
 readonly GmgnReadDirectoryIdentity directory;readonly string sid,path,hash;readonly byte[] bytes;
 Handle reader,folder;string identity,directoryIdentity;bool fault,complete;
 static InvalidOperationException Fail(string code){return new InvalidOperationException(code);}
 static void ValidateDirectory(string path){
  if(path==null||Path.GetFullPath(path)!=path||!System.Text.RegularExpressions.Regex.IsMatch(path,@"\AC:\\Users\\<local-user>\\Desktop\\IMC_TRADING_MAIN\\[.]local-tools\\gmgn-read-recovery-[0-9a-f]{32}\z"))throw Fail("GMGN_PROOF_DIRECTORY");
 }
 static Security Descriptor(string ownerSid,out IntPtr allocation){
  uint length;if(!ConvertStringSecurityDescriptorToSecurityDescriptorW("O:"+ownerSid+"D:P(A;;FA;;;"+ownerSid+")(A;;FA;;;SY)",1,out allocation,out length))throw Fail("GMGN_PROOF_DESCRIPTOR");
  return new Security{Length=(uint)Marshal.SizeOf(typeof(Security)),Descriptor=allocation,Inherit=false};
 }
 static void Free(IntPtr allocation){if(allocation!=IntPtr.Zero&&LocalFree(allocation)!=IntPtr.Zero)throw Fail("GMGN_PROOF_DESCRIPTOR_FREE");}
 public static GmgnReadDirectoryIdentity CreateNewDirectory(string path){
  ValidateDirectory(path);IntPtr allocation=IntPtr.Zero;
  try{Security security=Descriptor(WindowsIdentity.GetCurrent().User.Value,out allocation);
   if(!CreateDirectoryW(path,ref security))throw Fail("GMGN_PROOF_DIRECTORY_CREATE_NEW");return new GmgnReadDirectoryIdentity(path);
  }finally{Free(allocation);}
 }
 public GmgnReadProofPin(GmgnReadDirectoryIdentity recoveryDirectory,string name,byte[] publicEnvelope)
  :this(recoveryDirectory,name,publicEnvelope,null){}
 internal static GmgnReadProofPin PublishRetirementRequest(GmgnReadDirectoryIdentity directory,byte[] bytes,Func<bool> ownSenderAdmission){
  return new GmgnReadProofPin(directory,"retire.request.json",bytes,ownSenderAdmission);
 }
 GmgnReadProofPin(GmgnReadDirectoryIdentity recoveryDirectory,string name,byte[] publicEnvelope,Func<bool> ownSenderAdmission){
  Action<string> admittedProbe=fixtureProbe;
  lock(retained)retained.Add(this);IntPtr allocation=IntPtr.Zero;bool descriptorFreeAttempted=false;
  try{
   if(recoveryDirectory==null||recoveryDirectory.GetType().Assembly!=GetType().Assembly||
    !System.Text.RegularExpressions.Regex.IsMatch(name??"",@"\A(?:stage-ready|stage-fault|warm-source|retire-intent|retire-completed|retire[.]request|read-installation-receipt)[.]json\z")||
    publicEnvelope==null||publicEnvelope.Length==0||publicEnvelope.Length>32768)throw Fail("GMGN_PROOF_INPUT");
   directory=recoveryDirectory;ValidateDirectory(directory.Path);sid=WindowsIdentity.GetCurrent().User.Value;
   path=Path.Combine(directory.Path,name);bytes=(byte[])publicEnvelope.Clone();using(var sha=SHA256.Create())hash=BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-","").ToLowerInvariant();
   // Active test instrumentation must never touch production or be admitted
   // by a caller-supplied scope. Only the exact retained Sender's grant works.
   if(admittedProbe!=null&&(name!="retire.request.json"||ownSenderAdmission==null||!ownSenderAdmission()))throw Fail("GMGN_PROOF_FIXTURE_FORBIDDEN");
   folder=Open(directory.Path,0x000200a0,3,3,true,IntPtr.Zero);long size;directoryIdentity=Inspect(folder,directory.Path,true,out size);
   Security security=Descriptor(sid,out allocation);IntPtr attributes=Marshal.AllocHGlobal(Marshal.SizeOf(typeof(Security)));Handle writer;
   try{Marshal.StructureToPtr(security,attributes,false);writer=Open(path,0xc0020000,1,1,false,attributes);}finally{Marshal.FreeHGlobal(attributes);}
   uint written;if(!WriteFile(writer.Value,bytes,(uint)bytes.Length,out written,IntPtr.Zero)||written!=bytes.Length||!FlushFileBuffers(writer.Value))throw Fail("GMGN_PROOF_WRITE_FLUSH");
   identity=Inspect(writer,path,false,out size);if(size!=bytes.Length)throw Fail("GMGN_PROOF_SIZE");
   Handle overlapping=Open(path,0x80020000,3,3,false,IntPtr.Zero);Readback(overlapping);CloseOnce(writer);
   reader=Open(path,0x80020000,1,3,false,IntPtr.Zero);Readback(reader);CloseOnce(overlapping);
   descriptorFreeAttempted=true;Free(allocation);allocation=IntPtr.Zero;complete=true;Check();
   if(admittedProbe!=null)admittedProbe("AFTER_FINAL_READBACK");
  }catch{fault=true;throw;}finally{if(!descriptorFreeAttempted)try{Free(allocation);}catch{fault=true;throw;}}
 }
 Handle Open(string name,uint access,uint share,uint creation,bool isDirectory,IntPtr security){
  IntPtr value=CreateFileW(name,access,share,security,creation,isDirectory?0x02200000U:0x00200080U,IntPtr.Zero);
  if(value==IntPtr.Zero||value==new IntPtr(-1))throw Fail("GMGN_PROOF_OPEN");var held=new Handle{Value=value};handles.Add(held);return held;
 }
 void CloseOnce(Handle held){if(held==null||held.CloseAttempted)throw Fail("GMGN_PROOF_CLOSE_REPEATED");held.CloseAttempted=true;if(!CloseHandle(held.Value)){held.CloseUncertain=true;throw Fail("GMGN_PROOF_CLOSE_UNCONFIRMED");}held.Value=IntPtr.Zero;}
 void Acl(Handle held){
  IntPtr o,g,d,s,descriptor;if(GetSecurityInfo(held.Value,1,5,out o,out g,out d,out s,out descriptor)!=0||descriptor==IntPtr.Zero)throw Fail("GMGN_PROOF_SECURITY_READ");
  try{uint length=GetSecurityDescriptorLength(descriptor);if(length==0||length>32768)throw Fail("GMGN_PROOF_SECURITY_SIZE");
   byte[] value=new byte[length];Marshal.Copy(descriptor,value,0,value.Length);var security=new RawSecurityDescriptor(value,0);
   if(security.Owner==null||security.Owner.Value!=sid||(security.ControlFlags&ControlFlags.DiscretionaryAclProtected)==0||
    (security.ControlFlags&ControlFlags.DiscretionaryAclPresent)==0||security.DiscretionaryAcl==null||security.DiscretionaryAcl.Count!=2)throw Fail("GMGN_PROOF_ACL");
   var seen=new HashSet<string>(StringComparer.Ordinal);foreach(GenericAce raw in security.DiscretionaryAcl){var ace=raw as CommonAce;
    if(ace==null||ace.IsCallback||ace.AceQualifier!=AceQualifier.AccessAllowed||ace.AceFlags!=AceFlags.None||ace.AccessMask!=0x001f01ff||
     (ace.SecurityIdentifier.Value!=sid&&ace.SecurityIdentifier.Value!="S-1-5-18")||!seen.Add(ace.SecurityIdentifier.Value))throw Fail("GMGN_PROOF_ACL");}
  }finally{Free(descriptor);}
 }
 string Inspect(Handle held,string expected,bool isDirectory,out long size){
  Standard standard=new Standard();Tag tag=new Tag();Id id=new Id();uint flags;var actual=new StringBuilder(32768);
  if(held==null||held.Value==IntPtr.Zero||held.CloseAttempted||!GetHandleInformation(held.Value,out flags)||(flags&1)!=0||
   !GetFileInformationByHandleEx(held.Value,1,ref standard,24)||!GetFileInformationByHandleEx(held.Value,9,ref tag,8)||!GetFileInformationByHandleEx(held.Value,18,ref id,24)||
   standard.Pending!=0||standard.Directory!=(isDirectory?1:0)||(!isDirectory&&standard.Links!=1)||(tag.Attributes&0x400)!=0||tag.Reparse!=0)throw Fail("GMGN_PROOF_NATIVE_SHAPE");
  uint count=GetFinalPathNameByHandleW(held.Value,actual,(uint)actual.Capacity,0);if(count==0||count>=actual.Capacity||!String.Equals(actual.ToString(),@"\\?\"+expected,StringComparison.OrdinalIgnoreCase))throw Fail("GMGN_PROOF_NATIVE_PATH");
  Acl(held);size=standard.Bytes;return id.Volume.ToString("x16")+":"+BitConverter.ToString(id.Bytes).Replace("-","").ToLowerInvariant();
 }
 void Readback(Handle held){
  long size;if(Inspect(held,path,false,out size)!=identity||size!=bytes.Length)throw Fail("GMGN_PROOF_BINDING");
  long position;if(!SetFilePointerEx(held.Value,0,out position,0)||position!=0)throw Fail("GMGN_PROOF_SEEK");
  byte[] actual=new byte[bytes.Length];uint read;if(!ReadFile(held.Value,actual,(uint)actual.Length,out read,IntPtr.Zero)||read!=actual.Length)throw Fail("GMGN_PROOF_READ");
  int difference=0;for(int i=0;i<actual.Length;i++)difference|=actual[i]^bytes[i];if(difference!=0)throw Fail("GMGN_PROOF_READBACK");
 }
 public void Check(){lock(sync)try{
  if(fault||!complete)throw Fail("GMGN_PROOF_FAULT");directory.Check();long size;
  if(Inspect(folder,directory.Path,true,out size)!=directoryIdentity)throw Fail("GMGN_PROOF_DIRECTORY_CHANGED");Readback(reader);directory.Check();
 }catch{fault=true;throw;}}
 public string[] ReadPublicBinding(){lock(sync){Check();var value=new string[]{"1",path,identity,hash};Check();return value;}}
 public bool Faulted{get{lock(sync)return fault;}}
}
