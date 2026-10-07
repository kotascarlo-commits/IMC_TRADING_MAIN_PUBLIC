// Submission only. SENT is not a retirement acknowledgement. Unknown sends
// retain every object and are never retried or rolled back.
using System;using System.Collections.Generic;using System.IO;using System.Text;
using System.Web.Script.Serialization;
public sealed partial class GmgnReadInstallPlan {
 bool rootSenderAttempted;RootRetirementSender rootSender;
 public RootRetirementSender CreateRootRetirementSender(){RootTaskBinding original;try{
  lock(sync)try{if(rootSenderAttempted)throw Error("GMGN_ROOT_SENDER_USED");rootSenderAttempted=true;Check();original=rootTask;
   if(original==null)throw Error("GMGN_ROOT_SENDER_CONTEXT");}catch{MarkFault();throw;}
  // RootTask -> Plan is the only allowed direction. Claim outside Plan.
  string[] origin=original.ClaimRetirementSender(this);
  lock(sync){Check();if(!Object.ReferenceEquals(original,rootTask)||rootSender!=null)throw Error("GMGN_ROOT_SENDER_CONTEXT");
   rootSender=new RootRetirementSender(this,original,origin);return rootSender;}
 }catch(Exception error){Abort();throw RootRetirementSender.NormalizeFailure(error);}}
 public sealed class RootRetirementSender {
  readonly object sync=new object();readonly GmgnReadInstallPlan plan;readonly RootTaskBinding task;
  readonly string[] origin;GmgnLegacyLifetimePins lifetime;GmgnReadOwnerProof proof;GmgnReadProofPin request;
  readonly string fixture;string state="BINDING";bool attempted;
  sealed class PublicationGrant{internal bool Consumed;}PublicationGrant publicationGrant;
  static readonly List<RootRetirementSender> retained=new List<RootRetirementSender>();
  internal RootRetirementSender(GmgnReadInstallPlan owner,RootTaskBinding original,string[] binding){
   plan=owner;task=original;origin=(string[])binding.Clone();lock(retained)retained.Add(this);
   try{
    // This constructor is entered under Plan; never call RootTask/Proof here.
    plan.Check();if(origin.Length!=14||!plan.first.Contains(@"%USERPROFILE%\Desktop\IMC_TRADING_MAIN\TOOLS\GmgnReadInstallPlan.Sender.cs"))throw Error("GMGN_ROOT_SENDER_SOURCE");
    for(int i=0;i<4;i++)if(origin[6+i]!=plan.self[i])throw Error("GMGN_ROOT_SENDER_ORIGIN");
    var parser=new JavaScriptSerializer{MaxJsonLength=32768,RecursionLimit=4};var manifest=plan.ReadWarmManifest();
    string[] broker={"14552","3904","2026-10-02T15:14:56.6594833Z",@"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"};
    string[] parent={"3904","4176","2026-10-02T15:14:56.3325126Z",broker[3]};object raw;
    if(manifest.TryGetValue("warm_legacy_fixture",out raw)){
     if(!(raw is string))throw Error("GMGN_ROOT_SENDER_FIXTURE");var values=parser.Deserialize<Dictionary<string,object>>((string)raw);
     if(values==null||values.Count!=4)throw Error("GMGN_ROOT_SENDER_FIXTURE");var ordered=new SortedDictionary<string,string>(StringComparer.Ordinal);
     foreach(string key in new string[]{"broker_native","parent_native","scope","source_directory"}){object value;if(!values.TryGetValue(key,out value)||!(value is string))throw Error("GMGN_ROOT_SENDER_FIXTURE");ordered.Add(key,(string)value);}
     if(parser.Serialize(ordered)!=(string)raw)throw Error("GMGN_ROOT_SENDER_FIXTURE");fixture=ordered["scope"];
     if(!System.Text.RegularExpressions.Regex.IsMatch(fixture,@"\AC:\\Users\\<local-user>\\Desktop\\IMC_TRADING_MAIN\\[.]local-tools\\legacy-capture-native-[0-9a-f]{32}\z")||ordered["source_directory"]!=Path.Combine(fixture,"legacy"))throw Error("GMGN_ROOT_SENDER_FIXTURE");
     broker=parser.Deserialize<string[]>(ordered["broker_native"]);parent=parser.Deserialize<string[]>(ordered["parent_native"]);
     if(broker==null||parent==null||broker.Length!=4||parent.Length!=4||parser.Serialize(broker)!=ordered["broker_native"]||parser.Serialize(parent)!=ordered["parent_native"])throw Error("GMGN_ROOT_SENDER_FIXTURE");
    }
    int bp,pp;if(!Int32.TryParse(broker[0],out bp)||!Int32.TryParse(parent[0],out pp)||broker[1]!=parent[0]||broker[3]!=parent[3]||broker[3]!=@"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")throw Error("GMGN_ROOT_SENDER_LEGACY");
    lifetime=new GmgnLegacyLifetimePins(bp,broker[2],pp,parent[2],broker[3]);string[][] actual=lifetime.Bindings();
    for(int r=0;r<2;r++)for(int i=0;i<4;i++)if(actual[r][i]!=(r==0?broker[i]:parent[i]))throw Error("GMGN_ROOT_SENDER_LEGACY");
    plan.Check();state="BOUND";
   }catch(Exception error){state="FAULT";plan.Abort();throw NormalizeFailure(error);}
  }
  public void Send(string ownerProofPath,string externalOwnerProofSha){lock(sync){
   if(attempted||state!="BOUND")throw Error("GMGN_ROOT_SENDER_USED");attempted=true;state="PREPARING";
   try{
    // Runspace validators execute under Sender only, with no Plan or leaf
    // monitor held. Native owner handles remain original throughout.
    task.Check();plan.Check();lifetime.CheckAlive();string[][] legacy=lifetime.Bindings();
    proof=new GmgnReadOwnerProof(plan.first,plan.auth,ownerProofPath,externalOwnerProofSha,legacy,fixture);string[] owner=proof.ReadBinding();
    if(!proof.Matches(plan.first,plan.auth))throw Error("GMGN_ROOT_SENDER_PROOF");
    var parser=new JavaScriptSerializer{MaxJsonLength=32768,RecursionLimit=4};var fields=new SortedDictionary<string,string>(StringComparer.Ordinal);
    fields.Add("schema_version","1");fields.Add("purpose","IMC_WARM_RETIRE_V1");fields.Add("command_id",Guid.NewGuid().ToString("N"));
    fields.Add("installation_id",plan.installId);fields.Add("generation_sha256",plan.firstHash);fields.Add("source_fingerprint",plan.firstFingerprint);
    fields.Add("auth_binding_sha256",plan.authHash);fields.Add("directory",plan.recovery.Path);fields.Add("directory_identity",plan.recoveryId);
    fields.Add("origin",parser.Serialize(origin));fields.Add("supervisor",parser.Serialize(CopyChild()));fields.Add("legacy_bindings",parser.Serialize(legacy));
    fields.Add("owner_proof_path",owner[0]);fields.Add("owner_proof_sha256",owner[2]);fields.Add("owner_observed_qpc",owner[3]);fields.Add("owner_qpc_frequency",owner[4]);
    string body=parser.Serialize(fields),mac=plan.auth.Sign(body);var envelope=new SortedDictionary<string,string>(StringComparer.Ordinal);envelope.Add("body",body);envelope.Add("mac",mac);
    byte[] bytes=new UTF8Encoding(false,true).GetBytes(parser.Serialize(envelope));
    task.Check();plan.Check();lifetime.CheckAlive();proof.Check();
    // CREATE_NEW becomes visible before all readback checks finish. Every
    // subsequent error means effect MAY have occurred, never permission retry.
    publicationGrant=new PublicationGrant();var grant=publicationGrant;state="SENDING";
    request=GmgnReadProofPin.PublishRetirementRequest(plan.recovery,bytes,delegate{return AdmitOwnPublicationProbe(plan.recovery,grant);});request.Check();
    plan.Check();task.Check();request.Check();state="SENT";
   }catch(Exception error){state=state=="SENDING"?"SEND_UNKNOWN":"FAULT";plan.Abort();throw NormalizeFailure(error);}
  }}
  string[] CopyChild(){var value=new string[4];System.Array.Copy(origin,10,value,0,4);return value;}
  internal bool AdmitOwnPublicationProbe(GmgnReadDirectoryIdentity directory,object grant){lock(sync){
   if(state!="SENDING"||!attempted||publicationGrant==null||publicationGrant.Consumed||!Object.ReferenceEquals(grant,publicationGrant)||!Object.ReferenceEquals(directory,plan.recovery)||
    !System.Text.RegularExpressions.Regex.IsMatch(fixture??"",@"\AC:\\Users\\<local-user>\\Desktop\\IMC_TRADING_MAIN\\[.]local-tools\\legacy-capture-native-[0-9a-f]{32}\z"))return false;
   publicationGrant.Consumed=true;plan.Check();return true;
  }}
  internal static InvalidOperationException NormalizeFailure(Exception error){
   string[] codes={"GMGN_ROOT_SENDER_USED","GMGN_ROOT_SENDER_CLAIM_USED","GMGN_ROOT_SENDER_CONTEXT","GMGN_ROOT_SENDER_SOURCE","GMGN_ROOT_SENDER_ORIGIN","GMGN_ROOT_SENDER_FIXTURE","GMGN_ROOT_SENDER_LEGACY","GMGN_ROOT_SENDER_PROOF","GMGN_ROOT_TASK_FAULT","GMGN_ROOT_TASK_INSTANCE_CHANGED","GMGN_ROOT_TASK_INSTANCE_QUERY","GMGN_ROOT_TASK_CHILD_CHANGED","GMGN_TASK_ACTION","GMGN_ROOT_TASK_DEFINITION_OPERATION","GMGN_INSTALL_PLAN_FAULT","GMGN_SOURCE_LOCKED","GMGN_AUTH_FAULT","GMGN_LEGACY_PIN_FAULT","GMGN_LEGACY_PIN_BINDING","GMGN_STAGE_OWNER_PROOF_STALE","GMGN_STAGE_OWNER_PROOF_BINDING","GMGN_STAGE_OWNER_LOADED_SOURCE","GMGN_OWNER_PIN_AUTH","GMGN_OWNER_PIN_INPUT","GMGN_OWNER_PIN_HASH","GMGN_OWNER_PIN_ENVELOPE","GMGN_OWNER_PIN_FAULT","GMGN_PROOF_OPEN","GMGN_PROOF_WRITE_FLUSH","GMGN_PROOF_FAULT","GMGN_PROOF_ACL","GMGN_PROOF_READBACK","GMGN_PROOF_CLOSE_UNCONFIRMED"};
   for(int depth=0;error!=null&&depth<8;depth++,error=error.InnerException)foreach(string code in codes)if(error.Message==code)return Error(code);
   return Error("GMGN_ROOT_SENDER_OPERATION");
  }
  public string State{get{lock(sync)return state;}}
 }
}
