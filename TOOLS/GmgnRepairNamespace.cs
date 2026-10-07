// Broker-lifetime READ/traverse namespace pins. No timeout reset, child file
// writes, delete authority or retry. The broker's actual exit reclaims pins.
using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
public sealed class GmgnRepairNamespace {
 [StructLayout(LayoutKind.Sequential)]struct Standard {public long Allocation,Bytes;public uint Links;public byte Pending,Directory;}
 [StructLayout(LayoutKind.Sequential)]struct Tag {public uint Attributes,Reparse;}
 [StructLayout(LayoutKind.Sequential)]struct ID {public ulong Volume;[MarshalAs(UnmanagedType.ByValArray,SizeConst=16)]public byte[] Bytes;}
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern IntPtr CreateFileW(string path,uint access,uint share,IntPtr security,uint creation,uint flags,IntPtr template);
 [DllImport("kernel32.dll",SetLastError=true)]static extern uint GetFileType(IntPtr handle);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool GetFileInformationByHandleEx(IntPtr h,int kind,ref Standard value,uint bytes);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool GetFileInformationByHandleEx(IntPtr h,int kind,ref Tag value,uint bytes);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool GetFileInformationByHandleEx(IntPtr h,int kind,ref ID value,uint bytes);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern uint GetFinalPathNameByHandleW(IntPtr h,StringBuilder path,uint size,uint flags);
 sealed class Pin {public IntPtr Handle;public string Path,Identity;}
 static readonly List<GmgnRepairNamespace> retained=new List<GmgnRepairNamespace>();
 readonly object sync=new object();readonly List<Pin> pins=new List<Pin>();bool fault;
 public readonly string Directory;
 static string Canonical(string value) {
  if(String.IsNullOrEmpty(value)||value.Length<3||value[1]!=':'||value[2]!='\\'||value.StartsWith(@"\\")||value.IndexOf('\0')>=0||value.IndexOf('/')>=0)
   throw new InvalidOperationException("GMGN_REPAIR_NAMESPACE_PATH_INVALID");
  foreach(string part in value.Substring(3).Split('\\'))if(part=="."||part==".."||part.IndexOf(':')>=0)
   throw new InvalidOperationException("GMGN_REPAIR_NAMESPACE_PATH_INVALID");
  string path=Path.GetFullPath(value);return path.Length==3?path:path.TrimEnd('\\');
 }
 public GmgnRepairNamespace(string directory) {
  Directory=Canonical(directory);lock(retained)retained.Add(this);
  try {
   if(IntPtr.Size!=8||Marshal.SizeOf(typeof(Standard))!=24||Marshal.SizeOf(typeof(Tag))!=8||
      Marshal.SizeOf(typeof(ID))!=24||Marshal.OffsetOf(typeof(Standard),"Directory").ToInt32()!=21)
    throw new InvalidOperationException("GMGN_REPAIR_NAMESPACE_ABI_INVALID");
   List<string> paths=new List<string>();string path=Directory;
   while(true){paths.Add(path);if(path.Length==3)break;path=Canonical(Path.GetDirectoryName(path));}
   paths.Reverse();
   foreach(string candidate in paths){
    Check();Pin pin=new Pin{Path=candidate};pins.Add(pin);
    pin.Handle=CreateFileW(candidate,0xA0U,3,IntPtr.Zero,3,0x2200000U,IntPtr.Zero);
    if(pin.Handle==IntPtr.Zero||pin.Handle==new IntPtr(-1))throw new InvalidOperationException("GMGN_REPAIR_NAMESPACE_OPEN_FAILED");
    pin.Identity=Inspect(pin);Check();
   }
  }catch{fault=true;throw;}
 }
 string Inspect(Pin pin) {
  Standard s=new Standard();Tag t=new Tag();ID id=new ID();StringBuilder actual=new StringBuilder(32768);
  if(GetFileType(pin.Handle)!=1||!GetFileInformationByHandleEx(pin.Handle,1,ref s,24)||
     !GetFileInformationByHandleEx(pin.Handle,9,ref t,8)||!GetFileInformationByHandleEx(pin.Handle,18,ref id,24)||
     s.Directory!=1||s.Pending!=0||(t.Attributes&0x400)!=0||t.Reparse!=0)
   throw new InvalidOperationException("GMGN_REPAIR_NAMESPACE_SHAPE_INVALID");
  uint count=GetFinalPathNameByHandleW(pin.Handle,actual,(uint)actual.Capacity,0);
  if(count==0||count>=actual.Capacity||!String.Equals(actual.ToString(),@"\\?\"+pin.Path,StringComparison.OrdinalIgnoreCase))
   throw new InvalidOperationException("GMGN_REPAIR_NAMESPACE_PATH_CHANGED");
  return id.Volume.ToString("x16")+":"+BitConverter.ToString(id.Bytes).Replace("-","").ToLowerInvariant();
 }
 public void Check() {
  lock(sync)try {
   if(fault)throw new InvalidOperationException("GMGN_REPAIR_NAMESPACE_FAULTED");
   foreach(Pin pin in pins)if(pin.Identity==null||Inspect(pin)!=pin.Identity)
    throw new InvalidOperationException("GMGN_REPAIR_NAMESPACE_ID_CHANGED");
  }catch{fault=true;throw;}
 }
 public void Abort(){lock(sync)fault=true;}
 public bool Faulted{get{lock(sync)return fault;}}
 public int HeldCount{get{lock(sync)return pins.Count;}}
}
