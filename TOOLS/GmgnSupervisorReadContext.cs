// The new supervisor role has no legacy Process.Start or repair fallback.
using System;
using System.Diagnostics;
using System.Collections.Generic;
public sealed class GmgnSupervisorReadContext {
 public sealed class ReadyBinding {
  readonly string[] self;readonly string directory,identity,first,primary,node,entry;
  internal ReadyBinding(string[] s,string d,string id,string f,string p,string n,string e){self=(string[])s.Clone();directory=d;identity=id;first=f;primary=p;node=n;entry=e;}
  public string[] Self{get{return (string[])self.Clone();}}
  public string Directory{get{return directory;}}public string DirectoryIdentity{get{return identity;}}
  public string FirstGeneration{get{return first;}}public string PrimaryGeneration{get{return primary;}}
  public string NodeExecutable{get{return node;}}public string CliEntry{get{return entry;}}
 }
 static readonly List<GmgnSupervisorReadContext> retained=new List<GmgnSupervisorReadContext>();
 readonly object sync=new object();readonly GmgnImmutableSources first,primary;
 readonly GmgnLegacyCapture capture;readonly GmgnLegacyLifetimePins legacy;
 readonly GmgnLegacyRetirement retirement;readonly GmgnReadDirectoryIdentity recovery,directory;
 readonly GmgnReadProcessLifetime factory;readonly string[] self;
 readonly string entry,node,brokerDirectory,primaryDirectory,fixture;bool complete,fault;
 Action afterReadySnapshotFixtureProbe=null;
 public GmgnSupervisorReadContext(GmgnImmutableSources firstGeneration,GmgnImmutableSources primaryGeneration,
  GmgnLegacyCapture captured,GmgnLegacyLifetimePins original,GmgnLegacyRetirement retired,
  GmgnReadDirectoryIdentity recoveryIdentity,string readDirectory,string nodeExecutable,string cliEntry,string readBootstrap)
  :this(firstGeneration,primaryGeneration,captured,original,retired,recoveryIdentity,readDirectory,nodeExecutable,cliEntry,readBootstrap,null){}
 public static GmgnSupervisorReadContext CreateOwnFixture(GmgnImmutableSources firstGeneration,GmgnImmutableSources primaryGeneration,
  GmgnLegacyCapture captured,GmgnLegacyLifetimePins original,GmgnLegacyRetirement retired,
  GmgnReadDirectoryIdentity recoveryIdentity,string readDirectory,string nodeExecutable,string cliEntry,string readBootstrap,string fixtureScope){
  if(retired==null||!retired.MatchesFixtureScope(fixtureScope))throw new InvalidOperationException("GMGN_SUPERVISOR_FIXTURE_FORBIDDEN");
  return new GmgnSupervisorReadContext(firstGeneration,primaryGeneration,captured,original,retired,recoveryIdentity,readDirectory,nodeExecutable,cliEntry,readBootstrap,fixtureScope);
 }
 GmgnSupervisorReadContext(GmgnImmutableSources firstGeneration,GmgnImmutableSources primaryGeneration,
  GmgnLegacyCapture captured,GmgnLegacyLifetimePins original,GmgnLegacyRetirement retired,
  GmgnReadDirectoryIdentity recoveryIdentity,string readDirectory,string nodeExecutable,string cliEntry,string readBootstrap,string fixtureScope){
  lock(retained)retained.Add(this);
  try{
   // Legacy-object construction is only an OWN test path. Production must
   // present the complete installation admission, never fall back here.
   if(fixtureScope==null)throw new InvalidOperationException("GMGN_SUPERVISOR_ADMISSION_REQUIRED");
   foreach(object value in new object[]{firstGeneration,primaryGeneration,captured,original,retired,recoveryIdentity})
    if(value==null||value.GetType().Assembly!=GetType().Assembly)throw new InvalidOperationException("GMGN_SUPERVISOR_CONTEXT_TYPE");
   if(Object.ReferenceEquals(firstGeneration,primaryGeneration)||!captured.MatchesOwner(original)||!retired.MatchesContext(firstGeneration,original,captured)||retired.Phase!="RETIRED"||!original.ObserveOriginalExits()[0])
    throw new InvalidOperationException("GMGN_SUPERVISOR_CONTEXT_RETIREMENT");
   first=firstGeneration;primary=primaryGeneration;capture=captured;legacy=original;retirement=retired;recovery=recoveryIdentity;
   entry=cliEntry;node=nodeExecutable;fixture=fixtureScope;brokerDirectory=System.IO.Path.GetFullPath(readDirectory);
   if(brokerDirectory!=readDirectory||brokerDirectory!=System.IO.Path.Combine(recovery.Path,"read"))throw new InvalidOperationException("GMGN_SUPERVISOR_DIRECTORY_BINDING");
   string contracts=fixtureScope==null?@"%USERPROFILE%\Desktop\IMC_TRADING_MAIN\TOOLS":System.IO.Path.Combine(fixtureScope,"primary");
   primaryDirectory=contracts;
   foreach(string p in new string[]{System.IO.Path.Combine(contracts,"GMGN-ReadBroker.ps1"),System.IO.Path.Combine(contracts,"GMGN-ReadProtocol.ps1")})
    if(!primary.Contains(p)||first.Contains(p))throw new InvalidOperationException("GMGN_SUPERVISOR_PRIMARY_GENERATION");
   if(!first.Contains(@"%USERPROFILE%\Desktop\IMC_TRADING_MAIN\TOOLS\GmgnSupervisorReadContext.cs"))throw new InvalidOperationException("GMGN_SUPERVISOR_CONTEXT_SOURCE");
   if(node!=@"C:\Program Files\nodejs\node.exe")throw new InvalidOperationException("GMGN_SUPERVISOR_DISPATCH_BINDING");
   if(fixtureScope==null){
    if(entry!=@"%USERPROFILE%\AppData\Roaming\npm\node_modules\gmgn-cli\dist\index.js"||readBootstrap!=System.IO.Path.Combine(contracts,"GMGN-ReadCliBootstrap.mjs"))throw new InvalidOperationException("GMGN_SUPERVISOR_DISPATCH_BINDING");
    foreach(string p in new string[]{node,entry,readBootstrap})if(!first.Contains(p)||!primary.Contains(p)||first.HashForPath(p)!=primary.HashForPath(p))throw new InvalidOperationException("GMGN_SUPERVISOR_SDK_GENERATION");
    foreach(string name in new string[]{"Invoke-GMGN-ReadBroker.ps1","GMGN-ReadClientBootstrap.ps1","GmgnRepairNamespace.cs","GmgnReadDirectoryIdentity.cs","GmgnRepairProcessBinding.cs","GmgnReadClientIdentity.cs"}){
     string p=System.IO.Path.Combine(contracts,name);if(!primary.Contains(p))throw new InvalidOperationException("GMGN_SUPERVISOR_CLIENT_SOURCE");
     if(name.EndsWith(".cs")&&(!first.Contains(p)||first.HashForPath(p)!=primary.HashForPath(p)))throw new InvalidOperationException("GMGN_SUPERVISOR_CLIENT_SOURCE");
    }
    foreach(string name in new string[]{"imc-gmgn-read-broker.json","imc-execution.json"})if(!primary.Contains(@"%USERPROFILE%\Desktop\IMC_TRADING_MAIN\config\"+name))throw new InvalidOperationException("GMGN_SUPERVISOR_CLIENT_CONFIG");
   }else if(entry!=System.IO.Path.Combine(contracts,"entry.mjs")||readBootstrap!=System.IO.Path.Combine(contracts,"bootstrap.mjs"))throw new InvalidOperationException("GMGN_SUPERVISOR_FIXTURE_FORBIDDEN");
   self=GmgnRepairProcessBinding.Read(Process.GetCurrentProcess());directory=new GmgnReadDirectoryIdentity(brokerDirectory);
   factory=new GmgnReadProcessLifetime(primary,node,entry,readBootstrap,Int32.Parse(self[0]),self[2],self[3]);
   complete=true;Check();
  }catch{Fail();throw;}
 }
 void Fail(){fault=true;if(factory!=null)factory.Abort();}
 public void Check(){lock(sync)try{
  if(!complete||fault)throw new InvalidOperationException("GMGN_SUPERVISOR_CONTEXT_FAULT");
  first.Check();primary.Check();capture.CheckCommitted();recovery.Check();directory.Check();factory.Check();
  if(retirement.Phase!="RETIRED"||!legacy.ObserveOriginalExits()[0])throw new InvalidOperationException("GMGN_SUPERVISOR_RETIREMENT_LOST");
  string[] actual=GmgnRepairProcessBinding.Read(Process.GetCurrentProcess());for(int i=0;i<4;i++)if(actual[i]!=self[i])throw new InvalidOperationException("GMGN_SUPERVISOR_SELF_CHANGED");
 }catch{Fail();throw;}}
 public GmgnReadProcessLifetime.ReadTicket StartRead(string expectedNode,string expectedEntry,string[] args){lock(sync){
  GmgnReadProcessLifetime.ReadTicket ticket=null;
  try{
   Check();if(expectedNode!=node||expectedEntry!=entry)throw new InvalidOperationException("GMGN_SUPERVISOR_DISPATCH_BINDING");
   ticket=factory.StartReadWithinGeneration(first,args);Check();return ticket;
  }catch{Fail();if(ticket!=null)try{ticket.Dispose();}catch{Fail();}throw;}
 }}
 public void Abort(){lock(sync)Fail();}
 public ReadyBinding ReadReadyBinding(){lock(sync)try{
  Check();var binding=new ReadyBinding(self,brokerDirectory,directory.Identity,first.Fingerprint(),primary.Fingerprint(),node,entry);
  if(afterReadySnapshotFixtureProbe!=null){
   if(!retirement.MatchesFixtureScope(fixture))throw new InvalidOperationException("GMGN_SUPERVISOR_FIXTURE_FORBIDDEN");
   afterReadySnapshotFixtureProbe();
  }
  Check();return binding;
 }catch{Fail();throw;}}
 string PrimaryPath(string name){
  if(name!="GMGN-ReadBroker.ps1"&&name!="GMGN-ReadProtocol.ps1")throw new InvalidOperationException("GMGN_SUPERVISOR_SCRIPT_BLOCKED");
  return System.IO.Path.Combine(primaryDirectory,name);
 }
 public string PrimaryScriptPath(string name){lock(sync)try{Check();return PrimaryPath(name);}catch{Fail();throw;}}
 public string ReadPrimaryScriptText(string name){lock(sync)try{Check();string text=primary.ReadText(PrimaryPath(name));Check();return text;}catch{Fail();throw;}}
 public string NodeExecutable{get{lock(sync){Check();return node;}}}
 public string CliEntry{get{lock(sync){Check();return entry;}}}
 public string[] Self{get{lock(sync){Check();return (string[])self.Clone();}}}
 public string Directory{get{return brokerDirectory;}}
 public string DirectoryIdentity{get{lock(sync){Check();return directory.Identity;}}}
 public string FirstGeneration{get{lock(sync){Check();return first.Fingerprint();}}}
 public string PrimaryGeneration{get{lock(sync){Check();return primary.Fingerprint();}}}
 public string State{get{lock(sync)return complete&&!fault?"READ_ENABLED":"FAULT";}}
}
