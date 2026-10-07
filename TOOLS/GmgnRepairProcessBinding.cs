// Read-only held process metadata. No command lines, environment or secrets.
using System;
using System.Diagnostics;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public static class GmgnRepairProcessBinding {
 [StructLayout(LayoutKind.Sequential)]struct Basic {public IntPtr Exit,Peb,Affinity,Priority,Pid,Parent;}
 [StructLayout(LayoutKind.Sequential)]struct FileTime {public uint Low,High;}
 [DllImport("ntdll.dll")]static extern int NtQueryInformationProcess(IntPtr h,int kind,out Basic value,uint size,out uint actual);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool GetProcessTimes(IntPtr h,out FileTime birth,out FileTime exit,out FileTime kernel,out FileTime user);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern bool QueryFullProcessImageNameW(IntPtr h,uint flags,StringBuilder path,ref uint size);
 [DllImport("kernel32.dll",SetLastError=true)]static extern uint WaitForSingleObject(IntPtr h,uint timeout);
 [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)]struct Entry {
  public uint Size,Usage,Pid;public UIntPtr Heap;public uint Module,Threads,Parent;
  public int Priority;public uint Flags;[MarshalAs(UnmanagedType.ByValTStr,SizeConst=260)]public string Name;
 }
 [DllImport("kernel32.dll",SetLastError=true)]static extern IntPtr CreateToolhelp32Snapshot(uint flags,uint pid);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern bool Process32FirstW(IntPtr snapshot,ref Entry value);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern bool Process32NextW(IntPtr snapshot,ref Entry value);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool CloseHandle(IntPtr handle);
 public static int[] Children(int parent) {
  IntPtr snapshot=CreateToolhelp32Snapshot(2,0);
  if(snapshot==IntPtr.Zero||snapshot==new IntPtr(-1))throw new InvalidOperationException("GMGN_REPAIR_PROCESS_SNAPSHOT_FAILED");
  Exception failure=null;List<int> children=new List<int>();
  try {
   Entry value=new Entry();value.Size=(uint)Marshal.SizeOf(typeof(Entry));
   if(!Process32FirstW(snapshot,ref value))throw new InvalidOperationException("GMGN_REPAIR_PROCESS_FIRST_FAILED");
   do{if(value.Parent==parent){if(value.Pid>Int32.MaxValue)throw new InvalidOperationException("GMGN_REPAIR_CHILD_PID_INVALID");children.Add((int)value.Pid);}}
   while(Process32NextW(snapshot,ref value));
   if(Marshal.GetLastWin32Error()!=18)throw new InvalidOperationException("GMGN_REPAIR_PROCESS_NEXT_UNCONFIRMED");
  }catch(Exception error){failure=error;}
  if(!CloseHandle(snapshot))throw new InvalidOperationException("GMGN_REPAIR_SNAPSHOT_CLOSE_UNCONFIRMED");
  if(failure!=null)throw failure;
  return children.ToArray();
 }
 public static string[] Read(Process process) {
  if(process==null)throw new InvalidOperationException("GMGN_REPAIR_PROCESS_MISSING");
  IntPtr h=process.Handle;Basic value;uint actual;FileTime b,e,k,u;
  if(WaitForSingleObject(h,0)!=258 || NtQueryInformationProcess(h,0,out value,(uint)Marshal.SizeOf(typeof(Basic)),out actual)!=0 ||
      actual!=Marshal.SizeOf(typeof(Basic)) || value.Pid.ToInt64()!=process.Id ||
      !GetProcessTimes(h,out b,out e,out k,out u))throw new InvalidOperationException("GMGN_REPAIR_PROCESS_NATIVE_CHANGED");
  long parent=value.Parent.ToInt64();if(parent<0||parent>Int32.MaxValue)throw new InvalidOperationException("GMGN_REPAIR_PARENT_INVALID");
  StringBuilder path=new StringBuilder(32768);uint count=(uint)path.Capacity;
  if(!QueryFullProcessImageNameW(h,0,path,ref count)||count==0||count>=path.Capacity||WaitForSingleObject(h,0)!=258)
      throw new InvalidOperationException("GMGN_REPAIR_PROCESS_PATH_UNCONFIRMED");
  long birth=(long)(((ulong)b.High<<32)|b.Low);
  return new string[]{process.Id.ToString(),parent.ToString(),DateTime.FromFileTimeUtc(birth).ToString("o"),path.ToString()};
 }
}
