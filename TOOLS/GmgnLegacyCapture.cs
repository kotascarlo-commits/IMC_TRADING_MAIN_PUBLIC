// Bounded forensic capture of an explicit public IPC inventory. No provider
// config/key paths, order dispatch, source writes/deletes or process control.
using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Security.AccessControl;using System.Security.Principal;
public sealed class GmgnLegacyCapture {
 [StructLayout(LayoutKind.Sequential)]struct Security{public uint Length;public IntPtr Descriptor;[MarshalAs(UnmanagedType.Bool)]public bool Inherit;}
 [DllImport("advapi32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern bool ConvertStringSecurityDescriptorToSecurityDescriptorW(string text,uint revision,out IntPtr descriptor,out uint size);
 [DllImport("advapi32.dll")]static extern uint GetSecurityInfo(IntPtr h,int type,uint info,out IntPtr owner,out IntPtr group,out IntPtr dacl,out IntPtr sacl,out IntPtr descriptor);
 [DllImport("advapi32.dll")]static extern uint GetSecurityDescriptorLength(IntPtr descriptor);
 [DllImport("kernel32.dll")]static extern IntPtr LocalFree(IntPtr descriptor);
 [StructLayout(LayoutKind.Sequential)]struct Standard{public long Allocation,Bytes;public uint Links;public byte Pending,Directory;}
 [StructLayout(LayoutKind.Sequential)]struct Tag{public uint Attributes,Reparse;}
 [StructLayout(LayoutKind.Sequential)]struct ID{public ulong Volume;[MarshalAs(UnmanagedType.ByValArray,SizeConst=16)]public byte[] Bytes;}
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern IntPtr CreateFileW(string p,uint a,uint s,IntPtr security,uint c,uint f,IntPtr template);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool GetFileInformationByHandleEx(IntPtr h,int k,ref Standard v,uint s);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool GetFileInformationByHandleEx(IntPtr h,int k,ref Tag v,uint s);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool GetFileInformationByHandleEx(IntPtr h,int k,ref ID v,uint s);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern uint GetFinalPathNameByHandleW(IntPtr h,StringBuilder p,uint c,uint f);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool FlushFileBuffers(IntPtr h);
 sealed class File {internal string Path,Identity,Hash;internal long Bytes;internal IntPtr Handle;internal bool Protected;}
 sealed class Pair {internal File Source,Target,Readback;}
 static readonly List<GmgnLegacyCapture> retained=new List<GmgnLegacyCapture>();
 // Private own-fixture boundary probe. It cannot run against production IPC.
 static Action<string,int> fixtureProbe=null;
 readonly object sync=new object();readonly List<Pair> files=new List<Pair>();
 readonly List<GmgnRepairNamespace> namespaces=new List<GmgnRepairNamespace>();
 readonly string sourceDirectory,targetDirectory;readonly long end;
 readonly GmgnLegacyLifetimePins owner;
 readonly string sid;
 bool readyValidated,attempted,committed,fault;File manifest,manifestReadback;
 public GmgnLegacyCapture(string source,string target,string[] names,double seconds):this(source,target,names,seconds,null){}
 public GmgnLegacyCapture(string source,string target,string[] names,double seconds,GmgnLegacyLifetimePins lifetime){
  lock(retained)retained.Add(this);
  try {
   sid=WindowsIdentity.GetCurrent().User.Value;
   sourceDirectory=Path.GetFullPath(source);targetDirectory=Path.GetFullPath(target);
   bool production=sourceDirectory==@"%USERPROFILE%\AppData\Local\Temp\imc-gmgn-broker-3904";
   bool fixture=sourceDirectory.StartsWith(@"%USERPROFILE%\Desktop\IMC_TRADING_MAIN\.local-tools\legacy-capture-native-",StringComparison.OrdinalIgnoreCase)&&Path.GetFileName(sourceDirectory)=="legacy";
   if(lifetime==null&&!fixture||lifetime!=null&&(lifetime.GetType()!=typeof(GmgnLegacyLifetimePins)||lifetime.GetType().Assembly!=GetType().Assembly))throw new InvalidOperationException("GMGN_CAPTURE_OWNER_REQUIRED");
   owner=lifetime;CheckOwner();
   if(fixtureProbe!=null&&!fixture)throw new InvalidOperationException("GMGN_CAPTURE_FIXTURE_PROBE_FORBIDDEN");
   if((!production&&!fixture)||!targetDirectory.StartsWith(@"%USERPROFILE%\Desktop\IMC_TRADING_MAIN\",StringComparison.OrdinalIgnoreCase)||
    targetDirectory==sourceDirectory||source.Contains("\"")||target.Contains("\"")||names==null||names.Length<1||names.Length>512||
    Double.IsNaN(seconds)||seconds<1||seconds>120)throw new InvalidOperationException("GMGN_CAPTURE_INPUT");
   end=System.Diagnostics.Stopwatch.GetTimestamp()+(long)(seconds*System.Diagnostics.Stopwatch.Frequency);
   names=(string[])names.Clone();var seen=new HashSet<string>(StringComparer.OrdinalIgnoreCase);bool ready=false;
   foreach(string name in names){if(!System.Text.RegularExpressions.Regex.IsMatch(name??"",@"\A(?:broker\.(?:ready|last_error|last_error\.json)|[0-9a-f]{32}\.(?:request|response)\.json|[0-9a-f]{32}\.tmp)\z")||!seen.Add(name))throw new InvalidOperationException("GMGN_CAPTURE_PUBLIC_NAME");if(name=="broker.ready")ready=true;}
   if(!ready)throw new InvalidOperationException("GMGN_CAPTURE_READY_REQUIRED");
   namespaces.Add(new GmgnRepairNamespace(sourceDirectory));namespaces.Add(new GmgnRepairNamespace(targetDirectory));
   Probe("NAMESPACE_PINNED",0);
   foreach(string name in names){Deadline();var pair=new Pair{Source=new File{Path=Path.Combine(sourceDirectory,name)}};files.Add(pair);
    pair.Source.Handle=Open(pair.Source.Path,0x80000000U,1,3);Inspect(pair.Source,true);pair.Source.Hash=Digest(pair.Source);Probe("SOURCE_PINNED",files.Count-1);}
   CheckSources();CheckOwner();
  }catch{fault=true;throw;}
 }
 void Deadline(){if(System.Diagnostics.Stopwatch.GetTimestamp()>=end)throw new InvalidOperationException("GMGN_CAPTURE_DEADLINE");}
 void CheckOwner(){if(owner!=null)owner.CheckAlive();}
 void Probe(string phase,int index){if(fixtureProbe!=null){Deadline();fixtureProbe(phase,index);Deadline();}CheckOwner();}
 static IntPtr Open(string path,uint access,uint share,uint disposition){IntPtr h=CreateFileW(path,access,share,IntPtr.Zero,disposition,0x200080U,IntPtr.Zero);
  if(h==IntPtr.Zero||h==new IntPtr(-1))throw new InvalidOperationException("GMGN_CAPTURE_OPEN");return h;}
 void CreateProtected(File file){
  if(sid=="S-1-5-18")throw new InvalidOperationException("GMGN_CAPTURE_SYSTEM_OWNER_FORBIDDEN");
  IntPtr descriptor=IntPtr.Zero,attributes=IntPtr.Zero;uint size;
  try{
   if(!ConvertStringSecurityDescriptorToSecurityDescriptorW("O:"+sid+"D:P(A;;FA;;;"+sid+")(A;;FA;;;SY)",1,out descriptor,out size))throw new InvalidOperationException("GMGN_CAPTURE_SECURITY_DESCRIPTOR");
   var security=new Security{Length=(uint)Marshal.SizeOf(typeof(Security)),Descriptor=descriptor,Inherit=false};
   attributes=Marshal.AllocHGlobal(Marshal.SizeOf(typeof(Security)));Marshal.StructureToPtr(security,attributes,false);
   // The File object already belongs to the retained capture. Store the
   // native handle before any potentially failing descriptor cleanup.
   file.Protected=true;file.Handle=CreateFileW(file.Path,0xc0020000U,1,attributes,1,0x200080U,IntPtr.Zero);
   if(file.Handle==IntPtr.Zero||file.Handle==new IntPtr(-1))throw new InvalidOperationException("GMGN_CAPTURE_OPEN");
  }finally{
   if(attributes!=IntPtr.Zero)Marshal.FreeHGlobal(attributes);
   if(descriptor!=IntPtr.Zero&&LocalFree(descriptor)!=IntPtr.Zero)throw new InvalidOperationException("GMGN_CAPTURE_SECURITY_FREE");
  }
 }
 void CheckAcl(File file){
  IntPtr o,g,d,s,descriptor;
  if(GetSecurityInfo(file.Handle,1,5,out o,out g,out d,out s,out descriptor)!=0||descriptor==IntPtr.Zero)throw new InvalidOperationException("GMGN_CAPTURE_SECURITY_READ");
  try{
   uint length=GetSecurityDescriptorLength(descriptor);if(length==0||length>32768)throw new InvalidOperationException("GMGN_CAPTURE_SECURITY_SIZE");
   byte[] bytes=new byte[length];Marshal.Copy(descriptor,bytes,0,bytes.Length);var acl=new RawSecurityDescriptor(bytes,0);
   if(acl.Owner==null||acl.Owner.Value!=sid||(acl.ControlFlags&ControlFlags.DiscretionaryAclProtected)==0||
    (acl.ControlFlags&ControlFlags.DiscretionaryAclPresent)==0||acl.DiscretionaryAcl==null||acl.DiscretionaryAcl.Count!=2)throw new InvalidOperationException("GMGN_CAPTURE_ACL");
   var seen=new HashSet<string>(StringComparer.Ordinal);
   foreach(GenericAce raw in acl.DiscretionaryAcl){var ace=raw as CommonAce;
    if(ace==null||ace.IsCallback||ace.AceQualifier!=AceQualifier.AccessAllowed||ace.AceFlags!=AceFlags.None||ace.AccessMask!=0x001f01ff||
     (ace.SecurityIdentifier.Value!=sid&&ace.SecurityIdentifier.Value!="S-1-5-18")||!seen.Add(ace.SecurityIdentifier.Value))throw new InvalidOperationException("GMGN_CAPTURE_ACL");
   }
  }finally{if(LocalFree(descriptor)!=IntPtr.Zero)throw new InvalidOperationException("GMGN_CAPTURE_SECURITY_FREE");}
 }
 void Inspect(File file,bool first){
  Standard s=new Standard();Tag t=new Tag();ID id=new ID();var path=new StringBuilder(32768);
  if(!GetFileInformationByHandleEx(file.Handle,1,ref s,24)||!GetFileInformationByHandleEx(file.Handle,9,ref t,8)||
   !GetFileInformationByHandleEx(file.Handle,18,ref id,24)||s.Directory!=0||s.Pending!=0||s.Links!=1||s.Bytes<0||s.Bytes>4194304||
   (t.Attributes&0x400)!=0||t.Reparse!=0)throw new InvalidOperationException("GMGN_CAPTURE_NATIVE_SHAPE");
  uint n=GetFinalPathNameByHandleW(file.Handle,path,(uint)path.Capacity,0);
  if(n==0||n>=path.Capacity||!String.Equals(path.ToString(),@"\\?\"+file.Path,StringComparison.OrdinalIgnoreCase))throw new InvalidOperationException("GMGN_CAPTURE_NATIVE_PATH");
  string identity=id.Volume.ToString("x16")+":"+BitConverter.ToString(id.Bytes).Replace("-","").ToLowerInvariant();
  if(file.Protected)CheckAcl(file);
  if(first){file.Identity=identity;file.Bytes=s.Bytes;}else if(file.Identity!=identity||file.Bytes!=s.Bytes)throw new InvalidOperationException("GMGN_CAPTURE_NATIVE_CHANGED");
 }
 static FileStream Stream(File file,FileAccess access){return new FileStream(new Microsoft.Win32.SafeHandles.SafeFileHandle(file.Handle,false),access);}
 string Digest(File file){using(var stream=Stream(file,FileAccess.Read)){stream.Position=0;using(var sha=SHA256.Create())return BitConverter.ToString(sha.ComputeHash(stream)).Replace("-","").ToLowerInvariant();}}
 void CheckSources(){foreach(var ns in namespaces)ns.Check();foreach(var pair in files)Inspect(pair.Source,false);}
 public string ReadReady(){lock(sync)try{
  if(fault||attempted)throw new InvalidOperationException("GMGN_CAPTURE_READY_LOCKED");Deadline();CheckSources();CheckOwner();
  foreach(var pair in files)if(Path.GetFileName(pair.Source.Path)=="broker.ready"){
   if(pair.Source.Bytes>32768)throw new InvalidOperationException("GMGN_CAPTURE_READY_SIZE");
   using(var stream=Stream(pair.Source,FileAccess.Read)){stream.Position=0;using(var reader=new StreamReader(stream,new UTF8Encoding(false,true),false))return reader.ReadToEnd();}}
  throw new InvalidOperationException("GMGN_CAPTURE_READY_MISSING");
 }catch{fault=true;throw;}}
 public void BindValidatedReady(string sha256){lock(sync)try{
  if(fault||attempted||readyValidated)throw new InvalidOperationException("GMGN_CAPTURE_READY_BINDING");Deadline();CheckOwner();
  foreach(var pair in files)if(Path.GetFileName(pair.Source.Path)=="broker.ready"&&pair.Source.Hash==sha256){readyValidated=true;return;}
  throw new InvalidOperationException("GMGN_CAPTURE_READY_BINDING");
 }catch{fault=true;throw;}}
 static string Q(string value){return "\""+value.Replace("\\","\\\\").Replace("\"","\\\"")+"\"";}
 public void Copy(){lock(sync)try{
  if(fault||attempted||!readyValidated)throw new InvalidOperationException("GMGN_CAPTURE_COPY_LOCKED");attempted=true;Deadline();CheckSources();CheckOwner();
  foreach(var pair in files){Deadline();pair.Target=new File{Path=Path.Combine(targetDirectory,Path.GetFileName(pair.Source.Path))};
   // Retain the private writer handle after sealing. No method writes it again.
   CreateProtected(pair.Target);
   using(var input=Stream(pair.Source,FileAccess.Read))using(var output=Stream(pair.Target,FileAccess.ReadWrite)){
    input.Position=0;byte[] buffer=new byte[65536];long total=0;
    while(total<pair.Source.Bytes){Deadline();int count=input.Read(buffer,0,(int)Math.Min(buffer.Length,pair.Source.Bytes-total));if(count<=0)throw new InvalidOperationException("GMGN_CAPTURE_SHORT_SOURCE");output.Write(buffer,0,count);total+=count;}output.Flush();
   }
   if(!FlushFileBuffers(pair.Target.Handle))throw new InvalidOperationException("GMGN_CAPTURE_FLUSH");Inspect(pair.Target,true);
   pair.Readback=new File{Path=pair.Target.Path,Protected=true};pair.Readback.Handle=Open(pair.Target.Path,0x80020000U,3,3);Inspect(pair.Readback,true);pair.Readback.Hash=Digest(pair.Readback);
   if(pair.Source.Identity==pair.Target.Identity||pair.Target.Identity!=pair.Readback.Identity||pair.Source.Bytes!=pair.Target.Bytes||pair.Target.Bytes!=pair.Readback.Bytes||pair.Readback.Hash!=pair.Source.Hash)throw new InvalidOperationException("GMGN_CAPTURE_COPY_MISMATCH");
   Probe("COPIED_MEMBER",files.IndexOf(pair));
  }
  var text=new StringBuilder("{\"schema_version\":1,\"kind\":\"BOUNDED_PUBLIC_IPC_CAPTURE\",\"source_directory\":").Append(Q(sourceDirectory)).Append(",\"target_directory\":").Append(Q(targetDirectory)).Append(",\"files\":[");
  for(int i=0;i<files.Count;i++){if(i>0)text.Append(',');var p=files[i];text.Append("{\"name\":").Append(Q(Path.GetFileName(p.Source.Path))).Append(",\"source_identity\":").Append(Q(p.Source.Identity)).Append(",\"target_identity\":").Append(Q(p.Target.Identity)).Append(",\"bytes\":").Append(p.Source.Bytes).Append(",\"sha256\":").Append(Q(p.Source.Hash)).Append('}');}
  text.Append("]}");byte[] body=Encoding.UTF8.GetBytes(text.ToString());
  manifest=new File{Path=Path.Combine(targetDirectory,"capture-manifest.json")};CreateProtected(manifest);
  using(var stream=Stream(manifest,FileAccess.ReadWrite)){stream.Write(body,0,body.Length);stream.Flush();}
  if(!FlushFileBuffers(manifest.Handle))throw new InvalidOperationException("GMGN_CAPTURE_MANIFEST_FLUSH");Inspect(manifest,true);
  Probe("MANIFEST_FLUSHED",0);
  manifestReadback=new File{Path=manifest.Path,Protected=true};manifestReadback.Handle=Open(manifest.Path,0x80020000U,3,3);Inspect(manifestReadback,true);manifestReadback.Hash=Digest(manifestReadback);
  using(var sha=SHA256.Create()){string expected=BitConverter.ToString(sha.ComputeHash(body)).Replace("-","").ToLowerInvariant();if(manifest.Identity!=manifestReadback.Identity||manifestReadback.Hash!=expected)throw new InvalidOperationException("GMGN_CAPTURE_MANIFEST_READBACK");}
  // The final native owner check is the commit admission boundary. A later
  // intentional retirement may end the owner without invalidating copies.
  Deadline();CheckSources();Probe("BEFORE_COMMIT",0);
  foreach(var pair in files){Inspect(pair.Target,false);Inspect(pair.Readback,false);}
  Inspect(manifest,false);Inspect(manifestReadback,false);
  CheckOwner();committed=true;CheckCommitted();
 }catch{fault=true;throw;}}
 public void CheckCommitted(){lock(sync)try{
  if(fault||!committed)throw new InvalidOperationException("GMGN_CAPTURE_NOT_COMMITTED");
  // Source paths need not survive future Legacy cleanup. Targets must survive.
  namespaces[1].Check();foreach(var p in files){Inspect(p.Target,false);Inspect(p.Readback,false);if(Digest(p.Readback)!=p.Source.Hash)throw new InvalidOperationException("GMGN_CAPTURE_TARGET_CHANGED");}
  Inspect(manifest,false);Inspect(manifestReadback,false);if(Digest(manifestReadback)!=manifestReadback.Hash)throw new InvalidOperationException("GMGN_CAPTURE_MANIFEST_CHANGED");
 }catch{fault=true;throw;}}
 public string ReadySha256{get{lock(sync){foreach(var pair in files)if(Path.GetFileName(pair.Source.Path)=="broker.ready")return pair.Source.Hash;throw new InvalidOperationException("GMGN_CAPTURE_READY_MISSING");}}}
 public bool Committed{get{lock(sync)return committed&&!fault;}}
 public bool MatchesOwner(GmgnLegacyLifetimePins lifetime){return owner!=null&&Object.ReferenceEquals(owner,lifetime);}
 public void Invalidate(){lock(sync){fault=true;committed=false;}}
 // The readiness check and broker action share the same lock. Invalidate
 // cannot slip between authorization and the one-shot native retirement.
 internal void RetireAdmittedOwner(GmgnLegacyLifetimePins lifetime,string fixtureScope,long ownerDeadline){RetireAdmittedOwner(lifetime,fixtureScope,ownerDeadline,null);}
 internal void RetireAdmittedOwner(GmgnLegacyLifetimePins lifetime,string fixtureScope,long ownerDeadline,Action<Action> effectBoundary){lock(sync){
  if(!MatchesOwner(lifetime))throw new InvalidOperationException("GMGN_CAPTURE_OWNER_MISMATCH");
  CheckCommitted();lifetime.RetireBrokerOnce(fixtureScope,ownerDeadline,effectBoundary);CheckCommitted();
 }}
 public bool Faulted{get{lock(sync)return fault;}}
 public int Count{get{lock(sync)return files.Count;}}
}
