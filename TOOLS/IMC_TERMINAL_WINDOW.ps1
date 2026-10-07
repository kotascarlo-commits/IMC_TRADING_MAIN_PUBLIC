# Explicit CREATE_NEW_CONSOLE: a view must not share the calling console.
if(-not ('IMCTerminalWindow' -as [type])){Add-Type @'
using System;using System.Text;using System.ComponentModel;using System.Runtime.InteropServices;
public static class IMCTerminalWindow {
 [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)] struct Startup {
  public uint Size;public string Reserved,Desktop,Title;
  public uint X,Y,Width,Height,CharsX,CharsY,Fill,Flags;
  public ushort Show,ReservedSize;public IntPtr ReservedBytes,In,Out,Error;
 }
 [StructLayout(LayoutKind.Sequential)] struct ProcessInfo {public IntPtr Process,Thread;public uint Id,ThreadId;}
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool CreateProcess(
  string app,StringBuilder command,IntPtr pa,IntPtr ta,bool inherit,uint flags,IntPtr env,string cwd,ref Startup s,out ProcessInfo p);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool CloseHandle(IntPtr h);
 public static uint Open(string executable,string script,string cwd){
  return Open(executable,script,cwd,false);
 }
 public static uint Open(string executable,string script,string cwd,bool reality){
  if(!System.IO.Path.IsPathRooted(executable)||!System.IO.Path.IsPathRooted(script)||script.IndexOf('"')>=0)
   throw new ArgumentException("absolute_terminal_paths_required");
  Startup s=new Startup();s.Size=(uint)Marshal.SizeOf(s);s.Title="IMC Terminal Control Center";s.Flags=9;s.Show=1;s.CharsX=148;s.CharsY=60;
  ProcessInfo p;var cmd=new StringBuilder("\""+executable+"\" -NoProfile -ExecutionPolicy Bypass -File \""+script+"\" -Viewer"+(reality?" -Reality":""));
  if(!CreateProcess(executable,cmd,IntPtr.Zero,IntPtr.Zero,false,0x10,IntPtr.Zero,cwd,ref s,out p))throw new Win32Exception();
  bool a=CloseHandle(p.Thread),b=CloseHandle(p.Process);
  if(!a||!b)throw new InvalidOperationException("terminal_handle_close_unconfirmed");
  return p.Id;
 }
}
'@}
