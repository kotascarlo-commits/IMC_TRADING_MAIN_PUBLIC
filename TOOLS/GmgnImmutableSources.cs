// Lifetime public-code pins. No credential paths, sentinel writes, timeout
// reset, close/dispose/finalizer or recovery authority. FAULT retains handles.
using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
public sealed class GmgnImmutableSources {
 [StructLayout(LayoutKind.Sequential)]struct Standard {public long Allocation,Bytes;public uint Links;public byte Pending,Directory;}
 [StructLayout(LayoutKind.Sequential)]struct Tag {public uint Attributes,Reparse;}
 [StructLayout(LayoutKind.Sequential)]struct ID {public ulong Volume;[MarshalAs(UnmanagedType.ByValArray,SizeConst=16)]public byte[] Bytes;}
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern IntPtr CreateFileW(string p,uint a,uint s,IntPtr security,uint c,uint f,IntPtr template);
 [DllImport("kernel32.dll",SetLastError=true)]static extern uint GetFileType(IntPtr h);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool GetFileInformationByHandleEx(IntPtr h,int k,ref Standard v,uint s);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool GetFileInformationByHandleEx(IntPtr h,int k,ref Tag v,uint s);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool GetFileInformationByHandleEx(IntPtr h,int k,ref ID v,uint s);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern uint GetFinalPathNameByHandleW(IntPtr h,StringBuilder p,uint c,uint f);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool ReadFile(IntPtr h,byte[] b,uint n,out uint read,IntPtr o);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool SetFilePointerEx(IntPtr h,long d,out long p,uint m);
 sealed class Member {internal string Path,Identity,Hash;internal long Bytes;internal IntPtr Handle;}
 static readonly List<GmgnImmutableSources> retained=new List<GmgnImmutableSources>();
 readonly object sync=new object();readonly List<Member> members=new List<Member>();
 readonly List<GmgnRepairNamespace> namespaces=new List<GmgnRepairNamespace>();readonly string fixture;readonly bool advisorDomain;bool fault,complete;
 public GmgnImmutableSources(string userRoot,string nodeExecutable,string[] paths,string[] hashes):this(userRoot,nodeExecutable,paths,hashes,null){}
 public GmgnImmutableSources(string userRoot,string nodeExecutable,string[] paths,string[] hashes,string fixtureScope):this(userRoot,nodeExecutable,paths,hashes,fixtureScope,false){}
 public static GmgnImmutableSources CreateAdvisorPins(string[] paths,string[] hashes){
  return new GmgnImmutableSources(@"%USERPROFILE%",null,paths,hashes,null,true);
 }
 GmgnImmutableSources(string userRoot,string nodeExecutable,string[] paths,string[] hashes,string fixtureScope,bool advisor) {
  advisorDomain=advisor;
  lock(retained)retained.Add(this);
  try {
   if(fixtureScope!=null&&!System.Text.RegularExpressions.Regex.IsMatch(fixtureScope,@"\AC:\\Users\\<local-user>\\Desktop\\IMC_TRADING_MAIN\\[.]local-tools\\legacy-capture-native-[0-9a-f]{32}\z"))throw new InvalidOperationException("GMGN_SOURCE_FIXTURE_SCOPE");
   fixture=fixtureScope;
   if(paths==null||hashes==null||paths.Length==0||paths.Length!=hashes.Length||paths.Length>2048)throw new InvalidOperationException("GMGN_SOURCE_INVENTORY");
   paths=(string[])paths.Clone();hashes=(string[])hashes.Clone();
   string root=Canonical(userRoot),node=advisorDomain?null:Canonical(nodeExecutable);
   var unique=new HashSet<string>(StringComparer.OrdinalIgnoreCase);var leaves=new HashSet<string>(StringComparer.OrdinalIgnoreCase);
   for(int i=0;i<paths.Length;i++){
    string path=Canonical(paths[i]);string extension=Path.GetExtension(path).ToLowerInvariant();
    bool isNode=String.Equals(path,node,StringComparison.OrdinalIgnoreCase);
    bool accepted=advisorDomain?AdvisorSource(path):!((!path.StartsWith(root+"\\",StringComparison.OrdinalIgnoreCase)&&!isNode)||
     (!isNode&&extension!=".js"&&extension!=".mjs"&&extension!=".cjs"&&extension!=".ps1"&&extension!=".cs"&&
      !(extension==".json"&&(Path.GetFileName(path)=="package.json"||PublicConfig(path)))));
    if(!accepted||!unique.Add(path)||
     !System.Text.RegularExpressions.Regex.IsMatch(hashes[i]??"",@"\A[0-9a-f]{64}\z"))throw new InvalidOperationException("GMGN_SOURCE_PATH_OR_HASH");
    paths[i]=path;leaves.Add(Path.GetDirectoryName(path));
   }
   foreach(string leaf in leaves)namespaces.Add(new GmgnRepairNamespace(leaf));
   for(int i=0;i<paths.Length;i++){
    var member=new Member{Path=paths[i],Hash=hashes[i]};members.Add(member);
    member.Handle=CreateFileW(member.Path,0x80000000U,1,IntPtr.Zero,3,0x200080U,IntPtr.Zero);
    if(member.Handle==IntPtr.Zero||member.Handle==new IntPtr(-1))throw new InvalidOperationException("GMGN_SOURCE_OPEN_FAILED");
    Inspect(member,true);
   }
   foreach(Member member in members){if(Hash(member)!=member.Hash)throw new InvalidOperationException("GMGN_SOURCE_GENERATION_CHANGED");Inspect(member,false);}
   complete=true;Check();
  }catch{fault=true;throw;}
 }
 static bool AdvisorSource(string path){
  const string root=@"%USERPROFILE%\Desktop\IMC_TRADING_MAIN\";
  foreach(string name in new string[]{"GmgnRepairNamespace.cs","GmgnImmutableSources.cs","GmgnReadDirectoryIdentity.cs",
   "GmgnScheduledActionIdentity.cs","ImcAdvisorHostContext.cs","IMC-AdvisorBroker.ps1","IMC-AdvisorTaskBootstrap.ps1",
   "Invoke-IMC-AdvisorBroker.ps1","IMC-AdvisorClientBootstrap.ps1","GMGN-SupervisorTask.ps1"})
   if(path==root+"TOOLS\\"+name)return true;
  return path==root+@"config\imc-advisor.json"||path==AdvisorExecutable;
 }
 public const string AdvisorExecutable=@"%USERPROFILE%\AppData\Roaming\npm\node_modules\@openai\codex\node_modules\@openai\codex-win32-x64\vendor\x86_64-pc-windows-msvc\bin\codex.exe";
 bool PublicConfig(string path){
  const string root=@"%USERPROFILE%\Desktop\IMC_TRADING_MAIN\config\";
  foreach(string name in new string[]{"imc-gmgn-read-broker.json","imc-execution.json"})
   if(path==root+name||(fixture!=null&&path==Path.Combine(fixture,"config",name)))return true;
  return false;
 }
 static string Canonical(string path){
  if(String.IsNullOrEmpty(path)||path.Length<3||path[1]!=':'||path[2]!='\\'||path.StartsWith(@"\\"))throw new InvalidOperationException("GMGN_SOURCE_PATH");
  foreach(string part in path.Substring(3).Split('\\'))if(part=="."||part==".."||part.IndexOf(':')>=0||part.IndexOf('/')>=0)throw new InvalidOperationException("GMGN_SOURCE_PATH");
  return Path.GetFullPath(path).TrimEnd('\\');
 }
 void Inspect(Member member,bool first){
  Standard s=new Standard();Tag t=new Tag();ID id=new ID();var path=new StringBuilder(32768);
  if(GetFileType(member.Handle)!=1||!GetFileInformationByHandleEx(member.Handle,1,ref s,24)||
   !GetFileInformationByHandleEx(member.Handle,9,ref t,8)||!GetFileInformationByHandleEx(member.Handle,18,ref id,24)||
   s.Pending!=0||s.Directory!=0||s.Links!=1||s.Bytes<0||(t.Attributes&0x400)!=0||t.Reparse!=0)throw new InvalidOperationException("GMGN_SOURCE_NATIVE_SHAPE");
  uint length=GetFinalPathNameByHandleW(member.Handle,path,(uint)path.Capacity,0);
  if(length==0||length>=path.Capacity||!String.Equals(path.ToString(),@"\\?\"+member.Path,StringComparison.OrdinalIgnoreCase))throw new InvalidOperationException("GMGN_SOURCE_NATIVE_PATH");
  string identity=id.Volume.ToString("x16")+":"+BitConverter.ToString(id.Bytes).Replace("-","").ToLowerInvariant();
  if(first){member.Identity=identity;member.Bytes=s.Bytes;}
  else if(member.Identity!=identity||member.Bytes!=s.Bytes)throw new InvalidOperationException("GMGN_SOURCE_NATIVE_CHANGED");
 }
 string Hash(Member member){
  long position;if(!SetFilePointerEx(member.Handle,0,out position,0)||position!=0)throw new InvalidOperationException("GMGN_SOURCE_SEEK");
  using(var sha=SHA256.Create()){
   byte[] buffer=new byte[65536];long total=0;
   while(total<member.Bytes){uint count;uint wanted=(uint)Math.Min(buffer.Length,member.Bytes-total);
    if(!ReadFile(member.Handle,buffer,wanted,out count,IntPtr.Zero)||count==0||count>wanted)throw new InvalidOperationException("GMGN_SOURCE_READ");
    sha.TransformBlock(buffer,0,(int)count,buffer,0);total+=count;
   }
   sha.TransformFinalBlock(new byte[0],0,0);return BitConverter.ToString(sha.Hash).Replace("-","").ToLowerInvariant();
  }
 }
 public void Check(){lock(sync)try{
  if(fault||!complete)throw new InvalidOperationException("GMGN_SOURCE_LOCKED");
  foreach(var ns in namespaces)ns.Check();foreach(Member member in members)Inspect(member,false);
 }catch{fault=true;throw;}}
 public void Abort(){lock(sync)fault=true;}
 internal void InHealthyGeneration(Action work){lock(sync)try{Check();work();Check();}catch{fault=true;throw;}}
 public string PathsJson(){lock(sync){Check();var text=new StringBuilder("[");
  for(int i=0;i<members.Count;i++){if(i>0)text.Append(',');text.Append('"').Append(members[i].Path.Replace("\\","\\\\").Replace("\"","\\\"")).Append('"');}
  return text.Append(']').ToString();}}
 Member Find(string path){foreach(Member member in members)if(String.Equals(member.Path,path,StringComparison.OrdinalIgnoreCase))return member;
  throw new InvalidOperationException("GMGN_SOURCE_NOT_PINNED");}
 public string HashForPath(string path){lock(sync){Check();return Find(path).Hash;}}
 public string Fingerprint(){lock(sync){Check();var rows=new List<string>();foreach(Member m in members)rows.Add(m.Path+"\0"+m.Hash);rows.Sort(StringComparer.Ordinal);
  byte[] bytes=Encoding.UTF8.GetBytes(String.Join("\n",rows.ToArray()));using(var sha=SHA256.Create())return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-","").ToLowerInvariant();}}
 static string JsonText(string value){return "\""+value.Replace("\\","\\\\").Replace("\"","\\\"")+"\"";}
 public string[] ReadNamespaceLeaves(){lock(sync)try{
  Check();var leaves=new List<string>();foreach(GmgnRepairNamespace ns in namespaces)leaves.Add(ns.Directory);
  leaves.Sort(StringComparer.Ordinal);Check();return leaves.ToArray();
 }catch{fault=true;throw;}}
 public string NativeInventoryJson(){lock(sync)try{
  Check();var ordered=new List<Member>(members);ordered.Sort(delegate(Member a,Member b){return StringComparer.Ordinal.Compare(a.Path,b.Path);});
  var text=new StringBuilder("{\"schema_version\":1,\"files\":[");
  for(int i=0;i<ordered.Count;i++){
   Member m=ordered[i];if(i>0)text.Append(',');
   text.Append("{\"path\":").Append(JsonText(m.Path)).Append(",\"sha256\":").Append(JsonText(m.Hash));
   text.Append(",\"native_identity\":").Append(JsonText(m.Identity)).Append(",\"length\":");
   text.Append(m.Bytes.ToString(System.Globalization.CultureInfo.InvariantCulture)).Append('}');
  }
  text.Append("],\"namespace_leaves\":[");var leaves=new List<string>();foreach(GmgnRepairNamespace ns in namespaces)leaves.Add(ns.Directory);
  leaves.Sort(StringComparer.Ordinal);for(int i=0;i<leaves.Count;i++){if(i>0)text.Append(',');text.Append(JsonText(leaves[i]));}
  text.Append("]}");Check();return text.ToString();
 }catch{fault=true;throw;}}
 public string ReadText(string path){lock(sync)try{
  Check();Member member=Find(path);string ext=Path.GetExtension(member.Path).ToLowerInvariant();
  if(member.Bytes>2097152 || (ext!=".ps1"&&ext!=".cs"&&ext!=".js"&&ext!=".mjs"&&ext!=".cjs"&&Path.GetFileName(member.Path)!="package.json"&&!PublicConfig(member.Path)&&
   !(advisorDomain&&member.Path==@"%USERPROFILE%\Desktop\IMC_TRADING_MAIN\config\imc-advisor.json")))
   throw new InvalidOperationException("GMGN_SOURCE_TEXT_BLOCKED");
  long position;if(!SetFilePointerEx(member.Handle,0,out position,0)||position!=0)throw new InvalidOperationException("GMGN_SOURCE_SEEK");
  byte[] bytes=new byte[(int)member.Bytes];int total=0;
  while(total<bytes.Length){byte[] chunk=new byte[Math.Min(65536,bytes.Length-total)];uint count;
   if(!ReadFile(member.Handle,chunk,(uint)chunk.Length,out count,IntPtr.Zero)||count==0||count>chunk.Length)throw new InvalidOperationException("GMGN_SOURCE_READ");
   Buffer.BlockCopy(chunk,0,bytes,total,(int)count);total+=(int)count;}
  Check();int offset=bytes.Length>=3&&bytes[0]==239&&bytes[1]==187&&bytes[2]==191?3:0;
  return new UTF8Encoding(false,true).GetString(bytes,offset,bytes.Length-offset);
 }catch{fault=true;throw;}}
 public bool Contains(string path){lock(sync){Check();foreach(Member member in members)if(String.Equals(member.Path,path,StringComparison.OrdinalIgnoreCase))return true;return false;}}
 public int Count {get{lock(sync)return members.Count;}}
 public bool Faulted {get{lock(sync)return fault;}}
 public bool IsAdvisorDomain {get{return advisorDomain;}}
}
