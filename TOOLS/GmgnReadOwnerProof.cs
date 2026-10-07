// Held public owner evidence only. Never a retirement or SDK authority.
using System;using System.Collections.Generic;using System.IO;using System.Runtime.InteropServices;
using System.Security.Cryptography;using System.Text;using System.Web.Script.Serialization;using System.Management.Automation;
using System.Security.AccessControl;using System.Security.Principal;
public sealed class GmgnReadOwnerProof {
 [StructLayout(LayoutKind.Sequential)]struct Standard{public long Allocation,Bytes;public uint Links;public byte Pending,Directory;}
 [StructLayout(LayoutKind.Sequential)]struct Tag{public uint Attributes,Reparse;}
 [StructLayout(LayoutKind.Sequential)]struct Id{public ulong Volume;[MarshalAs(UnmanagedType.ByValArray,SizeConst=16)]public byte[] Bytes;}
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern IntPtr CreateFileW(string path,uint access,uint share,IntPtr security,uint creation,uint flags,IntPtr template);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool GetFileInformationByHandleEx(IntPtr h,int kind,ref Standard value,uint size);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool GetFileInformationByHandleEx(IntPtr h,int kind,ref Tag value,uint size);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool GetFileInformationByHandleEx(IntPtr h,int kind,ref Id value,uint size);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool GetHandleInformation(IntPtr h,out uint flags);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern uint GetFinalPathNameByHandleW(IntPtr h,StringBuilder value,uint size,uint flags);
 [DllImport("advapi32.dll")]static extern uint GetSecurityInfo(IntPtr h,int type,uint info,out IntPtr owner,out IntPtr group,out IntPtr dacl,out IntPtr sacl,out IntPtr descriptor);
 [DllImport("advapi32.dll")]static extern uint GetSecurityDescriptorLength(IntPtr descriptor);
 [DllImport("kernel32.dll")]static extern IntPtr LocalFree(IntPtr descriptor);
 static readonly List<GmgnReadOwnerProof> retained=new List<GmgnReadOwnerProof>();
 readonly object sync=new object();readonly GmgnImmutableSources sources;readonly GmgnReadLocalAuth auth;
 readonly GmgnRepairNamespace names;readonly string path,hash,fixture,helper;readonly string[][] legacy;
 readonly IntPtr handle;string identity;long length,observed,frequency;bool complete,fault,validated;
 string validatedBody,validatedMac;
 readonly bool requestMode;readonly GmgnReadDirectoryIdentity recovery;readonly IntPtr recoverySecurity;readonly string recoveryId;string requestBody;
 static InvalidOperationException Error(string code){return new InvalidOperationException(code);}
 public GmgnReadOwnerProof(GmgnImmutableSources first,GmgnReadLocalAuth authentication,string proofPath,string externalSha,string[][] legacyBindings,string fixtureScope)
  :this(first,authentication,proofPath,externalSha,legacyBindings,fixtureScope,null){}
 internal static GmgnReadOwnerProof OpenRetirementRequest(GmgnImmutableSources first,GmgnReadLocalAuth authentication,GmgnReadDirectoryIdentity directory,string externalSha){
  if(directory==null)throw Error("GMGN_OWNER_PIN_INPUT");
  return new GmgnReadOwnerProof(first,authentication,Path.Combine(directory.Path,"retire.request.json"),externalSha,null,null,directory);
 }
 GmgnReadOwnerProof(GmgnImmutableSources first,GmgnReadLocalAuth authentication,string proofPath,string externalSha,string[][] legacyBindings,string fixtureScope,GmgnReadDirectoryIdentity directory){
  lock(retained)retained.Add(this);
  try{
   requestMode=directory!=null;recovery=directory;
   if(first==null||authentication==null||first.GetType().Assembly!=GetType().Assembly||authentication.GetType().Assembly!=GetType().Assembly||first.IsAdvisorDomain||
    !(requestMode?directory.GetType().Assembly==GetType().Assembly&&System.Text.RegularExpressions.Regex.IsMatch(directory.Path,@"\AC:\\Users\\<local-user>\\Desktop\\IMC_TRADING_MAIN\\[.]local-tools\\gmgn-read-recovery-[0-9a-f]{32}\z")&&proofPath==Path.Combine(directory.Path,"retire.request.json"):
     System.Text.RegularExpressions.Regex.IsMatch(proofPath??"",@"\AC:\\Users\\<local-user>\\Desktop\\IMC_TRADING_MAIN\\[.]local-tools\\gmgn-owner-proof-[0-9a-f]{32}[.]json\z"))||
    Path.GetFullPath(proofPath)!=proofPath||!System.Text.RegularExpressions.Regex.IsMatch(externalSha??"",@"\A[0-9a-f]{64}\z")||
    (!requestMode&&(legacyBindings==null||legacyBindings.Length!=2)))throw Error("GMGN_OWNER_PIN_INPUT");
   sources=first;auth=authentication;path=proofPath;hash=externalSha;fixture=fixtureScope;legacy=new string[2][];
   if(!requestMode)for(int r=0;r<2;r++){if(legacyBindings[r]==null||legacyBindings[r].Length!=4)throw Error("GMGN_OWNER_PIN_INPUT");legacy[r]=(string[])legacyBindings[r].Clone();foreach(string value in legacy[r])if(String.IsNullOrEmpty(value))throw Error("GMGN_OWNER_PIN_INPUT");}
   const string code=@"%USERPROFILE%\Desktop\IMC_TRADING_MAIN\TOOLS\GmgnReadOwnerProof.cs",script=@"%USERPROFILE%\Desktop\IMC_TRADING_MAIN\TOOLS\GMGN-ReadSupervisorCommands.ps1";
   sources.Check();auth.Check();if(!sources.Contains(code)||!sources.Contains(script))throw Error("GMGN_OWNER_PIN_SOURCE");helper=sources.ReadText(script);
   if(requestMode){recovery.Check();recoveryId=recovery.Identity;recoverySecurity=CreateFileW(recovery.Path,0x000200a0U,3,IntPtr.Zero,3,0x02200000U,IntPtr.Zero);if(recoverySecurity==IntPtr.Zero||recoverySecurity==new IntPtr(-1))throw Error("GMGN_OWNER_PIN_RECOVERY_OPEN");CheckRecovery();}
   names=new GmgnRepairNamespace(Path.GetDirectoryName(path));handle=CreateFileW(path,0x80000000U,1,IntPtr.Zero,3,0x02200080U,IntPtr.Zero);
   if(handle==IntPtr.Zero||handle==new IntPtr(-1))throw Error("GMGN_OWNER_PIN_OPEN");Inspect(true);Validate();validated=true;complete=true;Check();
  }catch(Exception error){fault=true;throw NormalizeBoundary(error);}
 }
 void CheckRecovery(){if(!requestMode)return;recovery.Check();if(recovery.Identity!=recoveryId)throw Error("GMGN_OWNER_PIN_RECOVERY");
  IntPtr o,g,d,s,descriptor;if(GetSecurityInfo(recoverySecurity,1,5,out o,out g,out d,out s,out descriptor)!=0||descriptor==IntPtr.Zero)throw Error("GMGN_OWNER_PIN_RECOVERY_ACL");
  try{uint count=GetSecurityDescriptorLength(descriptor);if(count==0||count>32768)throw Error("GMGN_OWNER_PIN_RECOVERY_ACL");var bytes=new byte[count];Marshal.Copy(descriptor,bytes,0,bytes.Length);var acl=new RawSecurityDescriptor(bytes,0);
   string sid=WindowsIdentity.GetCurrent().User.Value;if(acl.Owner==null||acl.Owner.Value!=sid||(acl.ControlFlags&ControlFlags.DiscretionaryAclProtected)==0||acl.DiscretionaryAcl==null||acl.DiscretionaryAcl.Count!=2)throw Error("GMGN_OWNER_PIN_RECOVERY_ACL");
   var seen=new HashSet<string>(StringComparer.Ordinal);foreach(GenericAce raw in acl.DiscretionaryAcl){var ace=raw as CommonAce;if(ace==null||ace.IsCallback||ace.AceQualifier!=AceQualifier.AccessAllowed||ace.AceFlags!=AceFlags.None||ace.AccessMask!=0x001f01ff||(ace.SecurityIdentifier.Value!=sid&&ace.SecurityIdentifier.Value!="S-1-5-18")||!seen.Add(ace.SecurityIdentifier.Value))throw Error("GMGN_OWNER_PIN_RECOVERY_ACL");}
  }finally{if(LocalFree(descriptor)!=IntPtr.Zero)throw Error("GMGN_OWNER_PIN_RECOVERY_ACL");}recovery.Check();
 }
 void Inspect(bool initial){
  Standard standard=new Standard();Tag tag=new Tag();Id id=new Id();uint flags;var actual=new StringBuilder(32768);
  if(!GetHandleInformation(handle,out flags)||(flags&1)!=0||!GetFileInformationByHandleEx(handle,1,ref standard,24)||
   !GetFileInformationByHandleEx(handle,9,ref tag,8)||!GetFileInformationByHandleEx(handle,18,ref id,24)||standard.Directory!=0||standard.Pending!=0||standard.Links!=1||
   standard.Bytes<1||standard.Bytes>32768||(tag.Attributes&0x400)!=0||tag.Reparse!=0)throw Error("GMGN_OWNER_PIN_SHAPE");
  uint n=GetFinalPathNameByHandleW(handle,actual,(uint)actual.Capacity,0);if(n==0||n>=actual.Capacity||!String.Equals(actual.ToString(),@"\\?\"+path,StringComparison.OrdinalIgnoreCase))throw Error("GMGN_OWNER_PIN_PATH");
  string current=id.Volume.ToString("x16")+":"+BitConverter.ToString(id.Bytes).Replace("-","").ToLowerInvariant();
  if(initial){identity=current;length=standard.Bytes;}else if(identity!=current||length!=standard.Bytes)throw Error("GMGN_OWNER_PIN_CHANGED");
 }
 string ReadHeld(){
  names.Check();Inspect(false);byte[] bytes=new byte[(int)length];
  using(var stream=new FileStream(new Microsoft.Win32.SafeHandles.SafeFileHandle(handle,false),FileAccess.Read)){
   stream.Position=0;int total=0;while(total<bytes.Length){int count=stream.Read(bytes,total,bytes.Length-total);if(count<=0)throw Error("GMGN_OWNER_PIN_SHORT");total+=count;}
  }
  using(var sha=SHA256.Create())if(BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-","").ToLowerInvariant()!=hash)throw Error("GMGN_OWNER_PIN_HASH");
  Inspect(false);names.Check();return new UTF8Encoding(false,true).GetString(bytes);
 }
 // Owner evidence is a flat object with scalar fields. Reject duplicate
 // decoded names, nested containers, invalid separators and trailing bytes.
 internal static void UniqueFlatObject(string text){
  if(String.IsNullOrEmpty(text))throw Error("GMGN_OWNER_PIN_JSON");
  int index=0;var keys=new HashSet<string>(StringComparer.Ordinal);var parser=new JavaScriptSerializer{MaxJsonLength=32768,RecursionLimit=4};
  Skip(text,ref index);if(index>=text.Length||text[index++]!='{')throw Error("GMGN_OWNER_PIN_JSON");Skip(text,ref index);
  if(index<text.Length&&text[index]=='}'){index++;Skip(text,ref index);if(index!=text.Length)throw Error("GMGN_OWNER_PIN_JSON");return;}
  while(true){
   int start=index;StringToken(text,ref index);string key;try{key=parser.Deserialize<string>(text.Substring(start,index-start));}catch{throw Error("GMGN_OWNER_PIN_JSON");}
   if(!keys.Add(key))throw Error("GMGN_OWNER_PIN_DUPLICATE");Skip(text,ref index);if(index>=text.Length||text[index++]!=':')throw Error("GMGN_OWNER_PIN_JSON");Skip(text,ref index);
   start=index;
   if(index<text.Length&&text[index]=='"')StringToken(text,ref index);
   else{start=index;while(index<text.Length&&text[index]!=','&&text[index]!='}')index++;string scalar=text.Substring(start,index-start).Trim();
    if(!System.Text.RegularExpressions.Regex.IsMatch(scalar,@"\A(?:true|false|null|-?(?:0|[1-9][0-9]*)(?:[.][0-9]+)?(?:[eE][+-]?[0-9]+)?)\z"))throw Error("GMGN_OWNER_PIN_JSON");}
   try{parser.DeserializeObject(text.Substring(start,index-start));}catch{throw Error("GMGN_OWNER_PIN_JSON");}
   Skip(text,ref index);if(index>=text.Length)throw Error("GMGN_OWNER_PIN_JSON");char separator=text[index++];
   if(separator=='}'){Skip(text,ref index);if(index!=text.Length)throw Error("GMGN_OWNER_PIN_JSON");return;}
   if(separator!=',')throw Error("GMGN_OWNER_PIN_JSON");Skip(text,ref index);
  }
 }
 static void Skip(string text,ref int index){while(index<text.Length&&(text[index]==' '||text[index]=='\r'||text[index]=='\n'||text[index]=='\t'))index++;}
 static void StringToken(string text,ref int index){
  if(index>=text.Length||text[index++]!='"')throw Error("GMGN_OWNER_PIN_JSON");bool escaped=false;
  while(index<text.Length){char value=text[index++];if(value<32)throw Error("GMGN_OWNER_PIN_JSON");if(escaped){escaped=false;continue;}if(value=='\\'){escaped=true;continue;}if(value=='"')return;}throw Error("GMGN_OWNER_PIN_JSON");
 }
 void Validate(){
  sources.Check();auth.Check();CheckRecovery();string text=ReadHeld();UniqueFlatObject(text);var parser=new JavaScriptSerializer{MaxJsonLength=32768,RecursionLimit=4};Dictionary<string,object> packet;
  try{packet=parser.Deserialize<Dictionary<string,object>>(text);}catch{throw Error("GMGN_OWNER_PIN_ENVELOPE");}
  if(packet==null||packet.Count!=2||!packet.ContainsKey("body")||!packet.ContainsKey("mac")||!(packet["body"] is string)||!(packet["mac"] is string))throw Error("GMGN_OWNER_PIN_ENVELOPE");
  string body=(string)packet["body"],mac=(string)packet["mac"];
  if(!System.Text.RegularExpressions.Regex.IsMatch(mac,@"\A[0-9a-f]{64}\z"))throw Error("GMGN_OWNER_PIN_ENVELOPE");
  UniqueFlatObject(body);if(!auth.Verify(body,mac))throw Error("GMGN_OWNER_PIN_AUTH");
  validatedBody=body;validatedMac=mac;
  if(requestMode){Dictionary<string,object> fields;try{fields=parser.Deserialize<Dictionary<string,object>>(body);}catch{throw Error("GMGN_OWNER_PIN_JSON");}
   var ordered=new SortedDictionary<string,string>(StringComparer.Ordinal);foreach(var field in fields){if(!(field.Value is string))throw Error("GMGN_OWNER_PIN_REQUEST_TYPE");ordered.Add(field.Key,(string)field.Value);}
   if(parser.Serialize(ordered)!=body)throw Error("GMGN_OWNER_PIN_REQUEST_CANONICAL");requestBody=body;sources.Check();auth.Check();CheckRecovery();ReadHeld();return;
  }
  const string invoke=@"param($text,$path,$body,$legacy,$fixture)
$ErrorActionPreference='Stop';$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseInput($text,$path,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'GMGN_OWNER_PIN_VALIDATOR_PARSE'}
. ($ast.GetScriptBlock())
$owner=$body|ConvertFrom-Json -ErrorAction Stop
$clock=Assert-GmgnStageOwnerProofBody $owner $legacy $fixture
$clock.Observed.ToString()+'|'+$clock.Frequency.ToString()";
  try{using(PowerShell ps=PowerShell.Create()){
   ps.AddScript(invoke).AddArgument(helper).AddArgument(@"%USERPROFILE%\Desktop\IMC_TRADING_MAIN\TOOLS\GMGN-ReadSupervisorCommands.ps1").AddArgument(body).AddArgument(legacy).AddArgument(fixture);
   var output=ps.Invoke();if(ps.HadErrors||output.Count!=1||!(output[0].BaseObject is string))throw Error("GMGN_OWNER_PIN_VALIDATOR");
   string[] clock=((string)output[0].BaseObject).Split('|');if(clock.Length!=2||!Int64.TryParse(clock[0],out observed)||!Int64.TryParse(clock[1],out frequency))throw Error("GMGN_OWNER_PIN_VALIDATOR");
  }}catch(Exception error){throw Normalize(error);}
  sources.Check();auth.Check();ReadHeld();CheckClock();
 }
 void CheckClock(){long now=System.Diagnostics.Stopwatch.GetTimestamp();
  if(frequency!=System.Diagnostics.Stopwatch.Frequency||observed<=0||observed>now||(now-observed)/(double)frequency>15)throw Error("GMGN_STAGE_OWNER_PROOF_STALE");}
 static InvalidOperationException NormalizeBoundary(Exception error){
  string[] codes={"GMGN_OWNER_PIN_INPUT","GMGN_OWNER_PIN_SOURCE","GMGN_OWNER_PIN_OPEN","GMGN_OWNER_PIN_SHAPE","GMGN_OWNER_PIN_PATH","GMGN_OWNER_PIN_CHANGED","GMGN_OWNER_PIN_SHORT","GMGN_OWNER_PIN_HASH","GMGN_OWNER_PIN_JSON","GMGN_OWNER_PIN_DUPLICATE","GMGN_OWNER_PIN_ENVELOPE","GMGN_OWNER_PIN_AUTH","GMGN_OWNER_PIN_FAULT","GMGN_SOURCE_LOCKED","GMGN_AUTH_FAULT","GMGN_OWNER_PIN_RECOVERY_OPEN","GMGN_OWNER_PIN_RECOVERY","GMGN_OWNER_PIN_RECOVERY_ACL","GMGN_OWNER_PIN_REQUEST_TYPE","GMGN_OWNER_PIN_REQUEST_CANONICAL","GMGN_OWNER_PIN_ROLE"};
  for(int i=0;error!=null&&i<8;i++,error=error.InnerException){foreach(string code in codes)if(error.Message==code)return Error(code);var normalized=Normalize(error);if(normalized.Message!="GMGN_OWNER_PIN_VALIDATOR_OPERATION")return normalized;}
  return Error("GMGN_OWNER_PIN_OPERATION");
 }
 internal static InvalidOperationException Normalize(Exception error){
  string[] codes={"GMGN_STAGE_COMMAND_FIELDS","GMGN_STAGE_COMMAND_SCALAR_TYPE","GMGN_STAGE_OWNER_PROOF_BINDING","GMGN_STAGE_OWNER_PROOF_FIXTURE","GMGN_STAGE_OWNER_LOADED_SOURCE","GMGN_STAGE_OWNER_PROOF_STALE","GMGN_OWNER_PIN_VALIDATOR_PARSE","GMGN_OWNER_PIN_VALIDATOR"};
  for(int i=0;error!=null&&i<8;i++,error=error.InnerException)foreach(string code in codes)if(error.Message==code)return Error(code);return Error("GMGN_OWNER_PIN_VALIDATOR_OPERATION");
 }
 public void Check(){lock(sync)try{
  if(fault||!complete||!validated)throw Error("GMGN_OWNER_PIN_FAULT");
  // Semantics were validated once against these immutable held bytes and
  // trusted held source. Repeat every native/content/authentication boundary,
  // including MAC verification, without another runspace or JSON parse.
  sources.Check();auth.Check();CheckRecovery();ReadHeld();
  if(!auth.Verify(validatedBody,validatedMac))throw Error("GMGN_OWNER_PIN_AUTH");
  sources.Check();auth.Check();CheckRecovery();ReadHeld();if(!requestMode)CheckClock();
 }catch(Exception error){fault=true;throw NormalizeBoundary(error);}}
 public string[] ReadBinding(){lock(sync){Check();return new string[]{path,identity,hash,observed.ToString(),frequency.ToString()};}}
 // Immutable clock comparison immediately after successful construction.
 // This is not freshness, Source/Auth health or retirement authority; the
 // caller must still perform full Check() before the native effect.
 internal bool MatchesValidatedClock(GmgnImmutableSources first,GmgnReadLocalAuth authentication,long expectedObserved,long expectedFrequency){lock(sync){
  return !requestMode&&complete&&validated&&!fault&&Object.ReferenceEquals(sources,first)&&
   Object.ReferenceEquals(auth,authentication)&&observed==expectedObserved&&frequency==expectedFrequency;
 }}
 internal bool Matches(GmgnImmutableSources first,GmgnReadLocalAuth authentication){return !requestMode&&Object.ReferenceEquals(sources,first)&&Object.ReferenceEquals(auth,authentication);}
 internal string ReadRequestBody(){lock(sync){if(!requestMode)throw Error("GMGN_OWNER_PIN_ROLE");Check();return requestBody;}}
 internal bool MatchesRequest(GmgnImmutableSources first,GmgnReadLocalAuth authentication,GmgnReadDirectoryIdentity directory){return requestMode&&Object.ReferenceEquals(sources,first)&&Object.ReferenceEquals(auth,authentication)&&Object.ReferenceEquals(recovery,directory);}
 public void Abort(){lock(sync)fault=true;}
 public string State{get{lock(sync)return complete&&!fault?(requestMode?"PUBLIC_REQUEST_BOUND":"PUBLIC_PROOF_BOUND"):"FAULT";}}
}
