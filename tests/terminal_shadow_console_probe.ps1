# Native acceptance helper: reads an existing viewer console; no trading actions.
if(-not ('IMCShadowConsoleProbe' -as [type])){Add-Type @'
using System;
using System.Text;
using System.ComponentModel;
using System.Runtime.InteropServices;
public static class IMCShadowConsoleProbe {
 [StructLayout(LayoutKind.Sequential)] struct Coord {public short X,Y;}
 [StructLayout(LayoutKind.Sequential)] struct Rect {public short Left,Top,Right,Bottom;}
 [StructLayout(LayoutKind.Sequential)] struct Info {public Coord Size,Cursor;public ushort Attributes;public Rect Window;public Coord Maximum;}
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool AttachConsole(uint pid);
 [DllImport("kernel32.dll")] static extern bool FreeConsole();
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern IntPtr CreateFile(string p,uint access,uint share,IntPtr security,uint mode,uint flags,IntPtr template);
 [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool GetConsoleScreenBufferInfo(IntPtr h,out Info b);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool ReadConsoleOutputCharacter(IntPtr h,StringBuilder text,uint n,Coord at,out uint read);
 public static string Read(uint pid){
  FreeConsole();if(!AttachConsole(pid))throw new Win32Exception();
  IntPtr h=IntPtr.Zero;
  try {
   h=CreateFile("CONOUT$",0x80000000,3,IntPtr.Zero,3,0,IntPtr.Zero);
   if(h==new IntPtr(-1))throw new Win32Exception();
   Info b;if(!GetConsoleScreenBufferInfo(h,out b))throw new Win32Exception();
   int width=b.Window.Right-b.Window.Left+1;var result=new StringBuilder();
   for(short y=b.Window.Top;y<=b.Window.Bottom;y++){
    var row=new StringBuilder(width+1);uint n;
    if(!ReadConsoleOutputCharacter(h,row,(uint)width,new Coord{X=b.Window.Left,Y=y},out n))throw new Win32Exception();
    result.Append(row.ToString(0,(int)n).TrimEnd()).AppendLine();
   }
   return result.ToString();
  }finally{if(h!=IntPtr.Zero&&h!=new IntPtr(-1))CloseHandle(h);FreeConsole();}
 }
}
'@}
