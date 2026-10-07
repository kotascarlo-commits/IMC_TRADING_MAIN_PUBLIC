# Native parent-owned READ guards. No controller, mutex authority or restart.
# Faulted/partially acquired groups stay retained in the living parent process.
if(-not ('IMCStorageGuards' -as [type])) {
    Add-Type -AssemblyName System.Numerics
    Add-Type -ReferencedAssemblies @('System.dll','System.Numerics.dll') -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Numerics;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;

public sealed class IMCStorageGuards {
 [StructLayout(LayoutKind.Sequential)] struct FileID {
  public ulong Volume; [MarshalAs(UnmanagedType.ByValArray,SizeConst=16)] public byte[] ID;
 }
 [StructLayout(LayoutKind.Sequential)] struct Standard {
  public long Allocation,Bytes; public uint Links; public byte DeletePending,Directory;
 }
 [StructLayout(LayoutKind.Sequential)] struct Tag { public uint Attributes,ReparseTag; }
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]
 static extern IntPtr CreateFileW(string p,uint access,uint share,IntPtr security,uint creation,uint flags,IntPtr template);
 [DllImport("kernel32.dll",SetLastError=true)][return:MarshalAs(UnmanagedType.Bool)]
 static extern bool CloseHandle(IntPtr h);
 [DllImport("kernel32.dll",SetLastError=true)][return:MarshalAs(UnmanagedType.Bool)]
 static extern bool GetFileInformationByHandleEx(IntPtr h,int kind,ref FileID value,uint size);
 [DllImport("kernel32.dll",SetLastError=true)][return:MarshalAs(UnmanagedType.Bool)]
 static extern bool GetFileInformationByHandleEx(IntPtr h,int kind,ref Standard value,uint size);
 [DllImport("kernel32.dll",SetLastError=true)][return:MarshalAs(UnmanagedType.Bool)]
 static extern bool GetFileInformationByHandleEx(IntPtr h,int kind,ref Tag value,uint size);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]
 static extern uint GetFinalPathNameByHandleW(IntPtr h,StringBuilder p,uint size,uint flags);
 [DllImport("kernel32.dll",SetLastError=true)] static extern uint GetFileType(IntPtr h);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern uint GetDriveTypeW(string root);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)][return:MarshalAs(UnmanagedType.Bool)]
 static extern bool GetVolumeInformationByHandleW(IntPtr h,IntPtr volume,uint volumeSize,IntPtr serial,
   IntPtr length,IntPtr flags,StringBuilder system,uint systemSize);
 [DllImport("kernel32.dll",SetLastError=true)][return:MarshalAs(UnmanagedType.Bool)]
 static extern bool SetFilePointerEx(IntPtr h,long distance,out long position,uint method);
 [DllImport("kernel32.dll",SetLastError=true)][return:MarshalAs(UnmanagedType.Bool)]
 static extern bool ReadFile(IntPtr h,byte[] buffer,uint size,out uint count,IntPtr overlapped);

 enum State {Owned,Closing,Closed,Fault}
 sealed class Proof {
  internal ulong Volume; internal byte[] ID; internal long Bytes;
  internal bool SameID(Proof other) {
   if(other==null || Volume!=other.Volume)return false;
   for(int i=0;i<16;i++)if(ID[i]!=other.ID[i])return false;
   return true;
  }
  internal bool Same(Proof other) {return SameID(other) && Bytes==other.Bytes;}
  internal string Json() {return IdentityJSON(Volume,ID,Bytes);}
 }
 sealed class Owned {
  internal IntPtr Handle; internal State State; internal string Path; internal bool Directory;
  internal Proof Initial;
 }
 static readonly object PoolLock=new object();
 static readonly List<IMCStorageGuards> Retained=new List<IMCStorageGuards>();
 readonly object serial=new object();
 readonly List<Owned> all=new List<Owned>();
 readonly Dictionary<string,Owned> files=new Dictionary<string,Owned>(StringComparer.Ordinal);
 readonly Dictionary<string,Owned> directories=new Dictionary<string,Owned>(StringComparer.OrdinalIgnoreCase);
 readonly Dictionary<string,Owned> sentinels=new Dictionary<string,Owned>(StringComparer.OrdinalIgnoreCase);
 readonly Stopwatch clock=Stopwatch.StartNew();
 readonly double seconds; readonly string root,nonce;
 readonly bool absolute;readonly long endTick,frequency;long lastTick;
 readonly Func<IntPtr,bool> closeNative=CloseHandle;
 bool fault,closed,strong;
 public bool Faulted {get {lock(serial)return fault;}}
 public bool Closed {get {lock(serial)return closed;}}
 public static int RetainedCount {get {lock(PoolLock)return Retained.Count;}}
 public static string IdentityJSON(ulong volume,byte[] id,long bytes) {
  if(id==null || id.Length!=16 || bytes<0)throw new InvalidOperationException("STORAGE_IDENTITY_INVALID");
  byte[] unsigned=new byte[17];Array.Copy(id,unsigned,16);
  return "{\"volume\":"+volume.ToString(CultureInfo.InvariantCulture)+",\"file_id\":"+
    new BigInteger(unsigned).ToString(CultureInfo.InvariantCulture)+",\"bytes\":"+
    bytes.ToString(CultureInfo.InvariantCulture)+"}";
 }
 static void ABI() {
  if(IntPtr.Size!=8 || Marshal.SizeOf(typeof(FileID))!=24 || Marshal.OffsetOf(typeof(FileID),"ID").ToInt32()!=8 ||
    Marshal.SizeOf(typeof(Standard))!=24 || Marshal.OffsetOf(typeof(Standard),"Links").ToInt32()!=16 ||
    Marshal.OffsetOf(typeof(Standard),"DeletePending").ToInt32()!=20 || Marshal.OffsetOf(typeof(Standard),"Directory").ToInt32()!=21 ||
    Marshal.SizeOf(typeof(Tag))!=8)throw new InvalidOperationException("STORAGE_ABI_INVALID");
 }
 static Exception NativeError(int code) {return new InvalidOperationException("STORAGE_NATIVE_ERROR_"+code.ToString(CultureInfo.InvariantCulture));}
 static string Canonical(string path) {
  if(String.IsNullOrEmpty(path) || path.IndexOf('\0')>=0 || path.StartsWith(@"\\") ||
    path.Length<3 || path[1]!=':' || (path[2]!='\\' && path[2]!='/'))throw new InvalidOperationException("STORAGE_PATH_INVALID");
  foreach(string part in path.Substring(2).Split(new char[]{'\\','/'}))
   if(part==".." || part=="." || part.IndexOf(':')>=0)throw new InvalidOperationException("STORAGE_PATH_INVALID");
  string full=Path.GetFullPath(path);return full.Length==3?full:full.TrimEnd('\\');
 }
 string Bound(string path,bool allowRoot) {
  path=Canonical(path);
  if(!path.StartsWith(root+"\\",StringComparison.OrdinalIgnoreCase) &&
    !(allowRoot && String.Equals(path,root,StringComparison.OrdinalIgnoreCase)))throw new InvalidOperationException("STORAGE_PATH_OUTSIDE_ROOT");
  return path;
 }
 void Check() {
  if(fault || closed)throw new InvalidOperationException("STORAGE_GROUP_UNCONFIRMED");
  if(absolute) {
   long now=Stopwatch.GetTimestamp();
   if(Stopwatch.Frequency!=frequency || now<0 || now<lastTick)
    throw new InvalidOperationException("STORAGE_DEADLINE_COUNTER");
   lastTick=now;
   if(now>=endTick-1)throw new TimeoutException("STORAGE_DEADLINE");
   return;
  }
  if(clock.Elapsed.TotalSeconds>=seconds)throw new TimeoutException("STORAGE_DEADLINE");
 }
 void Fail() {if(!closed)fault=true;}
 Owned Open(string path,bool directory,uint share,uint creation) {
  Check();IntPtr h=CreateFileW(path,directory?0xA0U:0x80000000U,share,IntPtr.Zero,creation,
    directory?0x2200000U:0x200080U,IntPtr.Zero);
  int error=Marshal.GetLastWin32Error();
  if(h==IntPtr.Zero || h==new IntPtr(-1))throw NativeError(error);
  Owned member=new Owned{Handle=h,State=State.Owned,Path=path,Directory=directory};
  all.Add(member); // Owner registered before any fallible inspection.
  member.Initial=Inspect(member);Check();return member;
 }
 string NativePath(Owned m) {
  StringBuilder value=new StringBuilder(32768);
  uint size=GetFinalPathNameByHandleW(m.Handle,value,(uint)value.Capacity,0);
  int error=Marshal.GetLastWin32Error();
  if(size==0 || size>=value.Capacity)throw NativeError(error);
  string path=value.ToString();
  if(path.StartsWith(@"\\?\UNC\",StringComparison.OrdinalIgnoreCase))throw new InvalidOperationException("STORAGE_REMOTE_PATH");
  if(path.StartsWith(@"\\?\"))path=path.Substring(4);
  string full=Path.GetFullPath(path);return full.Length==3?full:full.TrimEnd('\\');
 }
 Proof Inspect(Owned m) {return InspectAt(m,m.Path);}
 Proof InspectAt(Owned m,string expectedPath) {
  if(m.State!=State.Owned)throw new InvalidOperationException("STORAGE_HANDLE_NOT_OWNED");
  FileID id=new FileID();Standard s=new Standard();Tag t=new Tag();
  if(!GetFileInformationByHandleEx(m.Handle,18,ref id,24)){int e=Marshal.GetLastWin32Error();throw NativeError(e);}
  if(!GetFileInformationByHandleEx(m.Handle,1,ref s,24)){int e=Marshal.GetLastWin32Error();throw NativeError(e);}
  if(!GetFileInformationByHandleEx(m.Handle,9,ref t,8)){int e=Marshal.GetLastWin32Error();throw NativeError(e);}
  if(GetFileType(m.Handle)!=1 || s.DeletePending!=0 || (s.Directory!=0)!=m.Directory ||
     s.Bytes<0 || (!m.Directory && s.Links!=1) || (t.Attributes&0x400)!=0 || t.ReparseTag!=0)
   throw new InvalidOperationException("STORAGE_MEMBER_SHAPE");
  if(!String.Equals(NativePath(m),expectedPath,StringComparison.OrdinalIgnoreCase))throw new InvalidOperationException("STORAGE_NATIVE_PATH_CHANGED");
  return new Proof{Volume=id.Volume,ID=(byte[])id.ID.Clone(),Bytes=m.Directory?0:s.Bytes};
 }
 void Assert(Owned m) {Check();if(!m.Initial.Same(Inspect(m)))throw new InvalidOperationException("STORAGE_NATIVE_ID_CHANGED");if(absolute)Check();}
 void CloseOwned(Owned m) {
  if(m.State!=State.Owned)throw new InvalidOperationException("STORAGE_CLOSE_STATE");
  m.State=State.Closing;
  try {
   if(!closeNative(m.Handle)){int e=Marshal.GetLastWin32Error();m.State=State.Fault;Fail();throw NativeError(e);}
   m.Handle=IntPtr.Zero;m.State=State.Closed;
  } catch {if(m.State!=State.Closed){m.State=State.Fault;Fail();}throw;}
 }
 void PinChain(string leaf) {
  string drive=Path.GetPathRoot(leaf);
  if(GetDriveTypeW(drive)!=3)throw new InvalidOperationException("STORAGE_LOCAL_FIXED_DRIVE_REQUIRED");
  // Include the access chain above CallerRoot, not just the repository suffix.
  List<string> chain=new List<string>();string cursor=leaf;
  while(true){chain.Add(cursor);if(cursor.Length==3)break;string parent=Path.GetDirectoryName(cursor);if(String.IsNullOrEmpty(parent))break;cursor=parent.Length==3?parent:parent.TrimEnd('\\');}
  chain.Reverse();
  foreach(string path in chain) {
   Check();if(directories.ContainsKey(path)){Assert(directories[path]);continue;}
   Owned pin=Open(path,true,1,3);
   StringBuilder system=new StringBuilder(32);
   if(!GetVolumeInformationByHandleW(pin.Handle,IntPtr.Zero,0,IntPtr.Zero,IntPtr.Zero,IntPtr.Zero,system,32)) {
    int e=Marshal.GetLastWin32Error();throw NativeError(e);
   }
   if(system.ToString()!="NTFS")throw new InvalidOperationException("STORAGE_LOCAL_NTFS_REQUIRED");
   directories.Add(path,pin);
  }
 }
 public IMCStorageGuards(string projectRoot,string generationNonce,double budgetSeconds) {
  ABI();root=Canonical(projectRoot);nonce=generationNonce;seconds=budgetSeconds;
  if(root.Length<3 || !System.Text.RegularExpressions.Regex.IsMatch(nonce??"",@"\A[a-f0-9]{32}\z") ||
    Double.IsNaN(seconds) || Double.IsInfinity(seconds) || seconds<=0 || seconds>21600)
   throw new InvalidOperationException("STORAGE_GENERATION_INVALID");
  lock(PoolLock)Retained.Add(this);
 }
 // Shared original QPC boundary, never a renewed per-group relative budget.
 // Validation precedes registration; no native resources exist here yet.
 public IMCStorageGuards(string projectRoot,string generationNonce,long originalEndTick,long nativeFrequency) {
  ABI();root=Canonical(projectRoot);nonce=generationNonce;
  if(root.Length<3 || !System.Text.RegularExpressions.Regex.IsMatch(nonce??"",@"\A[a-f0-9]{32}\z") ||
    originalEndTick<=0 || nativeFrequency<=0 || nativeFrequency!=Stopwatch.Frequency)
   throw new InvalidOperationException("STORAGE_GENERATION_INVALID");
  long now=Stopwatch.GetTimestamp();BigInteger remaining=new BigInteger(originalEndTick)-now;
  if(now<0 || remaining<=1 || remaining>new BigInteger(nativeFrequency)*21600)
   throw new InvalidOperationException("STORAGE_GENERATION_INVALID");
  absolute=true;endTick=originalEndTick;frequency=nativeFrequency;lastTick=now;
  lock(serial)Check();
  lock(PoolLock)Retained.Add(this);
 }
 public void Acquire(string[] keys,string[] paths,string[] leafScopes) {
  AcquireInternal(keys,paths,leafScopes,false);
 }
 // Separate scope ownership keeps journal/IPC pins alive after Source Close.
 // It deliberately owns no dummy database file and grants no write authority.
 public void AcquireScopes(string[] leafScopes) {
  AcquireInternal(new string[0],new string[0],leafScopes,true);
 }
 void AcquireInternal(string[] keys,string[] paths,string[] leafScopes,bool scopesOnly) {
  lock(serial)try {
   Check();if(all.Count!=0 || keys==null || paths==null || keys.Length!=paths.Length || leafScopes==null ||
     (scopesOnly ? (keys.Length!=0 || leafScopes.Length==0) : keys.Length==0))
    throw new InvalidOperationException("STORAGE_ACQUIRE_INVALID");
   keys=(string[])keys.Clone();paths=(string[])paths.Clone();leafScopes=(string[])leafScopes.Clone();
   HashSet<string> leaves=new HashSet<string>(StringComparer.OrdinalIgnoreCase);
   for(int i=0;i<paths.Length;i++){paths[i]=Bound(paths[i],false);leaves.Add(Path.GetDirectoryName(paths[i]));}
   foreach(string leaf in leafScopes)leaves.Add(Bound(leaf,true));
   foreach(string leaf in leaves)PinChain(leaf);
   foreach(string leaf in leaves) {
    string path=Path.Combine(leaf,".imc-publish-guard-"+nonce+".sentinel");
    Owned sentinel=Open(path,false,1,1);
    if(sentinel.Initial.Bytes!=0)throw new InvalidOperationException("STORAGE_SENTINEL_SIZE");
    sentinels.Add(path,sentinel);
   }
   // Sentinels held before the overlapping READWRITE/noDELETE leaf upgrade.
   foreach(string leaf in leaves) {
    Owned previous=directories[leaf],next=Open(leaf,true,3,3);
    if(!previous.Initial.Same(next.Initial))throw new InvalidOperationException("STORAGE_DIRECTORY_HANDOFF_ID");
    CloseOwned(previous);directories[leaf]=next;
   }
   for(int i=0;i<keys.Length;i++) {
    if(String.IsNullOrEmpty(keys[i]) || files.ContainsKey(keys[i]))throw new InvalidOperationException("STORAGE_MEMBER_KEY");
    Owned file=Open(paths[i],false,5,3);
    foreach(Owned other in files.Values)
      if(other.Initial.Volume!=file.Initial.Volume || other.Initial.SameID(file.Initial))throw new InvalidOperationException("STORAGE_MEMBER_ALIAS_OR_VOLUME");
    files.Add(keys[i],file);
   }
   AssertAll();
  } catch {Fail();throw;}
 }
 // Append one READ/noWrite/noDelete record within an already prepared leaf.
 // No namespace/sentinel recreation and no release on partial acquisition.
 public void AddStrongRead(string key,string path) {
  lock(serial)try {
   AssertAll();
   if(String.IsNullOrEmpty(key)||files.ContainsKey(key)||(!strong&&files.Count!=0))
    throw new InvalidOperationException("STORAGE_STRONG_APPEND_INVENTORY");
   path=Bound(path,false);string leaf=Path.GetDirectoryName(path);
   string sentinel=Path.Combine(leaf,".imc-publish-guard-"+nonce+".sentinel");
   if(!directories.ContainsKey(leaf)||!sentinels.ContainsKey(sentinel))
    throw new InvalidOperationException("STORAGE_STRONG_APPEND_UNPINNED");
   string cursor=leaf;
   while(true){if(!directories.ContainsKey(cursor))throw new InvalidOperationException("STORAGE_STRONG_APPEND_CHAIN");
    if(cursor.Length==3)break;cursor=Canonical(Path.GetDirectoryName(cursor));}
   foreach(Owned pin in sentinels.Values)
    if(String.Equals(path,pin.Path,StringComparison.OrdinalIgnoreCase))throw new InvalidOperationException("STORAGE_STRONG_APPEND_SENTINEL");
   foreach(Owned old in files.Values)
    if(String.Equals(path,old.Path,StringComparison.OrdinalIgnoreCase))throw new InvalidOperationException("STORAGE_STRONG_APPEND_ALIAS");
   Owned acquired=Open(path,false,1,3);
   if(acquired.Initial.Volume!=directories[leaf].Initial.Volume||acquired.Initial.Volume!=sentinels[sentinel].Initial.Volume)
    throw new InvalidOperationException("STORAGE_STRONG_APPEND_VOLUME");
   foreach(Owned old in files.Values)
    if(acquired.Initial.Volume!=old.Initial.Volume||acquired.Initial.SameID(old.Initial))
     throw new InvalidOperationException("STORAGE_STRONG_APPEND_ALIAS");
   if(acquired.Initial.SameID(sentinels[sentinel].Initial))throw new InvalidOperationException("STORAGE_STRONG_APPEND_SENTINEL");
   files.Add(key,acquired);strong=true;AssertAll();
  }catch{Fail();throw;}
 }
 public string MemberJSON(string key) {lock(serial)try{AssertAll();string result=files[key].Initial.Json();if(absolute)Check();return result;}catch{Fail();throw;}}
 public string SentinelJSON(string path) {lock(serial)try{AssertAll();string result=sentinels[Bound(path,false)].Initial.Json();if(absolute)Check();return result;}catch{Fail();throw;}}
 public string DirectoryJSON(string path) {lock(serial)try{
  AssertAll();Proof info=directories[Path.GetFullPath(path)].Initial;
  byte[] unsigned=new byte[17];Array.Copy(info.ID,unsigned,16);
  string result="{\"volume\":"+info.Volume.ToString(CultureInfo.InvariantCulture)+",\"file_id\":"+
    new BigInteger(unsigned).ToString(CultureInfo.InvariantCulture)+"}";
  if(absolute)Check();return result;
 }catch{Fail();throw;}}
 public string[] DirectoryPaths {get {lock(serial)try{AssertAll();string[] result=new string[directories.Count];directories.Keys.CopyTo(result,0);if(absolute)Check();return result;}catch{Fail();throw;}}}
 public string[] SentinelPaths {get {lock(serial)try{AssertAll();string[] result=new string[sentinels.Count];sentinels.Keys.CopyTo(result,0);if(absolute)Check();return result;}catch{Fail();throw;}}}
 public void AssertAll() {lock(serial)try{Check();foreach(Owned m in files.Values)Assert(m);foreach(Owned m in directories.Values)Assert(m);foreach(Owned m in sentinels.Values)Assert(m);}catch{Fail();throw;}}
 public string Hash(string key) {
  lock(serial)try {
   AssertAll();Owned m=files[key];long position;
   if(!SetFilePointerEx(m.Handle,0,out position,0)){int e=Marshal.GetLastWin32Error();throw NativeError(e);}
   byte[] buffer=new byte[1048576];long length=0;string result;
   using(SHA256 hash=SHA256.Create()) {
    while(true){Check();uint count;if(!ReadFile(m.Handle,buffer,(uint)buffer.Length,out count,IntPtr.Zero)){int e=Marshal.GetLastWin32Error();throw NativeError(e);}
     Check();if(count==0)break;length+=count;hash.TransformBlock(buffer,0,(int)count,null,0);}
    hash.TransformFinalBlock(new byte[0],0,0);
    if(length!=m.Initial.Bytes)throw new InvalidOperationException("STORAGE_HASH_BYTES");
    AssertAll();result=BitConverter.ToString(hash.Hash).Replace("-","").ToLowerInvariant();
   }
   if(absolute)Check();return result;
  } catch {Fail();throw;}
 }
 // The controller supplies the complete phase-specific mapping from its
 // reviewed generation + exact journal step/hash. Native paths never provide
 // authority to choose that mapping. This method never opens/closes a file.
 public void RebindWeak(string[] keys,string[] expectedPaths) {
  lock(serial)try {
   Check();if(strong || keys==null || expectedPaths==null || keys.Length!=files.Count || keys.Length!=expectedPaths.Length)
    throw new InvalidOperationException("STORAGE_REBIND_INVENTORY");
   keys=(string[])keys.Clone();expectedPaths=(string[])expectedPaths.Clone();
   foreach(Owned pin in directories.Values)Assert(pin);
   foreach(Owned sentinel in sentinels.Values)Assert(sentinel);
   HashSet<string> inventory=new HashSet<string>(StringComparer.Ordinal);
   for(int i=0;i<keys.Length;i++) {
    string path=Bound(expectedPaths[i],false),leaf=Path.GetDirectoryName(path);
    if(!files.ContainsKey(keys[i]) || !inventory.Add(keys[i]) || !directories.ContainsKey(leaf) ||
       !sentinels.ContainsKey(Path.Combine(leaf,".imc-publish-guard-"+nonce+".sentinel")))
     throw new InvalidOperationException("STORAGE_REBIND_UNPINNED_DESTINATION");
    string cursor=leaf;
    while(true){if(!directories.ContainsKey(cursor))throw new InvalidOperationException("STORAGE_REBIND_UNPINNED_CHAIN");
     if(cursor.Length==3)break;string parent=Path.GetDirectoryName(cursor);cursor=parent.Length==3?parent:parent.TrimEnd('\\');}
    if(!files[keys[i]].Initial.Same(InspectAt(files[keys[i]],path)))throw new InvalidOperationException("STORAGE_REBIND_IDENTITY");
    expectedPaths[i]=path;
   }
   for(int i=0;i<keys.Length;i++)files[keys[i]].Path=expectedPaths[i];
   AssertAll();
  }catch{Fail();throw;}
 }
 public void TakeStrong(string[] keys,string[] expectedPaths) {
  lock(serial)try {
   Check();if(strong || keys==null || expectedPaths==null || keys.Length!=files.Count || keys.Length!=expectedPaths.Length)
    throw new InvalidOperationException("STORAGE_STRONG_INVENTORY");
   keys=(string[])keys.Clone();expectedPaths=(string[])expectedPaths.Clone();
   foreach(Owned pin in directories.Values)Assert(pin);
   foreach(Owned sentinel in sentinels.Values)Assert(sentinel);
   HashSet<string> inventory=new HashSet<string>(StringComparer.Ordinal);
   // Verify every registered destination chain before the first Strong open.
   for(int i=0;i<keys.Length;i++) {
    string path=Bound(expectedPaths[i],false),leaf=Path.GetDirectoryName(path);
    if(!files.ContainsKey(keys[i]) || !inventory.Add(keys[i]) || !directories.ContainsKey(leaf) ||
       !sentinels.ContainsKey(Path.Combine(leaf,".imc-publish-guard-"+nonce+".sentinel")))
     throw new InvalidOperationException("STORAGE_STRONG_UNPINNED_DESTINATION");
    string cursor=leaf;
    while(true){if(!directories.ContainsKey(cursor))throw new InvalidOperationException("STORAGE_STRONG_UNPINNED_CHAIN");
     if(cursor.Length==3)break;string parent=Path.GetDirectoryName(cursor);cursor=parent.Length==3?parent:parent.TrimEnd('\\');}
    if(!String.Equals(NativePath(files[keys[i]]),path,StringComparison.OrdinalIgnoreCase))throw new InvalidOperationException("STORAGE_HANDOFF_PATH");
    expectedPaths[i]=path;
   }
   Dictionary<string,Owned> next=new Dictionary<string,Owned>(StringComparer.Ordinal);
   for(int i=0;i<keys.Length;i++) {
    string key=keys[i],path=Bound(expectedPaths[i],false);Owned old=files[key];
    if(next.ContainsKey(key) || !String.Equals(NativePath(old),path,StringComparison.OrdinalIgnoreCase))throw new InvalidOperationException("STORAGE_HANDOFF_PATH");
    // Temporarily update only after native path equality; initial ID is immutable.
    old.Path=path;Assert(old);Owned acquired=Open(path,false,1,3);
    if(!old.Initial.Same(acquired.Initial))throw new InvalidOperationException("STORAGE_STRONG_ID");
    next.Add(key,acquired);
   }
   foreach(Owned item in next.Values)Assert(item);
   foreach(Owned item in files.Values)CloseOwned(item);
   files.Clear();foreach(KeyValuePair<string,Owned> item in next)files.Add(item.Key,item.Value);
   strong=true;AssertAll();
  } catch {Fail();throw;}
 }
 public void Close() {
  lock(serial) {
   if(fault || closed)throw new InvalidOperationException("STORAGE_CLOSE_UNCONFIRMED");
   for(int i=all.Count-1;i>=0;i--)if(all[i].State==State.Owned)CloseOwned(all[i]);
   closed=true;lock(PoolLock)Retained.Remove(this);
  }
 }
}
'@
}
