// Source continuity only. No SDK, legacy retirement, installation or READY grant.
using System;using System.Collections.Generic;using System.Security.Principal;
using System.Web.Script.Serialization;
public sealed partial class GmgnReadInstallPlan {
 bool handoffAttempted;GmgnSourcePinBinding warmPins;GmgnSupervisorPipe warmPipe;
 GmgnScheduledActionIdentity warmRoot;GmgnPinTransferHandshake warmTransfer;
 GmgnHandoffCompletion warmCompletion;GmgnSupervisorSourceAdmission warmAdmission;
 GmgnHandoffCompletion.WarmOrigin warmOrigin;
 Dictionary<string,object> ReadWarmManifest(){
  Check();var parser=new JavaScriptSerializer{MaxJsonLength=2097152,RecursionLimit=12};
  try{return parser.Deserialize<Dictionary<string,object>>(first.ReadText(firstManifest));}
  catch{throw Error("GMGN_WARM_HANDOFF_MANIFEST");}
 }
 public bool HasWarmSourceHandoff(){lock(sync)try{
  var manifest=ReadWarmManifest();if(manifest==null)throw Error("GMGN_WARM_HANDOFF_MANIFEST");
  bool present=manifest.ContainsKey("warm_handoff");Check();return present;
 }catch{MarkFault();throw;}}
 public GmgnHandoffCompletion.WarmOrigin CompleteWarmSourceHandoff(){lock(sync)try{
  if(handoffAttempted)throw Error("GMGN_WARM_HANDOFF_USED");handoffAttempted=true;
  RequireWarmTaskAction();Check();
  if(!first.Contains(@"%USERPROFILE%\Desktop\IMC_TRADING_MAIN\TOOLS\GmgnReadInstallPlan.Warm.cs"))throw Error("GMGN_WARM_HANDOFF_SOURCE");
  var manifest=ReadWarmManifest();object contract,challengeValue;
  if(manifest==null||!manifest.TryGetValue("warm_handoff",out contract)||!(contract is string)||
   !manifest.TryGetValue("warm_action_challenge",out challengeValue)||!(challengeValue is string))throw Error("GMGN_WARM_HANDOFF_MANIFEST");
  string text=(string)contract,challenge=(string)challengeValue;
  if(!System.Text.RegularExpressions.Regex.IsMatch(challenge,@"\A[0-9a-f]{64}\z")||text.Length>32768)throw Error("GMGN_WARM_HANDOFF_MANIFEST");
  var parser=new JavaScriptSerializer{MaxJsonLength=32768,RecursionLimit=4};Dictionary<string,string> values;
  try{values=parser.Deserialize<Dictionary<string,string>>(text);}catch{throw Error("GMGN_WARM_HANDOFF_FIELDS");}
  string[] fields={"owner_sid","pipe_name","root_arguments","root_native","session_id"};
  if(values==null||values.Count!=fields.Length)throw Error("GMGN_WARM_HANDOFF_FIELDS");
  var canonical=new SortedDictionary<string,string>(StringComparer.Ordinal);
  foreach(string field in fields){string value;if(!values.TryGetValue(field,out value)||String.IsNullOrEmpty(value))throw Error("GMGN_WARM_HANDOFF_FIELDS");canonical.Add(field,value);}
  if(parser.Serialize(canonical)!=text)throw Error("GMGN_WARM_HANDOFF_NONCANONICAL");
  if(values["owner_sid"]!=WindowsIdentity.GetCurrent().User.Value||
   !System.Text.RegularExpressions.Regex.IsMatch(values["pipe_name"],@"\Aimc-read-bootstrap-[0-9a-f]{32}\z")||
   !System.Text.RegularExpressions.Regex.IsMatch(values["session_id"],@"\A[0-9a-f]{32}\z"))throw Error("GMGN_WARM_HANDOFF_CONTEXT");
  string[] rootBinding;try{rootBinding=parser.Deserialize<string[]>(values["root_native"]);}catch{throw Error("GMGN_WARM_HANDOFF_ROOT");}
  if(rootBinding==null||rootBinding.Length!=4||parser.Serialize(rootBinding)!=values["root_native"]||
   rootBinding[3]!=@"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")throw Error("GMGN_WARM_HANDOFF_ROOT");
  warmPins=new GmgnSourcePinBinding(first,MarkFault);
  warmRoot=GmgnScheduledActionIdentity.BindExpectedPeerAction(rootBinding,values["owner_sid"],recovery,recoveryId,values["root_arguments"]);
  warmPipe=new GmgnSupervisorPipe(values["pipe_name"],values["owner_sid"],false,30000);
  Check();warmRoot.CheckPeerPid(warmPipe.ReadKernelPeerPid());
  warmTransfer=new GmgnPinTransferHandshake(warmPipe,warmPins,warmRoot,"CHILD",firstHash,values["session_id"],challenge,rootBinding,self);
  warmTransfer.Execute();Check();
  // Completion claims COMPLETE; creating ChildAdmission then sets TRANSFERRED.
  warmCompletion=new GmgnHandoffCompletion(warmPipe,warmTransfer,warmPins,warmRoot,auth,"CHILD");
  warmAdmission=warmTransfer.CreateChildAdmission(firstManifest);Check();
  warmCompletion.SendChildReady(warmAdmission);
  // The successful fourth SEND permits Root exit. Only local checks below.
  warmOrigin=warmCompletion.ClaimWarmOrigin(first,auth,warmAdmission);warmOrigin.Check();Check();
  return warmOrigin;
 }catch{MarkFault();if(warmPipe!=null)warmPipe.Abort();throw;}}
}
