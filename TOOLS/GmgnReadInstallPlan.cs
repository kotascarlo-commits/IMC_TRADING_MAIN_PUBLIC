// Immutable installation plan binding only. PLAN_BOUND is never permission
// to dispatch, sign an installation receipt, retire a process or publish READY.
using System;using System.Collections.Generic;using System.Diagnostics;using System.IO;
using System.Security.Cryptography;using System.Security.Principal;using System.Text;
using System.Web.Script.Serialization;
public sealed partial class GmgnReadInstallPlan {
 readonly object sync=new object(),effectSync=new object();readonly GmgnImmutableSources first,planPin;
 readonly GmgnReadLocalAuth auth;readonly GmgnReadDirectoryIdentity recovery;
 readonly GmgnScheduledActionIdentity actor;readonly string path,hash,firstHash,firstFingerprint,authHash,recoveryId,installId,primaryPath,primaryHash;
 readonly string[] self;readonly string firstManifest,actionArguments;volatile bool fault;bool complete,effectCancelled;
 readonly long[] checkTimingTicks=new long[10];long checkTimingCount;
 static readonly List<GmgnReadInstallPlan> retained=new List<GmgnReadInstallPlan>();
 static InvalidOperationException Error(string code){return new InvalidOperationException(code);}
 internal static string Digest(string text){using(var sha=SHA256.Create())return BitConverter.ToString(sha.ComputeHash(new UTF8Encoding(false,true).GetBytes(text))).Replace("-","").ToLowerInvariant();}
 internal static string AuthDigest(GmgnReadLocalAuth value){return Digest(String.Join("\n",value.ReadPublicBinding()));}
 // The expected plan hash and encoded action arguments come from the fixed,
 // externally verified Task bootstrap, not from the plan being admitted.
 public GmgnReadInstallPlan(GmgnImmutableSources source1,GmgnImmutableSources heldPlan,string planPath,string externalPlanHash,
  string source1Manifest,string externalSource1Hash,GmgnReadLocalAuth authentication,GmgnReadDirectoryIdentity recoveryDirectory,
  string expectedInstallationId,string expectedActionArguments){
  lock(retained)retained.Add(this);
  try{
   foreach(object value in new object[]{source1,heldPlan,authentication,recoveryDirectory})
    if(value==null||value.GetType().Assembly!=GetType().Assembly)throw Error("GMGN_INSTALL_PLAN_TYPES");
   if(source1.IsAdvisorDomain||heldPlan.IsAdvisorDomain||Object.ReferenceEquals(source1,heldPlan)||heldPlan.Count!=1||
    !heldPlan.Contains(planPath)||Path.GetFileName(planPath)!="package.json"||source1.Contains(planPath)||
    !System.Text.RegularExpressions.Regex.IsMatch(externalPlanHash??"",@"\A[0-9a-f]{64}\z")||
    !System.Text.RegularExpressions.Regex.IsMatch(externalSource1Hash??"",@"\A[0-9a-f]{64}\z")||
    !System.Text.RegularExpressions.Regex.IsMatch(expectedInstallationId??"",@"\A[0-9a-f]{32}\z"))throw Error("GMGN_INSTALL_PLAN_BINDING");
   first=source1;planPin=heldPlan;auth=authentication;recovery=recoveryDirectory;
   path=planPath;hash=externalPlanHash;firstHash=externalSource1Hash;installId=expectedInstallationId;firstManifest=source1Manifest;actionArguments=expectedActionArguments;
   if(planPin.HashForPath(path)!=hash||!first.Contains(source1Manifest)||first.HashForPath(source1Manifest)!=firstHash)throw Error("GMGN_INSTALL_PLAN_GENERATION");
   string codePath=@"%USERPROFILE%\Desktop\IMC_TRADING_MAIN\TOOLS\GmgnReadInstallPlan.cs";
   if(!first.Contains(codePath))throw Error("GMGN_INSTALL_PLAN_SOURCE");
   firstFingerprint=first.Fingerprint();authHash=AuthDigest(auth);recoveryId=recovery.Identity;
   var serializer=new JavaScriptSerializer{MaxJsonLength=32768,RecursionLimit=4};
   string text=planPin.ReadText(path);Dictionary<string,string> values;
   try{values=serializer.Deserialize<Dictionary<string,string>>(text);}catch{throw Error("GMGN_INSTALL_PLAN_JSON");}
   string[] fields={"schema_version","purpose","installation_id","source1_manifest","source1_sha256","source1_fingerprint",
    "auth_binding_sha256","recovery_directory","recovery_identity","owner_sid","primary_plan","primary_plan_sha256"};
   if(values==null||values.Count!=fields.Length)throw Error("GMGN_INSTALL_PLAN_FIELDS");
   var canonical=new SortedDictionary<string,string>(StringComparer.Ordinal);
   foreach(string field in fields){string value;if(!values.TryGetValue(field,out value)||String.IsNullOrEmpty(value))throw Error("GMGN_INSTALL_PLAN_FIELDS");canonical.Add(field,value);}
   // Exact canonical bytes reject duplicate fields, alternate types, escapes,
   // ignored members and ambiguous parser representations.
   string canonicalText=serializer.Serialize(canonical);
   if(canonicalText!=text||Digest(canonicalText)!=hash)throw Error("GMGN_INSTALL_PLAN_NONCANONICAL");
   if(values["schema_version"]!="1"||values["purpose"]!="IMC_READ_INSTALL_PLAN_V1"||values["installation_id"]!=installId||
    values["source1_manifest"]!=source1Manifest||values["source1_sha256"]!=firstHash||values["source1_fingerprint"]!=firstFingerprint||
    values["auth_binding_sha256"]!=authHash||values["recovery_directory"]!=recovery.Path||values["recovery_identity"]!=recoveryId||
    values["owner_sid"]!=WindowsIdentity.GetCurrent().User.Value)throw Error("GMGN_INSTALL_PLAN_CONTEXT");
   primaryPath=values["primary_plan"];primaryHash=values["primary_plan_sha256"];
   if(!primaryPath.StartsWith(@"%USERPROFILE%\Desktop\IMC_TRADING_MAIN\.local-tools\",StringComparison.Ordinal)||
    primaryPath.IndexOf(':',2)>=0||primaryPath.IndexOf('/')>=0||
    !System.Text.RegularExpressions.Regex.IsMatch(primaryHash,@"\A[0-9a-f]{64}\z"))throw Error("GMGN_INSTALL_PLAN_PRIMARY");
   try{if(Path.GetFullPath(primaryPath)!=primaryPath||Path.GetFileName(primaryPath)!="package.json")throw Error("GMGN_INSTALL_PLAN_PRIMARY");}
   catch{throw Error("GMGN_INSTALL_PLAN_PRIMARY");}
   actor=GmgnScheduledActionIdentity.BindCurrentAction(WindowsIdentity.GetCurrent().User.Value,
    @"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",expectedActionArguments);
   self=actor.ReadNativeBinding();complete=true;Check();
  }catch{MarkFault();throw;}
 }
 public void Check(){CheckCore(true);}
 // Entry of a context-only checkpoint; callers must keep their full FINAL.
 // Original native source health is still checked at the first boundary.
 internal void CheckCheckpointEntry(){CheckCore(false);}
 void CheckCore(bool compareFingerprint){lock(sync)try{
  if(fault||!complete)throw Error("GMGN_INSTALL_PLAN_FAULT");
  long step=Stopwatch.GetTimestamp();first.Check();RecordCheckTiming(0,ref step);
  planPin.Check();RecordCheckTiming(1,ref step);auth.Check();RecordCheckTiming(2,ref step);
  recovery.Check();RecordCheckTiming(3,ref step);actor.Check();RecordCheckTiming(4,ref step);
  string heldPlanHash=planPin.HashForPath(path);RecordCheckTiming(5,ref step);
  // Preserve short-circuit order: later getters do not run after mismatch.
  if(heldPlanHash!=hash)throw Error("GMGN_INSTALL_PLAN_CHANGED");
  if(compareFingerprint){string currentFingerprint=first.Fingerprint();RecordCheckTiming(6,ref step);
   if(currentFingerprint!=firstFingerprint)throw Error("GMGN_INSTALL_PLAN_CHANGED");}
  string currentAuth=AuthDigest(auth);RecordCheckTiming(7,ref step);
  if(currentAuth!=authHash)throw Error("GMGN_INSTALL_PLAN_CHANGED");
  string currentRecovery=recovery.Identity;RecordCheckTiming(8,ref step);
  if(currentRecovery!=recoveryId)
   throw Error("GMGN_INSTALL_PLAN_CHANGED");
  string[] current=actor.ReadNativeBinding();RecordCheckTiming(9,ref step);checkTimingCount++;
  for(int i=0;i<4;i++)if(current[i]!=self[i])throw Error("GMGN_INSTALL_PLAN_SELF_CHANGED");
  if(current[0]!=Process.GetCurrentProcess().Id.ToString())throw Error("GMGN_INSTALL_PLAN_NOT_SELF");
 }catch{MarkFault();throw;}}
 void RecordCheckTiming(int index,ref long step){long now=Stopwatch.GetTimestamp();checkTimingTicks[index]+=now-step;step=now;}
 internal bool Matches(GmgnImmutableSources source1,GmgnReadLocalAuth authentication,GmgnReadDirectoryIdentity directory){lock(sync){Check();return Object.ReferenceEquals(first,source1)&&Object.ReferenceEquals(auth,authentication)&&Object.ReferenceEquals(recovery,directory);}}
 public string[] ReadBinding(){lock(sync){Check();return new string[]{"1",installId,path,hash,firstHash,firstFingerprint,authHash,recovery.Path,recoveryId,primaryPath,primaryHash};}}
 public string State{get{lock(sync)return complete&&!fault?"PLAN_BOUND":"FAULT";}}
 // Terminal leaf: no callback under effectSync may acquire another monitor.
 // After Abort returns no new TerminateProcess call can start. An effect
 // which already acquired this boundary may have completed before Abort.
 void MarkFault(){lock(effectSync){fault=true;effectCancelled=true;}}
 public void Abort(){MarkFault();}
 internal void CheckRetirementCancellation(){lock(effectSync){
  if(fault||effectCancelled)throw Error("GMGN_INSTALL_RETIRE_CANCELLED");
 }}
 internal void InRetirementEffect(Action effect){lock(effectSync){
  if(fault||effectCancelled)throw Error("GMGN_INSTALL_RETIRE_CANCELLED");
  effect();
 }}
 const string WarmArgumentsPrefix="-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -EncodedCommand ";
 const string WarmLoader=@"$ErrorActionPreference='Stop'
$imcWarmData=[Text.UTF8Encoding]::new($false,$true).GetString([Convert]::FromBase64String($imcWarmActionData))|ConvertFrom-Json -ErrorAction Stop
$imcWarmStreams=[Collections.Generic.List[IO.FileStream]]::new()
function Read-ImcWarmHeld([string]$Path,[string]$Hash){
 $s=[IO.FileStream]::new($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read);$imcWarmStreams.Add($s)
 if($s.Length -gt 2097152){throw 'GMGN_WARM_ACTION_SIZE'}
 $h=[Security.Cryptography.SHA256]::Create();try{$v=([BitConverter]::ToString($h.ComputeHash($s))).Replace('-','').ToLowerInvariant()}finally{$h.Dispose()}
 if($v -cne $Hash){throw 'GMGN_WARM_ACTION_HASH'}
 $s.Position=0;$r=[IO.StreamReader]::new($s,[Text.UTF8Encoding]::new($false,$true),$true,4096,$true);try{return $r.ReadToEnd()}finally{$r.Dispose()}
}
$null=Read-ImcWarmHeld $imcWarmData.install_plan $imcWarmData.install_plan_sha256
$null=Read-ImcWarmHeld $imcWarmData.source1_manifest $imcWarmData.source1_sha256
$imcWarmText=Read-ImcWarmHeld $imcWarmData.bootstrap $imcWarmData.bootstrap_sha256
$imcWarmExpectedArguments='-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -EncodedCommand '+[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($MyInvocation.MyCommand.ScriptBlock.ToString()))
$imcWarmTokens=$null;$imcWarmErrors=$null
$imcWarmAst=[Management.Automation.Language.Parser]::ParseInput($imcWarmText,$imcWarmData.bootstrap,[ref]$imcWarmTokens,[ref]$imcWarmErrors)
if($imcWarmErrors.Count){throw 'GMGN_WARM_ACTION_PARSE'}
& ($imcWarmAst.GetScriptBlock()) -ManifestPath $imcWarmData.source1_manifest -ExpectedGenerationSha256 $imcWarmData.source1_sha256 -ExpectedSelfSha256 $imcWarmData.bootstrap_sha256 -RecoveryDirectory $imcWarmData.recovery_directory -WarmInstallContract $imcWarmActionData -ExpectedWarmArguments $imcWarmExpectedArguments
";
 // A fixed Stage loader with public data anchors. Building/comparing this
 // action proves neither its execution nor its original Scheduler Run object.
 public string BuildWarmTaskAction(){lock(sync)try{
  Check();const string bootstrap=@"%USERPROFILE%\Desktop\IMC_TRADING_MAIN\TOOLS\GMGN-ReadSupervisor.ps1";
  if(!first.Contains(bootstrap))throw Error("GMGN_WARM_ACTION_SOURCE");
  var data=new SortedDictionary<string,string>(StringComparer.Ordinal);
  data.Add("schema_version","1");data.Add("purpose","IMC_WARM_TASK_ACTION_V1");data.Add("installation_id",installId);
  data.Add("install_plan",path);data.Add("install_plan_sha256",hash);data.Add("source1_manifest",firstManifest);
  data.Add("source1_sha256",firstHash);data.Add("source1_fingerprint",firstFingerprint);data.Add("auth_binding_sha256",authHash);
  var serializer=new JavaScriptSerializer{MaxJsonLength=32768,RecursionLimit=4};data.Add("auth_binding",serializer.Serialize(auth.ReadPublicBinding()));
  data.Add("recovery_directory",recovery.Path);data.Add("recovery_identity",recoveryId);
  data.Add("primary_plan",primaryPath);data.Add("primary_plan_sha256",primaryHash);data.Add("bootstrap",bootstrap);data.Add("bootstrap_sha256",first.HashForPath(bootstrap));
  string encodedData=Convert.ToBase64String(new UTF8Encoding(false,true).GetBytes(serializer.Serialize(data)));
  string body="$imcWarmActionData='"+encodedData+"'\n"+WarmLoader;
  string result=WarmArgumentsPrefix+Convert.ToBase64String(new UnicodeEncoding(false,false,true).GetBytes(body));
  if(result.Length>32767)throw Error("GMGN_WARM_ACTION_SIZE");Check();return result;
 }catch{MarkFault();throw;}}
 public void ValidateWarmTaskAction(string suppliedArguments){lock(sync)try{
  Check();if(suppliedArguments==null||suppliedArguments.Length>32767||!suppliedArguments.StartsWith(WarmArgumentsPrefix,StringComparison.Ordinal))throw Error("GMGN_WARM_ACTION_BINDING");
  string encoded=suppliedArguments.Substring(WarmArgumentsPrefix.Length),decoded;
  var unicode=new UnicodeEncoding(false,false,true);
  try{decoded=unicode.GetString(Convert.FromBase64String(encoded));if(Convert.ToBase64String(unicode.GetBytes(decoded))!=encoded)throw Error("GMGN_WARM_ACTION_ENCODING");}
  catch{throw Error("GMGN_WARM_ACTION_ENCODING");}
  if(suppliedArguments!=BuildWarmTaskAction())throw Error("GMGN_WARM_ACTION_BINDING");Check();
 }catch{MarkFault();throw;}}
 public void RequireWarmTaskAction(){lock(sync)ValidateWarmTaskAction(actionArguments);}
 // Native S2 source binding, not validation evidence or dispatch authority.
 public PrimaryBinding BindPrimary(GmgnImmutableSources source2){lock(sync)try{Check();return new PrimaryBinding(this,source2);}catch{MarkFault();throw;}}
 public sealed class PrimaryBinding {
  readonly object sync=new object();readonly GmgnReadInstallPlan plan;readonly GmgnImmutableSources sources;
  readonly string fingerprint;bool fault,complete;
  static readonly List<PrimaryBinding> retained=new List<PrimaryBinding>();
  internal PrimaryBinding(GmgnReadInstallPlan owner,GmgnImmutableSources source2){
   lock(retained)retained.Add(this);plan=owner;sources=source2;
   try{
    plan.Check();if(sources==null||sources.GetType().Assembly!=GetType().Assembly||sources.IsAdvisorDomain||
     Object.ReferenceEquals(sources,plan.first)||Object.ReferenceEquals(sources,plan.planPin)||
     !sources.Contains(plan.primaryPath)||sources.HashForPath(plan.primaryPath)!=plan.primaryHash)throw Error("GMGN_PRIMARY_PLAN_BINDING");
    var serializer=new JavaScriptSerializer{MaxJsonLength=2097152,RecursionLimit=4};
    string text=sources.ReadText(plan.primaryPath);Dictionary<string,object> document;
    try{document=serializer.Deserialize<Dictionary<string,object>>(text);}catch{throw Error("GMGN_PRIMARY_PLAN_JSON");}
    string[] fields={"schema_version","purpose","installation_id","node_executable","cli_entry","read_bootstrap","files"};
    if(document==null||document.Count!=fields.Length)throw Error("GMGN_PRIMARY_PLAN_FIELDS");
    var canonical=new SortedDictionary<string,object>(StringComparer.Ordinal);
    foreach(string name in fields){object value;if(!document.TryGetValue(name,out value)||value==null)throw Error("GMGN_PRIMARY_PLAN_FIELDS");
     if(name!="files"&&(!(value is string)||String.IsNullOrEmpty((string)value)))throw Error("GMGN_PRIMARY_PLAN_FIELDS");canonical.Add(name,value);}
    var files=document["files"] as Dictionary<string,object>;
    if(files==null||files.Count==0||files.Count>2047)throw Error("GMGN_PRIMARY_PLAN_INVENTORY");
    var ordered=new SortedDictionary<string,string>(StringComparer.Ordinal);
    var seen=new HashSet<string>(StringComparer.OrdinalIgnoreCase);
    foreach(var file in files){
     if(!(file.Value is string)||!seen.Add(file.Key)||file.Key==plan.primaryPath||
      !System.Text.RegularExpressions.Regex.IsMatch((string)file.Value,@"\A[0-9a-f]{64}\z"))throw Error("GMGN_PRIMARY_PLAN_INVENTORY");
     ordered.Add(file.Key,(string)file.Value);
    }
    canonical["files"]=ordered;string canonicalText=serializer.Serialize(canonical);
    if(text!=canonicalText||Digest(canonicalText)!=plan.primaryHash)throw Error("GMGN_PRIMARY_PLAN_NONCANONICAL");
    const string tools=@"%USERPROFILE%\Desktop\IMC_TRADING_MAIN\TOOLS\";
    const string sdk=@"%USERPROFILE%\AppData\Roaming\npm\node_modules\gmgn-cli\";
    const string node=@"C:\Program Files\nodejs\node.exe";
    if((string)document["schema_version"]!="1"||(string)document["purpose"]!="IMC_READ_PRIMARY_PLAN_V1"||
     (string)document["installation_id"]!=plan.installId||(string)document["node_executable"]!=node||
     (string)document["cli_entry"]!=sdk+@"dist\index.js"||(string)document["read_bootstrap"]!=tools+"GMGN-ReadCliBootstrap.mjs")throw Error("GMGN_PRIMARY_PLAN_CONTEXT");
    if(sources.Count!=ordered.Count+1)throw Error("GMGN_PRIMARY_PLAN_INVENTORY");
    foreach(var file in ordered){
     if(!sources.Contains(file.Key)||sources.HashForPath(file.Key)!=file.Value)throw Error("GMGN_PRIMARY_PLAN_INVENTORY");
     if(plan.first.Contains(file.Key)&&plan.first.HashForPath(file.Key)!=file.Value)throw Error("GMGN_PRIMARY_SHARED_SOURCE_CHANGED");
    }
    // Require the entire approved S1 SDK base, including transitive modules;
    // agreement between a truncated S2 manifest and its pins is insufficient.
    string[] firstPaths=serializer.Deserialize<string[]>(plan.first.PathsJson());int sdkCount=0;
    foreach(string p in firstPaths){
     bool required=p.StartsWith(sdk,StringComparison.Ordinal)||p==node||p==tools+"GMGN-ReadCliBootstrap.mjs"||
      p==tools+"GMGN-SignedQuoteProbe.mjs"||p==tools+"GMGN-SingleSwapTransport.mjs";
     if(required){sdkCount++;if(!ordered.ContainsKey(p)||ordered[p]!=plan.first.HashForPath(p))throw Error("GMGN_PRIMARY_SDK_INCOMPLETE");}
    }
    if(sdkCount!=169)throw Error("GMGN_PRIMARY_SDK_BASE");
    foreach(string name in new string[]{"GMGN-ReadBroker.ps1","GMGN-ReadProtocol.ps1","Invoke-GMGN-ReadBroker.ps1","GMGN-ReadClientBootstrap.ps1",
     "GmgnRepairNamespace.cs","GmgnReadDirectoryIdentity.cs","GmgnRepairProcessBinding.cs","GmgnReadClientIdentity.cs"})
     if(!ordered.ContainsKey(tools+name))throw Error("GMGN_PRIMARY_CONTRACT_INCOMPLETE");
    foreach(string name in new string[]{"imc-execution.json","imc-gmgn-read-broker.json"})
     if(!ordered.ContainsKey(@"%USERPROFILE%\Desktop\IMC_TRADING_MAIN\config\"+name))throw Error("GMGN_PRIMARY_CONTRACT_INCOMPLETE");
    fingerprint=sources.Fingerprint();complete=true;Check();
   }catch{fault=true;plan.Abort();throw;}
  }
  public void Check(){lock(sync)try{if(fault||!complete)throw Error("GMGN_PRIMARY_PLAN_FAULT");plan.Check();sources.Check();
   if(sources.HashForPath(plan.primaryPath)!=plan.primaryHash||sources.Fingerprint()!=fingerprint)throw Error("GMGN_PRIMARY_PLAN_CHANGED");
  }catch{fault=true;plan.Abort();throw;}}
  internal bool Matches(GmgnReadInstallPlan owner,GmgnImmutableSources source2){lock(sync){Check();return Object.ReferenceEquals(owner,plan)&&Object.ReferenceEquals(source2,sources);}}
  public string[] ReadBinding(){lock(sync){Check();return new string[]{"1",plan.installId,plan.hash,plan.primaryPath,plan.primaryHash,fingerprint};}}
  public string State{get{lock(sync)return complete&&!fault?"PRIMARY_BOUND":"FAULT";}}
  public void Abort(){lock(sync){fault=true;plan.Abort();}}
 }
}
