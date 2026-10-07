// Original process-object retention. Retirement is assembly-internal, one-shot,
// broker-only. Original handles remain retained even after terminal failure.
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public sealed class GmgnLegacyLifetimePins {
 [StructLayout(LayoutKind.Sequential)]struct Basic{public IntPtr Exit,Peb,Affinity,Priority,Pid,Parent;}
 [StructLayout(LayoutKind.Sequential)]struct Time{public uint Low,High;}
 [DllImport("kernel32.dll",SetLastError=true)]static extern IntPtr OpenProcess(uint access,bool inherit,uint pid);
 [DllImport("kernel32.dll",SetLastError=true)]static extern uint WaitForSingleObject(IntPtr handle,uint ms);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool TerminateProcess(IntPtr handle,uint code);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool GetProcessTimes(IntPtr h,out Time b,out Time e,out Time k,out Time u);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern bool QueryFullProcessImageNameW(IntPtr h,uint f,StringBuilder p,ref uint n);
 [DllImport("ntdll.dll")]static extern int NtQueryInformationProcess(IntPtr h,int c,out Basic b,uint n,out uint actual);
 static readonly List<GmgnLegacyLifetimePins> retained=new List<GmgnLegacyLifetimePins>();
 readonly object sync=new object();readonly IntPtr broker,parent;readonly int brokerPid,parentPid;
 readonly string brokerBirth,parentBirth,executable;bool complete,fault,retireAttempted,retired;IntPtr termination;
 Func<string,int> retireFixtureProbe=null;
 public GmgnLegacyLifetimePins(int brokerId,string brokerCreated,int parentId,string parentCreated,string exe){
  lock(retained)retained.Add(this);
  try{
   if(brokerId<=0||parentId<=0||brokerId==parentId||String.IsNullOrEmpty(exe)||!System.IO.Path.IsPathRooted(exe))throw new InvalidOperationException("GMGN_LEGACY_PIN_INPUT");
   brokerPid=brokerId;parentPid=parentId;brokerBirth=brokerCreated;parentBirth=parentCreated;executable=System.IO.Path.GetFullPath(exe);
   broker=OpenProcess(0x100400,false,(uint)brokerPid);if(broker==IntPtr.Zero)throw new InvalidOperationException("GMGN_LEGACY_PIN_OPEN");
   CheckLive(broker,brokerPid,brokerBirth,parentPid);
   parent=OpenProcess(0x100400,false,(uint)parentPid);if(parent==IntPtr.Zero)throw new InvalidOperationException("GMGN_LEGACY_PIN_OPEN");
   CheckLive(parent,parentPid,parentBirth,-1);CheckLive(broker,brokerPid,brokerBirth,parentPid);complete=true;
  }catch{fault=true;throw;}
 }
 static bool Exited(IntPtr h){uint result=WaitForSingleObject(h,0);if(result==0)return true;if(result==258)return false;throw new InvalidOperationException("GMGN_LEGACY_PIN_WAIT");}
 int CheckLive(IntPtr h,int pid,string birth,int parentId){
  Basic b;uint actual;Time created,e,k,u;var p=new StringBuilder(32768);uint n=(uint)p.Capacity;
  if(Exited(h)||NtQueryInformationProcess(h,0,out b,(uint)Marshal.SizeOf(typeof(Basic)),out actual)!=0||actual!=Marshal.SizeOf(typeof(Basic))||b.Pid.ToInt64()!=pid||
   (parentId>=0&&b.Parent.ToInt64()!=parentId)||!GetProcessTimes(h,out created,out e,out k,out u)||
   !QueryFullProcessImageNameW(h,0,p,ref n)||n==0||n>=p.Capacity||!String.Equals(p.ToString(),executable,StringComparison.OrdinalIgnoreCase)||
   DateTime.FromFileTimeUtc((long)(((ulong)created.High<<32)|created.Low)).ToString("o")!=birth||Exited(h)||b.Parent.ToInt64()<0||b.Parent.ToInt64()>Int32.MaxValue)throw new InvalidOperationException("GMGN_LEGACY_PIN_BINDING");
  return (int)b.Parent.ToInt64();
 }
 public bool[] Status(){lock(sync)try{if(!complete||fault)throw new InvalidOperationException("GMGN_LEGACY_PIN_FAULT");return new bool[]{Exited(broker),Exited(parent)};}catch{fault=true;throw;}}
 // Observation grants no new admission and stays usable after FAULT. No handle
 // is released, including after ParentExit; PID reuse stays impossible here.
 public bool[] ObserveOriginalExits(){lock(sync){if(broker==IntPtr.Zero||parent==IntPtr.Zero)throw new InvalidOperationException("GMGN_LEGACY_PIN_INCOMPLETE");return new bool[]{Exited(broker),Exited(parent)};}}
 int RetireProbe(string scope,string phase){if(retireFixtureProbe==null)return -1;
  if(String.IsNullOrEmpty(scope)||!System.Text.RegularExpressions.Regex.IsMatch(scope,@"\AC:\\Users\\<local-user>\\Desktop\\IMC_TRADING_MAIN\\[.]local-tools\\legacy-capture-native-[0-9a-f]{32}\z"))throw new InvalidOperationException("GMGN_LEGACY_RETIRE_FIXTURE_FORBIDDEN");
  return retireFixtureProbe(phase);
 }
 static void OwnerDeadline(long end){if(end<=0||System.Diagnostics.Stopwatch.GetTimestamp()>=end)throw new InvalidOperationException("GMGN_LEGACY_OWNER_PROOF_EXPIRED");}
 internal void RetireBrokerOnce(string validatedOwnScope,long ownerDeadline){RetireBrokerOnce(validatedOwnScope,ownerDeadline,null);}
 internal void RetireBrokerOnce(string validatedOwnScope,long ownerDeadline,Action<Action> effectBoundary){lock(sync){
  if(retireAttempted)throw new InvalidOperationException("GMGN_LEGACY_RETIRE_USED");retireAttempted=true;
  try{
   if(!complete||fault)throw new InvalidOperationException("GMGN_LEGACY_PIN_FAULT");
   OwnerDeadline(ownerDeadline);
   CheckLive(parent,parentPid,parentBirth,-1);
   if(Exited(broker)){retired=true;return;}
   CheckLive(broker,brokerPid,brokerBirth,parentPid);
   RetireProbe(validatedOwnScope,"BEFORE_OPEN");
   // The permanently retained original handle prevents reuse before this
   // one additional handle is acquired and natively bound to that object.
   termination=OpenProcess(0x100401,false,(uint)brokerPid);
   if(termination==IntPtr.Zero)throw new InvalidOperationException("GMGN_LEGACY_RETIRE_OPEN");
   RetireProbe(validatedOwnScope,"AFTER_OPEN");
   CheckLive(termination,brokerPid,brokerBirth,parentPid);
   int injected=RetireProbe(validatedOwnScope,"TERMINATE");
   // Recheck after locks, native binding, and own probes, immediately before
   // the actual effect. The authorized deadline is immutable for this call.
   Action nativeEffect=delegate{
    OwnerDeadline(ownerDeadline);
    if(injected>0||injected<0&&!TerminateProcess(termination,125))throw new InvalidOperationException("GMGN_LEGACY_RETIRE_TERMINATE");
   };
   // The trusted internal boundary executes synchronously, holds no Plan
   // or phase monitor and releases before WAIT. No probe executes inside it.
   if(effectBoundary==null)nativeEffect();else effectBoundary(nativeEffect);
   injected=RetireProbe(validatedOwnScope,"WAIT");
   uint waited=injected<0?WaitForSingleObject(termination,10000):(uint)injected;
   if(waited!=0||!Exited(broker))throw new InvalidOperationException("GMGN_LEGACY_RETIRE_EXIT_UNCONFIRMED");
   retired=true;
  }catch{fault=true;throw;}
 }}
 public bool Retired{get{lock(sync)return retired&&!fault;}}
 public void CheckAlive(){lock(sync)try{if(!complete||fault)throw new InvalidOperationException("GMGN_LEGACY_PIN_FAULT");CheckLive(broker,brokerPid,brokerBirth,parentPid);CheckLive(parent,parentPid,parentBirth,-1);}catch{fault=true;throw;}}
 public string[][] Bindings(){lock(sync)try{
  if(!complete||fault)throw new InvalidOperationException("GMGN_LEGACY_PIN_FAULT");
  int bp=CheckLive(broker,brokerPid,brokerBirth,parentPid),pp=CheckLive(parent,parentPid,parentBirth,-1);
  return new string[][]{new string[]{brokerPid.ToString(),bp.ToString(),brokerBirth,executable},new string[]{parentPid.ToString(),pp.ToString(),parentBirth,executable}};
 }catch{fault=true;throw;}}
 public int BrokerPid{get{return brokerPid;}}
 public int ParentPid{get{return parentPid;}}
 public string BrokerBirth{get{return brokerBirth;}}
 public string ParentBirth{get{return parentBirth;}}
 public bool Complete{get{lock(sync)return complete&&!fault;}}
 public bool Faulted{get{lock(sync)return fault;}}
}
