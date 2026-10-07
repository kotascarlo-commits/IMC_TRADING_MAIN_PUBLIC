// Retained original scheduler/ROOT-completion binding only. No send authority.
using System;using System.Reflection;using System.Runtime.InteropServices;
using System.Security.Principal;using System.Management.Automation;
public sealed partial class GmgnReadInstallPlan {
 bool rootTaskAttempted;RootTaskBinding rootTask;
 public RootTaskBinding BindOriginalRootTask(GmgnHandoffCompletion completion,
  GmgnScheduledActionIdentity originalChild,object originalTask,object originalRun,
  string originalInstanceGuid,int originalEnginePid,string taskName){try{
  lock(sync)try{
   if(rootTaskAttempted)throw Error("GMGN_ROOT_TASK_USED");rootTaskAttempted=true;Check();
  }catch{MarkFault();throw;}
  // Never enter RootTask -> Plan checks while holding Plan. The pending
  // object retains itself before validation and is published only once.
  var pending=new RootTaskBinding(this,completion,originalChild,originalTask,originalRun,originalInstanceGuid,originalEnginePid,taskName);
  lock(sync){Check();if(rootTask!=null)throw Error("GMGN_ROOT_TASK_USED");rootTask=pending;return rootTask;}
 }catch{Abort();throw;}}
 public sealed class RootTaskBinding {
  readonly object sync=new object();readonly GmgnReadInstallPlan plan;
  readonly GmgnHandoffCompletion completion;readonly GmgnScheduledActionIdentity child,strictChild;
  readonly object task,run;readonly string guid,taskPath,arguments,sid,helper;
  readonly int engine;readonly string[] binding;bool fault,complete,senderClaimed;
  static readonly System.Collections.Generic.List<RootTaskBinding> retained=new System.Collections.Generic.List<RootTaskBinding>();
  static object Property(object value,string name){return value.GetType().InvokeMember(name,BindingFlags.GetProperty,null,value,null);}
  static void Refresh(object value){value.GetType().InvokeMember("Refresh",BindingFlags.InvokeMethod,null,value,null);}
  internal RootTaskBinding(GmgnReadInstallPlan owner,GmgnHandoffCompletion completed,
   GmgnScheduledActionIdentity originalChild,object originalTask,object originalRun,string originalGuid,int originalEngine,string name){
   plan=owner;completion=completed;child=originalChild;task=originalTask;run=originalRun;guid=originalGuid;engine=originalEngine;
   lock(retained)retained.Add(this);
   try{
    if(completion==null||child==null||completion.GetType().Assembly!=GetType().Assembly||child.GetType().Assembly!=GetType().Assembly||
     task==null||run==null||!Marshal.IsComObject(task)||!Marshal.IsComObject(run)||engine<=0||
     !System.Text.RegularExpressions.Regex.IsMatch(guid??"",@"\A\{[0-9A-Fa-f-]{36}\}\z")||
     !(name=="IMC-ReadSupervisor"||System.Text.RegularExpressions.Regex.IsMatch(name??"",@"\AIMC-READ-FIXTURE-[0-9a-f]{32}\z")))throw Error("GMGN_ROOT_TASK_INPUT");
    string code=@"%USERPROFILE%\Desktop\IMC_TRADING_MAIN\TOOLS\GmgnReadInstallPlan.Root.cs";
    const string script=@"%USERPROFILE%\Desktop\IMC_TRADING_MAIN\TOOLS\GMGN-SupervisorTask.ps1";
    if(!plan.first.Contains(code)||!plan.first.Contains(script))throw Error("GMGN_ROOT_TASK_SOURCE");
    helper=plan.first.ReadText(script);sid=WindowsIdentity.GetCurrent().User.Value;taskPath="\\"+name;
    arguments=plan.BuildWarmTaskAction();
    // Supplement the original actor while its held process is still healthy.
    // Never replace it or rebind after failure. Its live handle prevents reuse
    // throughout this additional native open of exactly the same Native4.
    string[] native=child.ReadNativeBinding();
    if(native[3]!=@"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")throw Error("GMGN_ROOT_TASK_CHILD_EXECUTABLE");
    strictChild=GmgnScheduledActionIdentity.BindExpectedPeerAction(native,sid,plan.recovery,plan.recoveryId,arguments);
    string[] after=child.ReadNativeBinding(),strict=strictChild.ReadNativeBinding();
    for(int i=0;i<4;i++)if(native[i]!=after[i]||native[i]!=strict[i])throw Error("GMGN_ROOT_TASK_CHILD_CHANGED");
    binding=completion.ClaimRootTask(plan.first,plan.auth,child,plan.self);
    if(binding[1]!=plan.firstHash||binding[10]!=engine.ToString())throw Error("GMGN_ROOT_TASK_BINDING");
    // Canonical S1 binds this Root and its exact arguments before Child start.
    var manifest=plan.ReadWarmManifest();object raw,challenge;
    if(!manifest.TryGetValue("warm_handoff",out raw)||!(raw is string))throw Error("GMGN_ROOT_TASK_CONTRACT");
    var parser=new System.Web.Script.Serialization.JavaScriptSerializer{MaxJsonLength=32768,RecursionLimit=4};
    var fields=parser.Deserialize<System.Collections.Generic.Dictionary<string,string>>((string)raw);
    if(fields==null||fields.Count!=5)throw Error("GMGN_ROOT_TASK_CONTRACT");
    var ordered=new System.Collections.Generic.SortedDictionary<string,string>(StringComparer.Ordinal);
    foreach(string key in new string[]{"owner_sid","pipe_name","root_arguments","root_native","session_id"}){
     string value;if(!fields.TryGetValue(key,out value)||String.IsNullOrEmpty(value))throw Error("GMGN_ROOT_TASK_CONTRACT");ordered.Add(key,value);
    }
    if(!manifest.TryGetValue("warm_action_challenge",out challenge)||!(challenge is string)||(string)challenge!=binding[3]||
     parser.Serialize(ordered)!=(string)raw||fields["owner_sid"]!=sid||fields["root_arguments"]!=plan.actionArguments||
     fields["root_native"]!=parser.Serialize(plan.self)||fields["session_id"]!=binding[2])throw Error("GMGN_ROOT_TASK_CONTRACT");
    complete=true;Check();
   }catch{fault=true;throw;}
  }
  void CheckOriginalRun(){
   string actualGuid,actualPath,actualTaskPath;int actualEngine;
   try{Refresh(run);actualGuid=(string)Property(run,"InstanceGuid");actualEngine=Convert.ToInt32(Property(run,"EnginePID"));
    actualPath=(string)Property(run,"Path");actualTaskPath=(string)Property(task,"Path");}
   catch{throw Error("GMGN_ROOT_TASK_INSTANCE_QUERY");}
   if(actualGuid!=guid||actualEngine!=engine||actualPath!=taskPath||actualTaskPath!=taskPath)throw Error("GMGN_ROOT_TASK_INSTANCE_CHANGED");
  }
  void CheckDefinition(){
   const string invoke=@"param($text,$task,$sid,$arguments,$expectedPath)
$ErrorActionPreference='Stop';$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseInput($text,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'GMGN_ROOT_TASK_HELPER_PARSE'}
. ($ast.GetScriptBlock())
try{$originalDefinition=$task.Definition}catch{throw 'GMGN_ROOT_TASK_ORIGINAL_DEFINITION_QUERY'}
Assert-GmgnSupervisorTaskDefinition $originalDefinition $sid 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' $arguments '%USERPROFILE%\Desktop\IMC_TRADING_MAIN'
try{$service=New-Object -ComObject Schedule.Service;$service.Connect()}catch{throw 'GMGN_ROOT_TASK_SCHEDULER_CONNECT'}
try{$registryTask=$service.GetFolder('\').GetTask($expectedPath)}catch{throw 'GMGN_ROOT_TASK_REGISTRY_QUERY'}
try{$registryPath=$registryTask.Path;$registryDefinition=$registryTask.Definition}catch{throw 'GMGN_ROOT_TASK_REGISTRY_DEFINITION_QUERY'}
if($registryPath -cne $expectedPath){throw 'GMGN_ROOT_TASK_REGISTRY_PATH'}
Assert-GmgnSupervisorTaskDefinition $registryDefinition $sid 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' $arguments '%USERPROFILE%\Desktop\IMC_TRADING_MAIN'
'GMGN_ROOT_TASK_DEFINITION_VERIFIED'";
   try{using(PowerShell ps=PowerShell.Create()){
    ps.AddScript(invoke).AddArgument(helper).AddArgument(task).AddArgument(sid).AddArgument(arguments).AddArgument(taskPath);
    var result=ps.Invoke();if(ps.HadErrors||result.Count!=1||!(result[0].BaseObject is string)||
     (string)result[0].BaseObject!="GMGN_ROOT_TASK_DEFINITION_VERIFIED")throw Error("GMGN_ROOT_TASK_DEFINITION");
   }}catch(Exception error){throw NormalizeDefinitionFailure(error);}
  }
  // Exact known codes only. Never retain a raw message, inner exception,
  // ErrorRecord or caller-controlled suffix in a public diagnostic.
  internal static InvalidOperationException NormalizeDefinitionFailure(Exception error){
   string[] allowed={"GMGN_TASK_ACTION","GMGN_TASK_ALREADY_RUNNING","GMGN_TASK_ARGUMENT_SUBSTITUTION","GMGN_TASK_CONTRACT_INPUT",
    "GMGN_TASK_DELETE_SETTINGS","GMGN_TASK_EXISTS_OR_UNREADABLE","GMGN_TASK_IDLE_SETTINGS","GMGN_TASK_NAME",
    "GMGN_TASK_NETWORK_SETTINGS","GMGN_TASK_PRINCIPAL_OR_COUNT","GMGN_TASK_SCHEMA","GMGN_TASK_SETTINGS","GMGN_TASK_TRIGGER","GMGN_TASK_XML_FEATURE",
    "GMGN_ROOT_TASK_HELPER_PARSE","GMGN_ROOT_TASK_ORIGINAL_DEFINITION_QUERY","GMGN_ROOT_TASK_SCHEDULER_CONNECT",
    "GMGN_ROOT_TASK_REGISTRY_QUERY","GMGN_ROOT_TASK_REGISTRY_DEFINITION_QUERY","GMGN_ROOT_TASK_REGISTRY_PATH","GMGN_ROOT_TASK_DEFINITION"};
   for(int depth=0;error!=null&&depth<8;depth++,error=error.InnerException)
    foreach(string code in allowed)if(error.Message==code)return Error(code);
   return Error("GMGN_ROOT_TASK_DEFINITION_OPERATION");
  }
  public void Check(){lock(sync)try{
   if(fault||!complete)throw Error("GMGN_ROOT_TASK_FAULT");
   plan.Check();completion.CheckRootTask(plan.first,plan.auth,child,plan.self);CheckStrictChild();
   CheckOriginalRun();CheckDefinition();CheckOriginalRun();
   CheckStrictChild();completion.CheckRootTask(plan.first,plan.auth,child,plan.self);plan.Check();
  }catch{fault=true;plan.Abort();throw;}}
  public string[] ReadBinding(){lock(sync){Check();var result=(string[])binding.Clone();Check();return result;}}
  internal string[] ClaimRetirementSender(GmgnReadInstallPlan owner){lock(sync)try{
   if(senderClaimed)throw Error("GMGN_ROOT_SENDER_CLAIM_USED");senderClaimed=true;
   if(!Object.ReferenceEquals(owner,plan))throw Error("GMGN_ROOT_SENDER_CONTEXT");Check();return (string[])binding.Clone();
  }catch{fault=true;plan.Abort();throw;}}
  void CheckStrictChild(){
   string[] original=child.ReadNativeBinding(),strict=strictChild.ReadNativeBinding();
   for(int i=0;i<4;i++)if(original[i]!=binding[i+10]||strict[i]!=original[i])throw Error("GMGN_ROOT_TASK_CHILD_CHANGED");
   child.Check();
  }
  public string State{get{lock(sync)return complete&&!fault?"ROOT_TASK_BOUND":"FAULT";}}
  public void Abort(){lock(sync){fault=true;plan.Abort();}}
 }
}
