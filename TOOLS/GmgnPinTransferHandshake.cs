// Three messages bind public source pins; this class never carries IPC tokens.
using System;using System.Diagnostics;using System.Text;
public sealed class GmgnPinTransferHandshake {
 readonly object sync=new object();readonly GmgnSupervisorPipe pipe;readonly GmgnSourcePinBinding pins;
 readonly GmgnScheduledActionIdentity peer;readonly bool root;
 readonly string generation,session,challenge;readonly string[] rootSelf,childSelf,digests;
 string state="NEW";bool completionClaimed;
 public GmgnPinTransferHandshake(GmgnSupervisorPipe transport,GmgnSourcePinBinding sourcePins,GmgnScheduledActionIdentity originalPeer,
  string role,string generationSha256,string sessionId,string freshChallenge,string[] rootNative,string[] childNative){
  try{
   var assembly=GetType().Assembly;
   if(transport==null||sourcePins==null||originalPeer==null||transport.GetType().Assembly!=assembly||
    sourcePins.GetType().Assembly!=assembly||originalPeer.GetType().Assembly!=assembly||
    (role!="ROOT"&&role!="CHILD")||!Hex(generationSha256,64)||!Hex(sessionId,32)||!Hex(freshChallenge,64))throw Fail("GMGN_PIN_ACK_INPUT");
   root=role=="ROOT";if(transport.IsServerSide!=root)throw Fail("GMGN_PIN_ACK_ROLE");
   pipe=transport;pins=sourcePins;peer=originalPeer;generation=generationSha256;session=sessionId;challenge=freshChallenge;
   rootSelf=Copy(rootNative);childSelf=Copy(childNative);digests=pins.ReadDigests();
   if(digests.Length!=3||digests[0]!="1"||!Hex(digests[1],64)||!Hex(digests[2],64))throw Fail("GMGN_PIN_ACK_DIGEST");
   pipe.ClaimHandshake();
   Check();
  }catch{state="FAULT";throw;}
 }
 static InvalidOperationException Fail(string code){return new InvalidOperationException(code);}
 static bool Hex(string value,int length){return value!=null&&System.Text.RegularExpressions.Regex.IsMatch(value,"\\A[0-9a-f]{"+length+"}\\z");}
 static string[] Copy(string[] value){if(value==null||value.Length!=4)throw Fail("GMGN_PIN_ACK_NATIVE_BINDING");var copy=(string[])value.Clone();foreach(string item in copy)if(String.IsNullOrEmpty(item))throw Fail("GMGN_PIN_ACK_NATIVE_BINDING");return copy;}
 static string Quote(string value){return "\""+value.Replace("\\","\\\\").Replace("\"","\\\"")+"\"";}
 static string Array(string[] value){return "["+Quote(value[0])+","+Quote(value[1])+","+Quote(value[2])+","+Quote(value[3])+"]";}
 void Check(){
  if(state=="FAULT")throw Fail("GMGN_PIN_ACK_FAULT");pins.Check();
  string[] actual=pins.ReadDigests();for(int i=0;i<3;i++)if(actual[i]!=digests[i])throw Fail("GMGN_PIN_ACK_DIGEST_CHANGED");
  string[] self;using(var current=Process.GetCurrentProcess())self=GmgnRepairProcessBinding.Read(current);
  string[] wanted=root?rootSelf:childSelf;
  for(int i=0;i<4;i++)if(self[i]!=wanted[i])throw Fail("GMGN_PIN_ACK_SELF_CHANGED");
  string[] remote=peer.ReadNativeBinding();wanted=root?childSelf:rootSelf;
  for(int i=0;i<4;i++)if(remote[i]!=wanted[i])throw Fail("GMGN_PIN_ACK_PEER_CHANGED");
  peer.CheckPeerPid(pipe.ReadKernelPeerPid());pins.Check();
 }
 byte[] Message(int sequence){
  string direction=sequence==2?"CHILD_TO_ROOT":"ROOT_TO_CHILD";
  string kind=sequence==1?"CHALLENGE":sequence==2?"PIN_ACK":"COMMIT";
  string text="{\"schema_version\":1,\"kind\":"+Quote(kind)+",\"direction\":"+Quote(direction)+
   ",\"sequence\":"+sequence.ToString(System.Globalization.CultureInfo.InvariantCulture)+",\"session_id\":"+Quote(session)+
   ",\"challenge\":"+Quote(challenge)+",\"generation_sha256\":"+Quote(generation)+",\"root\":"+Array(rootSelf)+
   ",\"child\":"+Array(childSelf)+",\"file_pins_sha256\":"+Quote(digests[1])+",\"directory_pins_sha256\":"+Quote(digests[2])+"}";
  return new UTF8Encoding(false,true).GetBytes(text);
 }
 void Send(int sequence){Check();pipe.WriteMessage(Message(sequence));Check();}
 void Receive(int sequence){
  Check();byte[] actual=pipe.ReadMessage();Check();byte[] expected=Message(sequence);
  // Canonical wire encoding is exact: duplicates, missing/extra fields,
  // direction/sequence changes and alternate JSON encodings are rejected.
  if(actual.Length!=expected.Length)throw Fail("GMGN_PIN_ACK_MESSAGE");
  int difference=0;for(int i=0;i<actual.Length;i++)difference|=actual[i]^expected[i];
  if(difference!=0)throw Fail("GMGN_PIN_ACK_MESSAGE");Check();
 }
 public void Execute(){lock(sync)try{
  if(state!="NEW")throw Fail("GMGN_PIN_ACK_USED");state=root?"SEND_CHALLENGE":"READ_CHALLENGE";
  if(root){Send(1);state="READ_PIN_ACK";Receive(2);state="SEND_COMMIT";Send(3);}
  else{Receive(1);state="SEND_PIN_ACK";Send(2);state="READ_COMMIT";Receive(3);}
  Check();state="COMPLETE";
 }catch{state="FAULT";if(pipe!=null)pipe.Abort();throw;}}
 public string[] ReadCompletedBinding(){lock(sync)try{
  if(state!="COMPLETE")throw Fail("GMGN_PIN_ACK_INCOMPLETE");Check();
  return new string[]{"1",generation,session,challenge,digests[1],digests[2]};
 }catch{state="FAULT";if(pipe!=null)pipe.Abort();throw;}}
 internal string[] ClaimCompletion(GmgnSupervisorPipe transport,GmgnSourcePinBinding sourcePins,
  GmgnScheduledActionIdentity originalPeer,bool rootRole){lock(sync)try{
  if(completionClaimed)throw Fail("GMGN_PIN_ACK_COMPLETION_CLAIMED");
  if(state!="COMPLETE"||rootRole!=root||!Object.ReferenceEquals(pipe,transport)||
   !Object.ReferenceEquals(pins,sourcePins)||!Object.ReferenceEquals(peer,originalPeer))throw Fail("GMGN_PIN_ACK_COMPLETION_BINDING");
  completionClaimed=true;Check();return new string[]{"1",generation,session,challenge,digests[1],digests[2]};
 }catch{state="FAULT";if(pipe!=null)pipe.Abort();throw;}}
 public GmgnSupervisorSourceAdmission CreateChildAdmission(string manifestPath){lock(sync){
  GmgnSupervisorSourceAdmission candidate=null;
  try{
   if(root||state!="COMPLETE")throw Fail("GMGN_PIN_ACK_ADMISSION_STATE");Check();
   if(pins.ReadManifestHash(manifestPath)!=generation)throw Fail("GMGN_PIN_ACK_MANIFEST_GENERATION");
   candidate=new GmgnSupervisorSourceAdmission(pins,manifestPath,generation,childSelf,digests);
   Check();candidate.Check();state="TRANSFERRED";return candidate;
  }catch{if(candidate!=null)candidate.Abort();state="FAULT";if(pipe!=null)pipe.Abort();throw;}
 }}
 public void Abort(){lock(sync){state="FAULT";if(pipe!=null)pipe.Abort();}}
 public string State{get{lock(sync)return state;}}
}
