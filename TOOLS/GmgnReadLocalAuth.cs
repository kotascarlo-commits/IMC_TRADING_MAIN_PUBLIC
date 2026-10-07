// Local IMC IPC authentication only. Never reads provider credentials.
// DPAPI binds to the logged-in Windows owner, not an application identity.
using System;using System.Collections.Generic;using System.IO;using System.Runtime.InteropServices;
using System.Security.AccessControl;using System.Security.Cryptography;using System.Security.Principal;using System.Text;
public sealed class GmgnReadLocalAuth {
 [StructLayout(LayoutKind.Sequential)]struct Security{public uint Length;public IntPtr Descriptor;[MarshalAs(UnmanagedType.Bool)]public bool Inherit;}
 [StructLayout(LayoutKind.Sequential)]struct Blob{public int Length;public IntPtr Data;}
 [StructLayout(LayoutKind.Sequential)]struct Standard{public long Allocation,Bytes;public uint Links;public byte Pending,Directory;}
 [StructLayout(LayoutKind.Sequential)]struct Tag{public uint Attributes,Reparse;}
 [StructLayout(LayoutKind.Sequential)]struct Id{public ulong Volume;[MarshalAs(UnmanagedType.ByValArray,SizeConst=16)]public byte[] Bytes;}
 [DllImport("advapi32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern bool ConvertStringSecurityDescriptorToSecurityDescriptorW(string s,uint v,out IntPtr d,out uint n);
 [DllImport("advapi32.dll")]static extern uint GetSecurityInfo(IntPtr h,int type,uint info,out IntPtr owner,out IntPtr group,out IntPtr dacl,out IntPtr sacl,out IntPtr descriptor);
 [DllImport("advapi32.dll")]static extern uint GetSecurityDescriptorLength(IntPtr descriptor);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern bool CreateDirectoryW(string path,ref Security security);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern IntPtr CreateFileW(string path,uint access,uint share,IntPtr security,uint creation,uint flags,IntPtr template);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool GetFileInformationByHandleEx(IntPtr h,int kind,ref Standard value,uint n);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool GetFileInformationByHandleEx(IntPtr h,int kind,ref Tag value,uint n);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool GetFileInformationByHandleEx(IntPtr h,int kind,ref Id value,uint n);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern uint GetFinalPathNameByHandleW(IntPtr h,StringBuilder path,uint count,uint flags);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool GetHandleInformation(IntPtr h,out uint flags);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool WriteFile(IntPtr h,byte[] bytes,uint count,out uint written,IntPtr overlapped);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool ReadFile(IntPtr h,byte[] bytes,uint count,out uint read,IntPtr overlapped);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool SetFilePointerEx(IntPtr h,long distance,out long position,uint method);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool FlushFileBuffers(IntPtr h);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool CloseHandle(IntPtr h);
 [DllImport("kernel32.dll")]static extern IntPtr LocalFree(IntPtr memory);
 [DllImport("crypt32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern bool CryptProtectData(ref Blob input,string description,ref Blob entropy,IntPtr reserved,IntPtr prompt,uint flags,out Blob output);
 [DllImport("crypt32.dll",SetLastError=true)]static extern bool CryptUnprotectData(ref Blob input,IntPtr description,ref Blob entropy,IntPtr reserved,IntPtr prompt,uint flags,out Blob output);
 sealed class Handle{internal IntPtr Value;internal bool CloseAttempted,CloseUncertain;}
 static readonly List<GmgnReadLocalAuth> retained=new List<GmgnReadLocalAuth>();
 readonly object sync=new object();readonly List<Handle> handles=new List<Handle>();
 readonly string path,directory,sid;GmgnReadDirectoryIdentity namespacePin;
 Handle file,folder;string fileIdentity,directoryIdentity,cipherHash;long fileLength;byte[] key;bool fault,complete;
 readonly string fixtureScope;static Action<string,GmgnReadLocalAuth> fixtureProbe=null;
 static Func<string,int> fixtureCloseFailure=null;
 GmgnReadLocalAuth(string directoryPath,string ownerSid,string ownFixtureScope){
  lock(retained)retained.Add(this);
  directory=directoryPath;sid=ownerSid;fixtureScope=ownFixtureScope;path=Path.Combine(directory??"","store.dpapi");
  const string root=@"%USERPROFILE%\Desktop\IMC_TRADING_MAIN\.local-tools\";
  if(fixtureScope!=null&&!System.Text.RegularExpressions.Regex.IsMatch(fixtureScope,@"\AC:\\Users\\<local-user>\\Desktop\\IMC_TRADING_MAIN\\[.]local-tools\\local-auth-native-[0-9a-f]{32}\z"))throw Fail("GMGN_AUTH_FIXTURE_SCOPE");
  string prefix=fixtureScope==null?root:fixtureScope+"\\";
  if(directory==null||Path.GetFullPath(directory)!=directory||
   !System.Text.RegularExpressions.Regex.IsMatch(directory,"\\A"+System.Text.RegularExpressions.Regex.Escape(prefix)+"gmgn-read-auth-[0-9a-f]{32}\\z")||
   new SecurityIdentifier(sid).Value!=WindowsIdentity.GetCurrent().User.Value)throw Fail("GMGN_AUTH_INPUT");
 }
 static InvalidOperationException Fail(string code){return new InvalidOperationException(code);}
 static string Hash(byte[] bytes){using(var sha=SHA256.Create())return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-","").ToLowerInvariant();}
 static void Wipe(byte[] bytes){if(bytes!=null)Array.Clear(bytes,0,bytes.Length);}
 static void ValidateKey(byte[] bytes){
  if(bytes==null||bytes.Length!=64)throw Fail("GMGN_AUTH_LOCAL_KEY_SHAPE");
  foreach(byte b in bytes)if(!((b>=48&&b<=57)||(b>=97&&b<=102)))throw Fail("GMGN_AUTH_LOCAL_KEY_SHAPE");
 }
 Security Descriptor(out IntPtr allocation){
  uint size;if(!ConvertStringSecurityDescriptorToSecurityDescriptorW("O:"+sid+"D:P(A;;FA;;;"+sid+")(A;;FA;;;SY)",1,out allocation,out size))throw Fail("GMGN_AUTH_DESCRIPTOR_CREATE");
  return new Security{Length=(uint)Marshal.SizeOf(typeof(Security)),Descriptor=allocation,Inherit=false};
 }
 static void FreeDescriptor(IntPtr allocation){if(allocation!=IntPtr.Zero&&LocalFree(allocation)!=IntPtr.Zero)throw Fail("GMGN_AUTH_DESCRIPTOR_FREE");}
 Handle Open(string name,uint access,uint share,uint creation,bool isDirectory,IntPtr security){
  IntPtr value=CreateFileW(name,access,share,security,creation,isDirectory?0x02200000U:0x00200080U,IntPtr.Zero);
  if(value==IntPtr.Zero||value==new IntPtr(-1))throw Fail("GMGN_AUTH_OPEN");
  var held=new Handle{Value=value};handles.Add(held);return held;
 }
 void Probe(string phase){if(fixtureProbe!=null){if(fixtureScope==null)throw Fail("GMGN_AUTH_FIXTURE_FORBIDDEN");fixtureProbe(phase,this);}}
 void CloseOnce(Handle held,string phase){
  if(held==null||held.CloseAttempted)throw Fail("GMGN_AUTH_CLOSE_REPEATED");
  held.CloseAttempted=true;
  if(fixtureCloseFailure!=null){
   if(fixtureScope==null){held.CloseUncertain=true;throw Fail("GMGN_AUTH_FIXTURE_FORBIDDEN");}
   if(fixtureCloseFailure(phase)!=0){held.CloseUncertain=true;throw Fail("GMGN_AUTH_CLOSE_UNCONFIRMED");}
  }
  if(!CloseHandle(held.Value)){held.CloseUncertain=true;throw Fail("GMGN_AUTH_CLOSE_UNCONFIRMED");}
  held.Value=IntPtr.Zero;
 }
 void Acl(Handle held){
  IntPtr owner,group,dacl,sacl,descriptor;
  if(GetSecurityInfo(held.Value,1,5,out owner,out group,out dacl,out sacl,out descriptor)!=0||descriptor==IntPtr.Zero)throw Fail("GMGN_AUTH_SECURITY_READ");
  try{
   uint length=GetSecurityDescriptorLength(descriptor);if(length==0||length>32768)throw Fail("GMGN_AUTH_SECURITY_SIZE");
   byte[] bytes=new byte[length];Marshal.Copy(descriptor,bytes,0,bytes.Length);var value=new RawSecurityDescriptor(bytes,0);
   if(value.Owner==null||value.Owner.Value!=sid||(value.ControlFlags&ControlFlags.DiscretionaryAclProtected)==0||
    (value.ControlFlags&ControlFlags.DiscretionaryAclPresent)==0||value.DiscretionaryAcl==null||value.DiscretionaryAcl.Count!=2)throw Fail("GMGN_AUTH_ACL");
   var seen=new HashSet<string>(StringComparer.Ordinal);
   foreach(GenericAce raw in value.DiscretionaryAcl){var ace=raw as CommonAce;
    if(ace==null||ace.IsCallback||ace.AceQualifier!=AceQualifier.AccessAllowed||ace.AceFlags!=AceFlags.None||ace.AccessMask!=0x001f01ff||
     (ace.SecurityIdentifier.Value!=sid&&ace.SecurityIdentifier.Value!="S-1-5-18")||!seen.Add(ace.SecurityIdentifier.Value))throw Fail("GMGN_AUTH_ACL");
   }
  }finally{FreeDescriptor(descriptor);}
 }
 string Inspect(Handle held,string expectedPath,bool isDirectory,out long length){
  if(held==null||held.Value==IntPtr.Zero||held.CloseAttempted)throw Fail("GMGN_AUTH_HANDLE_STATE");
  Standard standard=new Standard();Tag tag=new Tag();Id id=new Id();uint flags;var actual=new StringBuilder(32768);
  if(!GetHandleInformation(held.Value,out flags)||(flags&1)!=0||
   !GetFileInformationByHandleEx(held.Value,1,ref standard,24)||!GetFileInformationByHandleEx(held.Value,9,ref tag,8)||
   !GetFileInformationByHandleEx(held.Value,18,ref id,24)||standard.Pending!=0||standard.Directory!=(isDirectory?1:0)||
   (!isDirectory&&standard.Links!=1)||(tag.Attributes&0x400)!=0||tag.Reparse!=0)throw Fail("GMGN_AUTH_NATIVE_SHAPE");
  uint count=GetFinalPathNameByHandleW(held.Value,actual,(uint)actual.Capacity,0);
  if(count==0||count>=actual.Capacity||!String.Equals(actual.ToString(),@"\\?\"+expectedPath,StringComparison.OrdinalIgnoreCase))throw Fail("GMGN_AUTH_NATIVE_PATH");
  Acl(held);length=standard.Bytes;return id.Volume.ToString("x16")+":"+BitConverter.ToString(id.Bytes).Replace("-","").ToLowerInvariant();
 }
 byte[] Read(Handle held){
  long size;string id=Inspect(held,path,false,out size);
  if(id!=fileIdentity||size!=fileLength||size<=0||size>16384)throw Fail("GMGN_AUTH_FILE_BINDING");
  long position;if(!SetFilePointerEx(held.Value,0,out position,0)||position!=0)throw Fail("GMGN_AUTH_SEEK");
  byte[] bytes=new byte[(int)size];uint read;if(!ReadFile(held.Value,bytes,(uint)bytes.Length,out read,IntPtr.Zero)||read!=bytes.Length)throw Fail("GMGN_AUTH_READ");
  if(Hash(bytes)!=cipherHash)throw Fail("GMGN_AUTH_CIPHERTEXT_HASH");return bytes;
 }
 byte[] Crypt(byte[] input,bool protect){
  byte[] entropy;using(var sha=SHA256.Create())entropy=sha.ComputeHash(Encoding.ASCII.GetBytes("IMC_GMGN_LOCAL_IPC_AUTH_V1\0"+sid));
  Blob data=new Blob{Length=input.Length,Data=Marshal.AllocHGlobal(input.Length)};
  Blob extra=new Blob{Length=entropy.Length,Data=Marshal.AllocHGlobal(entropy.Length)},output=new Blob();
  try{
   Marshal.Copy(input,0,data.Data,input.Length);Marshal.Copy(entropy,0,extra.Data,entropy.Length);
   bool ok=protect?CryptProtectData(ref data,null,ref extra,IntPtr.Zero,IntPtr.Zero,1,out output):CryptUnprotectData(ref data,IntPtr.Zero,ref extra,IntPtr.Zero,IntPtr.Zero,1,out output);
   if(!ok||output.Data==IntPtr.Zero||output.Length<=0||output.Length>16384)throw Fail("GMGN_AUTH_DPAPI");
   byte[] result=new byte[output.Length];Marshal.Copy(output.Data,result,0,result.Length);return result;
  }finally{
   Marshal.Copy(new byte[data.Length],0,data.Data,data.Length);Marshal.FreeHGlobal(data.Data);
   Marshal.Copy(new byte[extra.Length],0,extra.Data,extra.Length);Marshal.FreeHGlobal(extra.Data);Wipe(entropy);
   if(output.Data!=IntPtr.Zero){if(!protect&&output.Length>0&&output.Length<=16384)Marshal.Copy(new byte[output.Length],0,output.Data,output.Length);FreeDescriptor(output.Data);}
  }
 }
 public static GmgnReadLocalAuth CreateNew(string directoryPath,string ownerSid,byte[] localIpcAsciiKey){
  return CreateNewCore(directoryPath,ownerSid,localIpcAsciiKey,null);
 }
 public static GmgnReadLocalAuth CreateNewFixture(string directoryPath,string ownerSid,byte[] localIpcAsciiKey,string ownFixtureScope){
  if(ownFixtureScope==null)throw Fail("GMGN_AUTH_FIXTURE_SCOPE");return CreateNewCore(directoryPath,ownerSid,localIpcAsciiKey,ownFixtureScope);
 }
 static GmgnReadLocalAuth CreateNewCore(string directoryPath,string ownerSid,byte[] localIpcAsciiKey,string ownFixtureScope){
  var value=new GmgnReadLocalAuth(directoryPath,ownerSid,ownFixtureScope);byte[] copy=null;IntPtr descriptor=IntPtr.Zero;
  try{
   ValidateKey(localIpcAsciiKey);copy=(byte[])localIpcAsciiKey.Clone();byte[] cipher=value.Crypt(copy,true);
   Security security=value.Descriptor(out descriptor);
   if(!CreateDirectoryW(value.directory,ref security))throw Fail("GMGN_AUTH_DIRECTORY_CREATE_NEW");
   value.InitializeDirectory();
   IntPtr attributes=Marshal.AllocHGlobal(Marshal.SizeOf(typeof(Security)));
   Handle writer;
   try{Marshal.StructureToPtr(security,attributes,false);writer=value.Open(value.path,0xc0020000U,1,1,false,attributes);}finally{Marshal.FreeHGlobal(attributes);}
   uint written;if(!WriteFile(writer.Value,cipher,(uint)cipher.Length,out written,IntPtr.Zero)||written!=cipher.Length||!FlushFileBuffers(writer.Value))throw Fail("GMGN_AUTH_WRITE_FLUSH");
   value.fileIdentity=value.Inspect(writer,value.path,false,out value.fileLength);value.cipherHash=Hash(cipher);
   Handle overlapping=value.Open(value.path,0x80020000U,3,3,false,IntPtr.Zero);
   value.Read(overlapping);value.CloseOnce(writer,"WRITER_CLOSE");value.Probe("AFTER_WRITER_CLOSE");
   // Identity stays pinned by overlapping, but WRITE is temporarily shared.
   // Final no-WRITE open plus full revalidation is the authority boundary.
   value.file=value.Open(value.path,0x80020000U,1,3,false,IntPtr.Zero);
   byte[] decrypted=value.Crypt(value.Read(value.file),false);
   try{ValidateKey(decrypted);int difference=0;for(int i=0;i<64;i++)difference|=decrypted[i]^copy[i];if(difference!=0)throw Fail("GMGN_AUTH_DPAPI_READBACK");value.key=(byte[])decrypted.Clone();}finally{Wipe(decrypted);}
   value.CloseOnce(overlapping,"OVERLAP_CLOSE");value.complete=true;value.Check();return value;
  }catch{value.fault=true;throw;}finally{Wipe(copy);try{FreeDescriptor(descriptor);}catch{value.fault=true;throw;}}
 }
 void InitializeDirectory(){
  // Additional READ_CONTROL handle is required for ACL inspection; the
  // namespace helper itself deliberately has narrower attribute rights.
  namespacePin=new GmgnReadDirectoryIdentity(directory);
  folder=Open(directory,0x000200a0U,3,3,true,IntPtr.Zero);long length;
  directoryIdentity=Inspect(folder,directory,true,out length);namespacePin.Check();
 }
 public static GmgnReadLocalAuth OpenExisting(string directoryPath,string ownerSid,string expectedDirectoryId,string expectedFileId,string expectedCipherHash){
  var value=new GmgnReadLocalAuth(directoryPath,ownerSid,null);
  try{
   value.InitializeDirectory();if(value.directoryIdentity!=expectedDirectoryId)throw Fail("GMGN_AUTH_DIRECTORY_BINDING");
   value.file=value.Open(value.path,0x80020000U,1,3,false,IntPtr.Zero);
   value.fileIdentity=value.Inspect(value.file,value.path,false,out value.fileLength);value.cipherHash=expectedCipherHash;
   if(value.fileIdentity!=expectedFileId)throw Fail("GMGN_AUTH_FILE_BINDING");
   value.key=value.Crypt(value.Read(value.file),false);ValidateKey(value.key);value.complete=true;value.Check();return value;
  }catch{value.fault=true;throw;}
 }
 public void Check(){lock(sync)try{
  if(fault||!complete)throw Fail("GMGN_AUTH_FAULT");namespacePin.Check();long length;
  if(Inspect(folder,directory,true,out length)!=directoryIdentity||Inspect(file,path,false,out length)!=fileIdentity||length!=fileLength)throw Fail("GMGN_AUTH_NATIVE_CHANGED");
 }catch{fault=true;throw;}}
 internal void InHealthyAuthentication(Action work){lock(sync)try{Check();work();Check();}catch{fault=true;throw;}}
 public string[] ReadPublicBinding(){lock(sync){Check();return new string[]{"1",directory,sid,directoryIdentity,fileIdentity,cipherHash};}}
 public string Sign(string body){lock(sync)try{
  Check();if(String.IsNullOrEmpty(body)||body.Length>32768)throw Fail("GMGN_AUTH_BODY_SIZE");
  var utf8=new UTF8Encoding(false,true);int count;
  try{count=utf8.GetByteCount(body);}catch(EncoderFallbackException){throw Fail("GMGN_AUTH_BODY_ENCODING");}
  if(count>32768)throw Fail("GMGN_AUTH_BODY_SIZE");byte[] bytes=utf8.GetBytes(body);
  string mac;using(var hmac=new HMACSHA256(key))mac=BitConverter.ToString(hmac.ComputeHash(bytes)).Replace("-","").ToLowerInvariant();Check();return mac;
 }catch{fault=true;throw;}}
 public bool Verify(string body,string mac){lock(sync)try{
  Check();if(mac==null||!System.Text.RegularExpressions.Regex.IsMatch(mac,@"\A[0-9a-f]{64}\z"))throw Fail("GMGN_AUTH_MAC_SHAPE");
  string actual=Sign(body);int difference=0;for(int i=0;i<64;i++)difference|=actual[i]^mac[i];Check();return difference==0;
 }catch{fault=true;throw;}}
 public void Abort(){lock(sync)fault=true;}
 public bool Faulted{get{lock(sync)return fault;}}
}
