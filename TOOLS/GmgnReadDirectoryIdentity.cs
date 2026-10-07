// Retained native directory identity. No files, secrets or process mutation.
using System;using System.IO;using System.Text;using System.Collections.Generic;using System.Runtime.InteropServices;
public sealed class GmgnReadDirectoryIdentity {
 [StructLayout(LayoutKind.Sequential)]struct Standard{public long Allocation,Bytes;public uint Links;public byte Pending,Directory;}
 [StructLayout(LayoutKind.Sequential)]struct Tag{public uint Attributes,Reparse;}
 [StructLayout(LayoutKind.Sequential)]struct ID{public ulong Volume;[MarshalAs(UnmanagedType.ByValArray,SizeConst=16)]public byte[] Bytes;}
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern IntPtr CreateFileW(string p,uint a,uint s,IntPtr security,uint c,uint f,IntPtr template);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool GetFileInformationByHandleEx(IntPtr h,int k,ref Standard v,uint n);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool GetFileInformationByHandleEx(IntPtr h,int k,ref Tag v,uint n);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool GetFileInformationByHandleEx(IntPtr h,int k,ref ID v,uint n);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern uint GetFinalPathNameByHandleW(IntPtr h,StringBuilder p,uint n,uint f);
 static readonly List<GmgnReadDirectoryIdentity> retained=new List<GmgnReadDirectoryIdentity>();
 readonly object sync=new object();readonly GmgnRepairNamespace names;readonly IntPtr handle;readonly string path,identity;bool fault;
 public GmgnReadDirectoryIdentity(string directory){lock(retained)retained.Add(this);try{
  path=System.IO.Path.GetFullPath(directory);if(path!=directory)throw new InvalidOperationException("GMGN_DIRECTORY_NONCANONICAL");
  names=new GmgnRepairNamespace(path);handle=CreateFileW(path,0xA0,3,IntPtr.Zero,3,0x2200000,IntPtr.Zero);
  if(handle==IntPtr.Zero||handle==new IntPtr(-1))throw new InvalidOperationException("GMGN_DIRECTORY_OPEN");identity=Inspect();Check();
 }catch{fault=true;throw;}}
 string Inspect(){Standard s=new Standard();Tag t=new Tag();ID id=new ID();var actual=new StringBuilder(32768);
  if(!GetFileInformationByHandleEx(handle,1,ref s,24)||!GetFileInformationByHandleEx(handle,9,ref t,8)||!GetFileInformationByHandleEx(handle,18,ref id,24)||
   s.Directory!=1||s.Pending!=0||(t.Attributes&0x400)!=0||t.Reparse!=0)throw new InvalidOperationException("GMGN_DIRECTORY_NATIVE_SHAPE");
  uint n=GetFinalPathNameByHandleW(handle,actual,(uint)actual.Capacity,0);
  if(n==0||n>=actual.Capacity||!String.Equals(actual.ToString(),@"\\?\"+path,StringComparison.OrdinalIgnoreCase))throw new InvalidOperationException("GMGN_DIRECTORY_NATIVE_PATH");
  return id.Volume.ToString("x16")+":"+BitConverter.ToString(id.Bytes).Replace("-","").ToLowerInvariant();
 }
 public void Check(){lock(sync)try{if(fault)throw new InvalidOperationException("GMGN_DIRECTORY_FAULT");names.Check();if(Inspect()!=identity)throw new InvalidOperationException("GMGN_DIRECTORY_CHANGED");}catch{fault=true;throw;}}
 public string Identity{get{lock(sync){Check();return identity;}}}
 public string Path{get{lock(sync){Check();return path;}}}
 public bool Faulted{get{lock(sync)return fault;}}
}
