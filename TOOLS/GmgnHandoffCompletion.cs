// Authenticated fourth message after child source admission. No dispatch or
// retirement authority: callers must still satisfy their runtime contracts.
using System;using System.Diagnostics;using System.Text;
public sealed class GmgnHandoffCompletion {
 readonly object sync=new object();readonly GmgnSupervisorPipe pipe;
 readonly GmgnPinTransferHandshake handshake;readonly GmgnSourcePinBinding pins;
 readonly GmgnScheduledActionIdentity peer;readonly GmgnReadLocalAuth auth;
 readonly bool root;readonly string[] binding,self,remote;readonly string fixtureScope;string state="NEW";
 readonly string validatedSession,validatedChallenge;
 static Action<GmgnScheduledActionIdentity> fixtureAfterWrite=null;
 GmgnSupervisorSourceAdmission completedAdmission;bool warmOriginClaimed,rootTaskClaimed;
 public GmgnHandoffCompletion(GmgnSupervisorPipe transport,GmgnPinTransferHandshake transfer,
  GmgnSourcePinBinding sourcePins,GmgnScheduledActionIdentity originalPeer,GmgnReadLocalAuth authentication,string role)
  :this(transport,transfer,sourcePins,originalPeer,authentication,role,null){}
 public GmgnHandoffCompletion(GmgnSupervisorPipe transport,GmgnPinTransferHandshake transfer,
  GmgnSourcePinBinding sourcePins,GmgnScheduledActionIdentity originalPeer,GmgnReadLocalAuth authentication,string role,string ownFixtureScope){
  try{
   if(transport==null||transfer==null||sourcePins==null||originalPeer==null||authentication==null||
    transport.GetType().Assembly!=GetType().Assembly||transfer.GetType().Assembly!=GetType().Assembly||
    sourcePins.GetType().Assembly!=GetType().Assembly||originalPeer.GetType().Assembly!=GetType().Assembly||
    authentication.GetType().Assembly!=GetType().Assembly||(role!="ROOT"&&role!="CHILD"))throw Fail("GMGN_HANDOFF_READY_INPUT");
   root=role=="ROOT";if(transport.IsServerSide!=root)throw Fail("GMGN_HANDOFF_READY_ROLE");
   pipe=transport;handshake=transfer;pins=sourcePins;peer=originalPeer;auth=authentication;
   if(ownFixtureScope!=null&&(!System.Text.RegularExpressions.Regex.IsMatch(ownFixtureScope,@"\AC:\\Users\\<local-user>\\Desktop\\IMC_TRADING_MAIN\\[.]local-tools\\pin-ack-native-[0-9a-f]{32}\z")||
    !pins.ReadNativeVectors()[1].Contains("\"path\":"+Quote(ownFixtureScope)+",")))throw Fail("GMGN_HANDOFF_FIXTURE_SCOPE");
   fixtureScope=ownFixtureScope;
   binding=handshake.ClaimCompletion(pipe,pins,peer,root);remote=peer.ReadNativeBinding();
   using(var current=Process.GetCurrentProcess())self=GmgnRepairProcessBinding.Read(current);
   Check();
   if(binding==null||binding.Length!=6||binding[0]!="1"||
    !System.Text.RegularExpressions.Regex.IsMatch(binding[2]??"",@"\A[0-9a-f]{32}\z")||
    !System.Text.RegularExpressions.Regex.IsMatch(binding[3]??"",@"\A[0-9a-f]{64}\z"))throw Fail("GMGN_HANDOFF_READY_INPUT");
   validatedSession=binding[2];validatedChallenge=binding[3];
  }catch{state="FAULT";throw;}
 }
 static InvalidOperationException Fail(string code){return new InvalidOperationException(code);}
 static string Quote(string value){return "\""+value.Replace("\\","\\\\").Replace("\"","\\\"")+"\"";}
 static string Array(string[] values){var result=new StringBuilder("[");for(int i=0;i<values.Length;i++){if(i>0)result.Append(',');result.Append(Quote(values[i]));}return result.Append(']').ToString();}
 void Check(){
  if(state=="FAULT")throw Fail("GMGN_HANDOFF_READY_FAULT");
  bool sourceMatches=pins.MatchDigests(binding[4],binding[5]);auth.Check();
  if(!sourceMatches)throw Fail("GMGN_HANDOFF_READY_SOURCE");
  string[] actual;using(var current=Process.GetCurrentProcess())actual=GmgnRepairProcessBinding.Read(current);
  for(int i=0;i<4;i++)if(actual[i]!=self[i])throw Fail("GMGN_HANDOFF_READY_SELF");
  actual=peer.ReadNativeBinding();for(int i=0;i<4;i++)if(actual[i]!=remote[i])throw Fail("GMGN_HANDOFF_READY_PEER");
  peer.CheckPeerPid(pipe.ReadKernelPeerPid());pins.Check();auth.Check();
 }
 byte[] Packet(){
  string body="{\"schema_version\":1,\"kind\":\"ADMISSION_READY\",\"direction\":\"CHILD_TO_ROOT\",\"sequence\":4,\"binding\":"+
   Array(binding)+",\"root\":"+Array(root?self:remote)+",\"child\":"+Array(root?remote:self)+",\"local_auth\":"+Array(auth.ReadPublicBinding())+"}";
  return new UTF8Encoding(false,true).GetBytes("{\"body\":"+Quote(body)+",\"mac\":"+Quote(auth.Sign(body))+"}");
 }
 public void SendChildReady(GmgnSupervisorSourceAdmission admission){lock(sync)try{
  if(root||state!="NEW"||handshake.State!="TRANSFERRED"||admission==null||admission.GetType().Assembly!=GetType().Assembly)throw Fail("GMGN_HANDOFF_READY_STATE");
  Check();admission.Check();string[] admitted=admission.ReadBinding();
  if(admitted.Length!=4||admitted[0]!=binding[0]||admitted[1]!=binding[1]||admitted[2]!=binding[4]||admitted[3]!=binding[5])throw Fail("GMGN_HANDOFF_READY_ADMISSION");
  state="SENDING";pipe.WriteMessage(Packet());
  if(fixtureAfterWrite!=null){if(fixtureScope==null)throw Fail("GMGN_HANDOFF_FIXTURE_FORBIDDEN");fixtureAfterWrite(peer);}
  // Receiving this packet allows Root to exit. No original-peer or pipe
  // query may make the already admitted child depend on Root afterward.
  admission.Check();pins.Check();auth.Check();completedAdmission=admission;state="COMPLETE";
 }catch{state="FAULT";if(pipe!=null)pipe.Abort();throw;}}
 public void WaitChildReady(){lock(sync)try{
  if(!root||state!="NEW"||handshake.State!="COMPLETE")throw Fail("GMGN_HANDOFF_READY_STATE");
  state="RECEIVING";Check();byte[] actual=pipe.ReadMessage();Check();byte[] expected=Packet();
  if(actual.Length!=expected.Length)throw Fail("GMGN_HANDOFF_READY_MESSAGE");
  int difference=0;for(int i=0;i<actual.Length;i++)difference|=actual[i]^expected[i];
  if(difference!=0)throw Fail("GMGN_HANDOFF_READY_MESSAGE");Check();state="COMPLETE";
 }catch{state="FAULT";if(pipe!=null)pipe.Abort();throw;}}
 public string State{get{lock(sync)return state;}}
 // A completed fourth receive already checked the kernel peer. Retain the
 // original actor afterward; the transport deadline is not a lifetime lease.
 void CheckCommittedRoot(){
  if(!root||state!="COMPLETE")throw Fail("GMGN_ROOT_TASK_INCOMPLETE");
  if(handshake.State!="COMPLETE")throw Fail("GMGN_ROOT_TASK_TRANSFER_FAULT");
  bool matches=pins.MatchDigests(binding[4],binding[5]);auth.Check();
  if(!matches)throw Fail("GMGN_HANDOFF_READY_SOURCE");
  string[] current;using(var process=Process.GetCurrentProcess())current=GmgnRepairProcessBinding.Read(process);
  for(int i=0;i<4;i++)if(current[i]!=self[i])throw Fail("GMGN_HANDOFF_READY_SELF");
  current=peer.ReadNativeBinding();for(int i=0;i<4;i++)if(current[i]!=remote[i])throw Fail("GMGN_HANDOFF_READY_PEER");
  pins.Check();auth.Check();
 }
 internal string[] ClaimRootTask(GmgnImmutableSources sources,GmgnReadLocalAuth authentication,
  GmgnScheduledActionIdentity originalChild,string[] rootSelf){lock(sync)try{
  if(rootTaskClaimed)throw Fail("GMGN_ROOT_TASK_CLAIMED");rootTaskClaimed=true;
  RequireRootTaskReferences(sources,authentication,originalChild,rootSelf);CheckCommittedRoot();
  var result=new string[14];System.Array.Copy(binding,0,result,0,6);System.Array.Copy(self,0,result,6,4);System.Array.Copy(remote,0,result,10,4);return result;
 }catch{state="FAULT";throw;}}
 void RequireRootTaskReferences(GmgnImmutableSources sources,GmgnReadLocalAuth authentication,
  GmgnScheduledActionIdentity originalChild,string[] rootSelf){
  if(!root||state!="COMPLETE"||!Object.ReferenceEquals(auth,authentication)||
   !Object.ReferenceEquals(peer,originalChild)||rootSelf==null||rootSelf.Length!=4||
   !pins.MatchesSources(sources))throw Fail("GMGN_ROOT_TASK_CONTEXT");
  for(int i=0;i<4;i++)if(rootSelf[i]!=self[i])throw Fail("GMGN_ROOT_TASK_SELF");
 }
 internal void CheckRootTask(GmgnImmutableSources sources,GmgnReadLocalAuth authentication,
  GmgnScheduledActionIdentity originalChild,string[] rootSelf){lock(sync)try{
  if(!rootTaskClaimed)throw Fail("GMGN_ROOT_TASK_NOT_CLAIMED");
  RequireRootTaskReferences(sources,authentication,originalChild,rootSelf);CheckCommittedRoot();
 }catch{state="FAULT";throw;}}
 // One retained origin for a future explicit warm-validation admission.
 // This proves successful child send and local checks, not Root reception,
 // installation validation, scheduler provenance or permission to dispatch.
 internal WarmOrigin ClaimWarmOrigin(GmgnImmutableSources sources,GmgnReadLocalAuth authentication,GmgnSupervisorSourceAdmission admission){lock(sync)try{
  if(root||state!="COMPLETE"||warmOriginClaimed||!Object.ReferenceEquals(auth,authentication)||
   !Object.ReferenceEquals(completedAdmission,admission)||!pins.MatchesSources(sources)||!admission.MatchesPins(pins))throw Fail("GMGN_WARM_ORIGIN_BINDING");
  warmOriginClaimed=true;CheckIndependentOrigin();return new WarmOrigin(this);
 }catch{if(completedAdmission!=null&&Object.ReferenceEquals(completedAdmission,admission))completedAdmission.Abort();state="FAULT";throw;}}
 void CheckIndependentOrigin(){
  if(root||state!="COMPLETE"||!warmOriginClaimed||completedAdmission==null)throw Fail("GMGN_WARM_ORIGIN_FAULT");
  completedAdmission.Check();pins.Check();auth.Check();
  string[] actual;using(var current=Process.GetCurrentProcess())actual=GmgnRepairProcessBinding.Read(current);
  for(int i=0;i<4;i++)if(actual[i]!=self[i])throw Fail("GMGN_WARM_ORIGIN_SELF");
  if(binding==null||binding.Length!=6)throw Fail("GMGN_WARM_ORIGIN_SOURCE");
  if(!pins.MatchDigests(binding[4],binding[5]))throw Fail("GMGN_WARM_ORIGIN_SOURCE");
 }
 public sealed class WarmOrigin {
  readonly GmgnHandoffCompletion owner;bool fault;
  readonly string schema,generation,session,challenge,files,directories;
  internal WarmOrigin(GmgnHandoffCompletion completed){owner=completed;lock(owner.sync)try{
   Check();string[] claims=owner.completedAdmission.ReadValidatedClaims(owner.pins);
   if(owner.binding==null||owner.binding.Length!=6||owner.binding[0]!="1"||owner.binding[0]!=claims[0]||
    owner.binding[1]!=claims[1]||owner.binding[2]!=owner.validatedSession||owner.binding[3]!=owner.validatedChallenge||
    owner.binding[4]!=claims[2]||owner.binding[5]!=claims[3])throw Fail("GMGN_WARM_ORIGIN_SOURCE");
   schema=owner.binding[0];generation=owner.binding[1];session=owner.binding[2];challenge=owner.binding[3];files=owner.binding[4];directories=owner.binding[5];
  }catch{owner.completedAdmission.Abort();fault=true;owner.state="FAULT";throw;}}
  public void Check(){lock(owner.sync)try{if(fault)throw Fail("GMGN_WARM_ORIGIN_FAULT");owner.CheckIndependentOrigin();}catch{fault=true;owner.state="FAULT";throw;}}
  internal bool Matches(GmgnImmutableSources sources,GmgnReadLocalAuth authentication,GmgnSupervisorSourceAdmission admission){lock(owner.sync){Check();return owner.pins.MatchesSources(sources)&&Object.ReferenceEquals(owner.auth,authentication)&&Object.ReferenceEquals(owner.completedAdmission,admission);}}
  public string[] ReadBinding(){lock(owner.sync){Check();var result=new string[14];System.Array.Copy(owner.binding,0,result,0,6);System.Array.Copy(owner.self,0,result,6,4);System.Array.Copy(owner.remote,0,result,10,4);return result;}}
  // Full native boundaries with scalar claim comparisons. No array snapshot
  // or successful past check is used as current health or authority.
  internal void CheckValidatedOrigin(GmgnImmutableSources sources,GmgnReadLocalAuth authentication,GmgnSupervisorSourceAdmission admission){lock(owner.sync)try{
   if(fault||owner.root||owner.state!="COMPLETE"||!owner.warmOriginClaimed||owner.completedAdmission==null||
    !Object.ReferenceEquals(owner.auth,authentication)||!Object.ReferenceEquals(owner.completedAdmission,admission))throw Fail("GMGN_WARM_ORIGIN_FAULT");
   if(!owner.pins.CheckOriginalSource(sources))throw Fail("GMGN_WARM_ORIGIN_BINDING");
   admission.CheckClaimScalars(owner.pins);
   owner.auth.Check();string[] actual;using(var current=Process.GetCurrentProcess())actual=GmgnRepairProcessBinding.Read(current);
   for(int i=0;i<4;i++)if(actual[i]!=owner.self[i])throw Fail("GMGN_WARM_ORIGIN_SELF");
   if(owner.binding==null||owner.binding.Length!=6||owner.binding[0]!=schema||owner.binding[1]!=generation||
    owner.binding[2]!=session||owner.binding[3]!=challenge||owner.binding[4]!=files||owner.binding[5]!=directories)throw Fail("GMGN_WARM_ORIGIN_SOURCE");
   admission.CheckClaimScalars(owner.pins);
   if(!owner.pins.CheckOriginalSource(sources))throw Fail("GMGN_WARM_ORIGIN_BINDING");
  }catch{if(owner.completedAdmission!=null)owner.completedAdmission.Abort();fault=true;owner.state="FAULT";throw;}}
  // Used only after a full origin check in the same caller. Immutable role
  // and digest claims remain original; every independent fault state and
  // native directory/file/Self binding is checked again. No new authority.
  internal void CheckCheckpoint(GmgnImmutableSources sources,GmgnReadLocalAuth authentication,GmgnSupervisorSourceAdmission admission){lock(owner.sync)try{
   if(fault||owner.root||owner.state!="COMPLETE"||!owner.warmOriginClaimed||owner.completedAdmission==null||
    !Object.ReferenceEquals(owner.auth,authentication)||!Object.ReferenceEquals(owner.completedAdmission,admission))throw Fail("GMGN_WARM_ORIGIN_FAULT");
   admission.CheckLocalState(owner.pins);
   if(!owner.pins.CheckOriginalSource(sources))throw Fail("GMGN_WARM_ORIGIN_BINDING");
   owner.auth.Check();string[] actual;using(var current=Process.GetCurrentProcess())actual=GmgnRepairProcessBinding.Read(current);
   for(int i=0;i<4;i++)if(actual[i]!=owner.self[i])throw Fail("GMGN_WARM_ORIGIN_SELF");
   admission.CheckLocalState(owner.pins);
   if(!owner.pins.CheckOriginalSource(sources))throw Fail("GMGN_WARM_ORIGIN_BINDING");
  }catch{if(owner.completedAdmission!=null)owner.completedAdmission.Abort();fault=true;owner.state="FAULT";throw;}}
  public string State{get{lock(owner.sync)return !fault&&owner.state=="COMPLETE"?"ORIGIN_BOUND":"FAULT";}}
 }
}
