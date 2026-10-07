// Warm capture and authenticated retirement continuity. Production Warm stays WAIT.
// No SDK dispatch, READY, receipt or locator authority is exposed here.
using System;using System.Collections.Generic;using System.IO;
using System.Web.Script.Serialization;
using System.Runtime.InteropServices;
using System.Security.AccessControl;using System.Security.Principal;
public sealed partial class GmgnReadInstallPlan {
 bool legacyAdmissionAttempted;WarmValidationAdmission legacyAdmission;
 sealed class LegacyFactoryGrant {internal bool Consumed;}
 LegacyFactoryGrant legacyFactoryGrant;
 public WarmValidationAdmission CreateWarmValidationAdmission(){WarmValidationAdmission pending=null;try{
  LegacyFactoryGrant grant;lock(sync)try{
  if(legacyAdmissionAttempted)throw Error("GMGN_WARM_LEGACY_USED");legacyAdmissionAttempted=true;
  Check();RequireWarmTaskAction();
  if(warmOrigin==null||warmAdmission==null||!warmOrigin.Matches(first,auth,warmAdmission))throw Error("GMGN_WARM_LEGACY_ORIGIN");
  const string source=@"%USERPROFILE%\Desktop\IMC_TRADING_MAIN\TOOLS\GmgnReadInstallPlan.Legacy.cs";
  if(!first.Contains(source))throw Error("GMGN_WARM_LEGACY_SOURCE");
  legacyFactoryGrant=new LegacyFactoryGrant();grant=legacyFactoryGrant;
  }catch{MarkFault();throw;}
  // The role validator uses a fresh runspace. Never hold Plan while that
  // runspace calls the independently retained Source/Auth/Capture/Life pins.
  pending=WarmValidationAdmission.CreateFromFactory(this,grant);
  lock(sync){Check();if(!Object.ReferenceEquals(grant,legacyFactoryGrant)||!grant.Consumed||legacyAdmission!=null)throw Error("GMGN_WARM_LEGACY_FACTORY");pending.Check();legacyAdmission=pending;return pending;}
 }catch{Abort();if(pending!=null)pending.Abort();throw;}}
 public sealed partial class WarmValidationAdmission {
  [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern bool CreateDirectoryW(string path,IntPtr security);
  [StructLayout(LayoutKind.Sequential)]struct Security {public uint Length;public IntPtr Descriptor;[MarshalAs(UnmanagedType.Bool)]public bool Inherit;}
  [DllImport("advapi32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern bool ConvertStringSecurityDescriptorToSecurityDescriptorW(string text,uint revision,out IntPtr descriptor,out uint size);
  [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern IntPtr CreateFileW(string path,uint access,uint share,IntPtr security,uint creation,uint flags,IntPtr template);
  [DllImport("advapi32.dll")]static extern uint GetSecurityInfo(IntPtr handle,int type,uint info,out IntPtr owner,out IntPtr group,out IntPtr dacl,out IntPtr sacl,out IntPtr descriptor);
  [DllImport("advapi32.dll")]static extern uint GetSecurityDescriptorLength(IntPtr descriptor);
  [DllImport("kernel32.dll")]static extern IntPtr LocalFree(IntPtr descriptor);
  readonly GmgnReadInstallPlan plan;readonly GmgnHandoffCompletion.WarmOrigin origin;
  readonly GmgnSupervisorSourceAdmission admission;readonly string[] self;
  readonly string[] originBinding;
  readonly string fixture,sourceDirectory;GmgnLegacyLifetimePins lifetime;
  GmgnLegacyCapture capture;GmgnLegacyRetirement retirement;volatile bool fault;bool complete;
  GmgnReadDirectoryIdentity captureDirectory;IntPtr recoverySecurity,captureSecurity;
  static readonly List<WarmValidationAdmission> retained=new List<WarmValidationAdmission>();
  internal static WarmValidationAdmission CreateFromFactory(GmgnReadInstallPlan owner,object grant){
   if(owner==null)throw Error("GMGN_WARM_LEGACY_FACTORY");
   try{lock(owner.sync)try{
    if(!owner.legacyAdmissionAttempted||owner.legacyFactoryGrant==null||
     !Object.ReferenceEquals(grant,owner.legacyFactoryGrant)||owner.legacyFactoryGrant.Consumed)throw Error("GMGN_WARM_LEGACY_FACTORY");
    owner.legacyFactoryGrant.Consumed=true;
    if(!owner.first.Contains(@"%USERPROFILE%\Desktop\IMC_TRADING_MAIN\TOOLS\GmgnReadInstallPlan.Legacy.cs"))throw Error("GMGN_WARM_LEGACY_SOURCE");
    owner.Check();
   }catch{owner.MarkFault();throw;}
    return new WarmValidationAdmission(owner);
   }catch{owner.Abort();throw;}
  }
  WarmValidationAdmission(GmgnReadInstallPlan owner){
   plan=owner;origin=owner.warmOrigin;admission=owner.warmAdmission;self=(string[])owner.self.Clone();
   lock(retained)retained.Add(this);
   try{
    CheckOrigin();originBinding=origin.ReadBinding();if(originBinding.Length!=14)throw Error("GMGN_WARM_LEGACY_ORIGIN");
    var manifest=plan.ReadWarmManifest();object inventory;
    if(!manifest.TryGetValue("capture_inventory",out inventory))throw Error("GMGN_WARM_LEGACY_INVENTORY");
    var entries=inventory as System.Collections.IList;if(entries==null||entries.Count<1||entries.Count>512)throw Error("GMGN_WARM_LEGACY_INVENTORY");
    var names=new string[entries.Count];for(int i=0;i<entries.Count;i++){
     if(!(entries[i] is string))throw Error("GMGN_WARM_LEGACY_INVENTORY");names[i]=(string)entries[i];
    }
    string[] broker={"14552","3904","2026-10-02T15:14:56.6594833Z",@"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"};
    string[] parent={"3904","4176","2026-10-02T15:14:56.3325126Z",broker[3]};
    sourceDirectory=@"%USERPROFILE%\AppData\Local\Temp\imc-gmgn-broker-3904";
    object raw;if(manifest.TryGetValue("warm_legacy_fixture",out raw)){
     if(!(raw is string))throw Error("GMGN_WARM_LEGACY_FIXTURE");
     var parser=new JavaScriptSerializer{MaxJsonLength=32768,RecursionLimit=4};
     Dictionary<string,string> values;try{values=parser.Deserialize<Dictionary<string,string>>((string)raw);}catch{throw Error("GMGN_WARM_LEGACY_FIXTURE");}
     if(values==null||values.Count!=4)throw Error("GMGN_WARM_LEGACY_FIXTURE");
     var ordered=new SortedDictionary<string,string>(StringComparer.Ordinal);
     foreach(string key in new string[]{"broker_native","parent_native","scope","source_directory"}){
      string value;if(!values.TryGetValue(key,out value)||String.IsNullOrEmpty(value))throw Error("GMGN_WARM_LEGACY_FIXTURE");ordered.Add(key,value);
     }
     fixture=values["scope"];sourceDirectory=values["source_directory"];
     if(parser.Serialize(ordered)!=(string)raw||!System.Text.RegularExpressions.Regex.IsMatch(fixture,@"\AC:\\Users\\<local-user>\\Desktop\\IMC_TRADING_MAIN\\[.]local-tools\\legacy-capture-native-[0-9a-f]{32}\z")||
      sourceDirectory!=Path.Combine(fixture,"legacy"))throw Error("GMGN_WARM_LEGACY_FIXTURE");
     try{broker=parser.Deserialize<string[]>(values["broker_native"]);parent=parser.Deserialize<string[]>(values["parent_native"]);}catch{throw Error("GMGN_WARM_LEGACY_FIXTURE");}
     if(broker==null||parent==null||broker.Length!=4||parent.Length!=4||parser.Serialize(broker)!=values["broker_native"]||
      parser.Serialize(parent)!=values["parent_native"])throw Error("GMGN_WARM_LEGACY_FIXTURE");
    }
    const string exe=@"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe";
    int brokerPid,parentPid;
    if(broker[3]!=exe||parent[3]!=exe||!Int32.TryParse(broker[0],out brokerPid)||!Int32.TryParse(parent[0],out parentPid)||
     broker[1]!=parent[0])throw Error("GMGN_WARM_LEGACY_BINDING");
    // CheckOrigin uses short leaf calls. No Source/Auth/Capture/Lifetime
    // monitor remains held across CreateAuthenticated's fresh runspace.
    CheckOrigin();lifetime=new GmgnLegacyLifetimePins(brokerPid,broker[2],parentPid,parent[2],exe);
    string[][] actual=lifetime.Bindings();for(int r=0;r<2;r++)for(int i=0;i<4;i++)
     if(actual[r][i]!=(r==0?broker[i]:parent[i]))throw Error("GMGN_WARM_LEGACY_BINDING");
    CheckOrigin();string root=plan.recovery.Path;
    if(!System.Text.RegularExpressions.Regex.IsMatch(root,@"\AC:\\Users\\<local-user>\\Desktop\\IMC_TRADING_MAIN\\[.]local-tools\\gmgn-read-recovery-[0-9a-f]{32}\z"))throw Error("GMGN_WARM_LEGACY_CAPTURE_PATH");
    string target=Path.Combine(root,"capture");
    recoverySecurity=OpenSecurity(root);CheckProtectedAcl(recoverySecurity);plan.recovery.Check();
    // Native create is single-attempt: an existing target is never adopted.
    captureDirectory=CreateCaptureDirectory(target);captureDirectory.Check();
    captureSecurity=OpenSecurity(target);CheckProtectedAcl(captureSecurity);plan.recovery.Check();
    capture=new GmgnLegacyCapture(sourceDirectory,target,names,60,lifetime);CheckOrigin();
    retirement=GmgnLegacyRetirement.CreateAuthenticated(plan.first,lifetime,capture,sourceDirectory,fixture,plan.auth);
    CheckOrigin();lifetime.CheckAlive();capture.Copy();CheckOrigin();
    complete=true;Check();
   }catch{Fault();throw;}
  }
  static GmgnReadDirectoryIdentity CreateCaptureDirectory(string path){
   IntPtr descriptor=IntPtr.Zero,attributes=IntPtr.Zero;uint length;
   try{
    string sid=WindowsIdentity.GetCurrent().User.Value;
    if(!ConvertStringSecurityDescriptorToSecurityDescriptorW("O:"+sid+"D:P(A;;FA;;;"+sid+")(A;;FA;;;SY)",1,out descriptor,out length))throw Error("GMGN_WARM_LEGACY_SECURITY_DESCRIPTOR");
    var security=new Security{Length=(uint)Marshal.SizeOf(typeof(Security)),Descriptor=descriptor,Inherit=false};
    attributes=Marshal.AllocHGlobal(Marshal.SizeOf(typeof(Security)));Marshal.StructureToPtr(security,attributes,false);
    if(!CreateDirectoryW(path,attributes))throw Error("GMGN_WARM_LEGACY_CAPTURE_CREATE_NEW");
    // The identity class retains itself before inspection, including on
    // failure. Pin before any potentially failing descriptor cleanup.
    return new GmgnReadDirectoryIdentity(path);
   }finally{
    if(attributes!=IntPtr.Zero)Marshal.FreeHGlobal(attributes);
    if(descriptor!=IntPtr.Zero&&LocalFree(descriptor)!=IntPtr.Zero)throw Error("GMGN_WARM_LEGACY_SECURITY_FREE");
   }
  }
  static IntPtr OpenSecurity(string path){
   IntPtr handle=CreateFileW(path,0x000200a0U,3,IntPtr.Zero,3,0x02200000U,IntPtr.Zero);
   if(handle==IntPtr.Zero||handle==new IntPtr(-1))throw Error("GMGN_WARM_LEGACY_SECURITY_OPEN");return handle;
  }
  static void CheckProtectedAcl(IntPtr handle){
   IntPtr o,g,d,s,descriptor;if(GetSecurityInfo(handle,1,5,out o,out g,out d,out s,out descriptor)!=0||descriptor==IntPtr.Zero)throw Error("GMGN_WARM_LEGACY_SECURITY_READ");
   try{
    uint length=GetSecurityDescriptorLength(descriptor);if(length==0||length>32768)throw Error("GMGN_WARM_LEGACY_SECURITY_SIZE");
    var bytes=new byte[length];Marshal.Copy(descriptor,bytes,0,bytes.Length);var acl=new RawSecurityDescriptor(bytes,0);
    string sid=WindowsIdentity.GetCurrent().User.Value;
    if(acl.Owner==null||acl.Owner.Value!=sid||(acl.ControlFlags&ControlFlags.DiscretionaryAclProtected)==0||
     (acl.ControlFlags&ControlFlags.DiscretionaryAclPresent)==0||acl.DiscretionaryAcl==null||acl.DiscretionaryAcl.Count!=2)throw Error("GMGN_WARM_LEGACY_ACL");
    var seen=new HashSet<string>(StringComparer.Ordinal);
    foreach(GenericAce raw in acl.DiscretionaryAcl){var ace=raw as CommonAce;
     if(ace==null||ace.IsCallback||ace.AceQualifier!=AceQualifier.AccessAllowed||ace.AceFlags!=AceFlags.None||ace.AccessMask!=0x001f01ff||
      (ace.SecurityIdentifier.Value!=sid&&ace.SecurityIdentifier.Value!="S-1-5-18")||!seen.Add(ace.SecurityIdentifier.Value))throw Error("GMGN_WARM_LEGACY_ACL");
    }
   }finally{if(LocalFree(descriptor)!=IntPtr.Zero)throw Error("GMGN_WARM_LEGACY_SECURITY_FREE");}
  }
  void CheckOrigin(){
   // Factory admission already validated the immutable canonical action.
   // Plan.Check still checks the original actor's actual native commandline.
   plan.Check();
   if(!Object.ReferenceEquals(plan.warmOrigin,origin)||!Object.ReferenceEquals(plan.warmAdmission,admission)||
    origin==null||admission==null||!origin.Matches(plan.first,plan.auth,admission))throw Error("GMGN_WARM_LEGACY_ORIGIN");
   string[] current=plan.actor.ReadNativeBinding();for(int i=0;i<4;i++)if(current[i]!=self[i])throw Error("GMGN_WARM_LEGACY_SELF");
   plan.Check();
  }
  Action<string> checkpointFixtureProbe=null;
  void OwnCheckpointProbe(string phase){
   Action<string> probe=checkpointFixtureProbe;if(probe==null)return;
   if(System.Threading.Monitor.IsEntered(plan.sync)||System.Threading.Monitor.IsEntered(retirementSync)||
    !Object.ReferenceEquals(plan.legacyAdmission,this)||
    !System.Text.RegularExpressions.Regex.IsMatch(fixture??"",@"\AC:\\Users\\<local-user>\\Desktop\\IMC_TRADING_MAIN\\[.]local-tools\\legacy-capture-native-[0-9a-f]{32}\z"))throw Error("GMGN_WARM_CHECKPOINT_FIXTURE_FORBIDDEN");
   probe(phase);
  }
  void CheckOriginCheckpoint(){
   plan.CheckCheckpointEntry();
   if(!Object.ReferenceEquals(plan.warmOrigin,origin)||!Object.ReferenceEquals(plan.warmAdmission,admission)||origin==null||admission==null)throw Error("GMGN_WARM_LEGACY_ORIGIN");
   origin.CheckCheckpoint(plan.first,plan.auth,admission);
   string[] current=plan.actor.ReadNativeBinding();for(int i=0;i<4;i++)if(current[i]!=self[i])throw Error("GMGN_WARM_LEGACY_SELF");
   plan.Check();
  }
  void CheckValidatedOrigin(){
   plan.Check();
   if(!Object.ReferenceEquals(plan.warmOrigin,origin)||!Object.ReferenceEquals(plan.warmAdmission,admission)||origin==null||admission==null)throw Error("GMGN_WARM_LEGACY_ORIGIN");
   origin.CheckValidatedOrigin(plan.first,plan.auth,admission);
   string[] current=plan.actor.ReadNativeBinding();for(int i=0;i<4;i++)if(current[i]!=self[i])throw Error("GMGN_WARM_LEGACY_SELF");
   plan.Check();
  }
  void Fault(){fault=true;plan.Abort();lock(retirementSync)retirementState="FAULT";if(capture!=null)capture.Invalidate();}
  void CheckCommonContext(){
   if(fault||!complete)throw Error("GMGN_WARM_LEGACY_FAULT");
   CheckValidatedOrigin();capture.CheckCommitted();
   captureDirectory.Check();CheckProtectedAcl(recoverySecurity);CheckProtectedAcl(captureSecurity);
   if(!capture.MatchesOwner(lifetime)||!retirement.MatchesContext(plan.first,lifetime,capture))throw Error("GMGN_WARM_LEGACY_CONTEXT");
   OwnCheckpointProbe("COMMON_END");CheckOriginCheckpoint();
  }
  void CheckCapturedContext(){
   CheckCommonContext();if(retirement.Phase!="ADMITTED")throw Error("GMGN_WARM_LEGACY_CONTEXT");
   lifetime.CheckAlive();OwnCheckpointProbe("CAPTURED_END");CheckOriginCheckpoint();
  }
  public void Check(){try{
   // Monitoring is not retirement authority. In-flight retirement must not
   // fail merely because its original broker has just exited.
   CheckCommonContext();string phase;lock(retirementSync){if(fault)throw Error("GMGN_WARM_LEGACY_FAULT");phase=retirementState;}
   if(phase=="WAIT_PARENT"||phase=="BOTH_EXITED")CheckRetiredContext();
  }catch{Fault();throw;}}
  public string State{get{lock(retirementSync)return complete&&!fault?retirementState:"FAULT";}}
  public void Abort(){Fault();}
 }
}
