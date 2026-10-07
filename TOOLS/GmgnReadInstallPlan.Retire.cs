// Explicit Warm request admission. Production Warm remains WAIT until its
// complete installation chain has independent validation.
using System;using System.Collections.Generic;using System.Globalization;
using System.Web.Script.Serialization;
public sealed partial class GmgnReadInstallPlan {
 public sealed partial class WarmValidationAdmission {
  readonly object retirementSync=new object();bool retirementAttempted;
  string retirementState="LEGACY_CAPTURED";WarmRetirementCommand retirementCommand;
  sealed class WarmRetirementCommand {
   readonly WarmValidationAdmission owner;readonly GmgnReadOwnerProof request,proof;
   readonly long observed,frequency;
   static readonly List<WarmRetirementCommand> retained=new List<WarmRetirementCommand>();
   internal WarmRetirementCommand(WarmValidationAdmission admission,string externalRequestSha){
    owner=admission;lock(retained)retained.Add(this);
    owner.CheckCapturedContext();var plan=owner.plan;
    const string code=@"%USERPROFILE%\Desktop\IMC_TRADING_MAIN\TOOLS\GmgnReadInstallPlan.Retire.cs";
    if(!plan.first.Contains(code))throw Error("GMGN_WARM_RETIRE_SOURCE");
    request=GmgnReadOwnerProof.OpenRetirementRequest(plan.first,plan.auth,plan.recovery,externalRequestSha);
    if(!request.MatchesRequest(plan.first,plan.auth,plan.recovery))throw Error("GMGN_WARM_RETIRE_REQUEST");
    var parser=new JavaScriptSerializer{MaxJsonLength=32768,RecursionLimit=6};
    string body=request.ReadRequestBody();var fields=parser.Deserialize<Dictionary<string,object>>(body);
    string[] names={"schema_version","purpose","command_id","installation_id","generation_sha256","source_fingerprint",
     "auth_binding_sha256","directory","directory_identity","origin","supervisor","legacy_bindings",
     "owner_proof_path","owner_proof_sha256","owner_observed_qpc","owner_qpc_frequency"};
    if(fields==null||fields.Count!=names.Length)throw Error("GMGN_WARM_RETIRE_FIELDS");
    var canonical=new SortedDictionary<string,string>(StringComparer.Ordinal);
    foreach(string name in names){object value;if(!fields.TryGetValue(name,out value)||!(value is string)||String.IsNullOrEmpty((string)value))throw Error("GMGN_WARM_RETIRE_FIELDS");canonical.Add(name,(string)value);}
    if(parser.Serialize(canonical)!=body)throw Error("GMGN_WARM_RETIRE_CANONICAL");
    Guid id;if(canonical["schema_version"]!="1"||canonical["purpose"]!="IMC_WARM_RETIRE_V1"||
     !Guid.TryParseExact(canonical["command_id"],"N",out id)||id.ToString("N")!=canonical["command_id"]||
     canonical["installation_id"]!=plan.installId||canonical["generation_sha256"]!=plan.firstHash||
     canonical["source_fingerprint"]!=plan.firstFingerprint||canonical["auth_binding_sha256"]!=plan.authHash||
     canonical["directory"]!=plan.recovery.Path||canonical["directory_identity"]!=plan.recoveryId)throw Error("GMGN_WARM_RETIRE_CONTEXT");
    // Root emits Root/Child; WarmOrigin retains Child/Root. Both are the
    // original fourteen fields, never an invented replacement receipt.
    owner.origin.CheckCheckpoint(plan.first,plan.auth,owner.admission);
    string[] origin=ReadStrings(parser,canonical["origin"],14),warm=(string[])owner.originBinding.Clone();
    for(int i=0;i<6;i++)if(origin[i]!=warm[i])throw Error("GMGN_WARM_RETIRE_ORIGIN");
    for(int i=0;i<4;i++)if(origin[6+i]!=warm[10+i]||origin[10+i]!=warm[6+i])throw Error("GMGN_WARM_RETIRE_ORIGIN");
    string[] supervisor=ReadStrings(parser,canonical["supervisor"],4);
    for(int i=0;i<4;i++)if(supervisor[i]!=owner.self[i])throw Error("GMGN_WARM_RETIRE_SELF");
    string[][] legacy=parser.Deserialize<string[][]>(canonical["legacy_bindings"]),actual=owner.lifetime.Bindings();
    if(legacy==null||legacy.Length!=2||parser.Serialize(legacy)!=canonical["legacy_bindings"])throw Error("GMGN_WARM_RETIRE_LEGACY");
    for(int r=0;r<2;r++){if(legacy[r]==null||legacy[r].Length!=4)throw Error("GMGN_WARM_RETIRE_LEGACY");for(int i=0;i<4;i++)if(legacy[r][i]!=actual[r][i])throw Error("GMGN_WARM_RETIRE_LEGACY");}
    observed=ReadClock(canonical["owner_observed_qpc"]);frequency=ReadClock(canonical["owner_qpc_frequency"]);
    // Fresh validator runs outside Plan and every Source/Auth/Capture/Life
    // monitor. Its immutable original clock is carried to the native effect.
    proof=new GmgnReadOwnerProof(plan.first,plan.auth,canonical["owner_proof_path"],canonical["owner_proof_sha256"],actual,owner.fixture);
    if(!proof.MatchesValidatedClock(plan.first,plan.auth,observed,frequency))throw Error("GMGN_WARM_RETIRE_CLOCK");
   }
   static string[] ReadStrings(JavaScriptSerializer parser,string text,int count){
    var values=parser.Deserialize<string[]>(text);if(values==null||values.Length!=count||parser.Serialize(values)!=text)throw Error("GMGN_WARM_RETIRE_ARRAY");
    foreach(string value in values)if(String.IsNullOrEmpty(value))throw Error("GMGN_WARM_RETIRE_ARRAY");return values;
   }
   static long ReadClock(string text){long value;if(!Int64.TryParse(text,NumberStyles.None,CultureInfo.InvariantCulture,out value)||value<=0||value.ToString(CultureInfo.InvariantCulture)!=text)throw Error("GMGN_WARM_RETIRE_CLOCK");return value;}
   internal void CheckBeforeEffect(){owner.CheckCapturedContext();request.Check();proof.Check();owner.plan.Check();}
   internal void Retire(){CheckBeforeEffect();owner.retirement.RetireWithBoundary(observed,frequency,owner.plan.InRetirementEffect,owner.plan.CheckRetirementCancellation);}
   internal void CheckRetainedRequest(){if(!request.MatchesRequest(owner.plan.first,owner.plan.auth,owner.plan.recovery))throw Error("GMGN_WARM_RETIRE_REQUEST");request.Check();}
  }
  public void RetireFromRequest(string externalRequestSha){
   lock(retirementSync){if(retirementAttempted||retirementState!="LEGACY_CAPTURED")throw Error("GMGN_WARM_RETIRE_USED");retirementAttempted=true;retirementState="VALIDATING";}
   try{
    var command=new WarmRetirementCommand(this,externalRequestSha);
    lock(retirementSync){if(fault||retirementState!="VALIDATING")throw Error("GMGN_WARM_RETIRE_FAULT");retirementCommand=command;retirementState="RETIRE_REQUESTED";}
    command.Retire();
    lock(retirementSync){if(fault||retirementState!="RETIRE_REQUESTED")throw Error("GMGN_WARM_RETIRE_FAULT");retirementState="WAIT_PARENT";}
    CheckRetiredContext();
   }catch(Exception error){Fault();throw NormalizeRetirementFailure(error);}
  }
  void CheckRetiredContext(){
   CheckOrigin();capture.CheckCommitted();captureDirectory.Check();CheckProtectedAcl(recoverySecurity);CheckProtectedAcl(captureSecurity);
   if(!capture.MatchesOwner(lifetime)||!retirement.MatchesContext(plan.first,lifetime,capture)||retirement.Phase!="RETIRED"||!lifetime.Retired)throw Error("GMGN_WARM_RETIRE_CONTEXT");
   WarmRetirementCommand command;lock(retirementSync){if(fault||retirementCommand==null)throw Error("GMGN_WARM_RETIRE_FAULT");command=retirementCommand;}
   command.CheckRetainedRequest();CheckOrigin();
  }
  public bool[] ObserveRetiredOriginalExits(){try{
   lock(retirementSync){if(fault||(retirementState!="WAIT_PARENT"&&retirementState!="BOTH_EXITED"))throw Error("GMGN_WARM_RETIRE_PHASE");}
   CheckRetiredContext();bool[] exits=lifetime.ObserveOriginalExits();
   lock(retirementSync){if(fault)throw Error("GMGN_WARM_RETIRE_FAULT");if(exits[0]&&exits[1])retirementState="BOTH_EXITED";}
   return exits;
  }catch(Exception error){Fault();throw NormalizeRetirementFailure(error);}}
  internal static InvalidOperationException NormalizeRetirementFailure(Exception error){
   string[] codes={"GMGN_WARM_RETIRE_USED","GMGN_WARM_RETIRE_FAULT","GMGN_WARM_RETIRE_PHASE","GMGN_WARM_RETIRE_SOURCE","GMGN_WARM_RETIRE_REQUEST","GMGN_WARM_RETIRE_FIELDS","GMGN_WARM_RETIRE_CANONICAL","GMGN_WARM_RETIRE_CONTEXT","GMGN_WARM_RETIRE_ORIGIN","GMGN_WARM_RETIRE_SELF","GMGN_WARM_RETIRE_LEGACY","GMGN_WARM_RETIRE_ARRAY","GMGN_WARM_RETIRE_CLOCK","GMGN_INSTALL_PLAN_FAULT","GMGN_INSTALL_RETIRE_CANCELLED","GMGN_WARM_LEGACY_FAULT","GMGN_CAPTURE_NOT_COMMITTED","GMGN_LEGACY_OWNER_PROOF_EXPIRED","GMGN_LEGACY_RETIRE_EXIT_UNCONFIRMED","GMGN_LEGACY_RETIRE_TERMINATE","GMGN_LEGACY_PIN_FAULT","GMGN_OWNER_PIN_AUTH","GMGN_OWNER_PIN_HASH","GMGN_OWNER_PIN_FAULT","GMGN_STAGE_OWNER_PROOF_STALE"};
   for(int depth=0;error!=null&&depth<8;depth++,error=error.InnerException)foreach(string code in codes)if(error.Message==code)return Error(code);
   return Error("GMGN_WARM_RETIRE_OPERATION");
  }
 }
}
