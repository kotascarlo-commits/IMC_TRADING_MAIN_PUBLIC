// Client-owned original process and directory pins. No SDK, writes or stop.
using System;using System.Diagnostics;using System.Collections.Generic;
public sealed class GmgnReadClientIdentity {
 static readonly List<GmgnReadClientIdentity> retained=new List<GmgnReadClientIdentity>();
 readonly object sync=new object();readonly Process process;readonly GmgnReadDirectoryIdentity directory;
 readonly string[] expected;readonly string directoryId;bool fault,complete;
 public GmgnReadClientIdentity(string path,string nativeDirectoryId,string[] nativeSelf){
  lock(retained)retained.Add(this);
  try{
   if(nativeSelf==null||nativeSelf.Length!=4||String.IsNullOrEmpty(nativeDirectoryId))throw new InvalidOperationException("GMGN_CLIENT_IDENTITY_INPUT");
   expected=(string[])nativeSelf.Clone();foreach(string value in expected)if(String.IsNullOrEmpty(value))throw new InvalidOperationException("GMGN_CLIENT_IDENTITY_INPUT");
   int pid;if(!Int32.TryParse(expected[0],out pid)||pid<=0)throw new InvalidOperationException("GMGN_CLIENT_IDENTITY_INPUT");
   directoryId=nativeDirectoryId;directory=new GmgnReadDirectoryIdentity(path);
   // Never borrow a mutable caller-supplied Process or expose this instance.
   process=Process.GetProcessById(pid);IntPtr original=process.Handle;
   if(original==IntPtr.Zero)throw new InvalidOperationException("GMGN_CLIENT_PROCESS_MISSING");
   complete=true;Check();
  }catch{fault=true;throw;}
 }
 public void Check(){lock(sync)try{
  if(fault||!complete)throw new InvalidOperationException("GMGN_CLIENT_IDENTITY_FAULT");
  directory.Check();if(directory.Identity!=directoryId)throw new InvalidOperationException("GMGN_CLIENT_DIRECTORY_CHANGED");
  string[] actual=GmgnRepairProcessBinding.Read(process);
  for(int i=0;i<4;i++)if(actual[i]!=expected[i])throw new InvalidOperationException("GMGN_CLIENT_PROCESS_CHANGED");
 }catch{fault=true;throw;}}
 public void Abort(){lock(sync)fault=true;}
 public string State{get{lock(sync)return complete&&!fault?"BOUND":"FAULT";}}
}
