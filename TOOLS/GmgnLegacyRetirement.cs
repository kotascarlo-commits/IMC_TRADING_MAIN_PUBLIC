// Native retirement authority. Public callers cannot supply role booleans.
// The held, trusted validator executes in a fresh in-process runspace; it
// reads only captured public READY bytes and inherited local IPC auth.
using System;
using System.IO;
using System.Management.Automation;
using System.Text.RegularExpressions;
public sealed partial class GmgnLegacyRetirement {
 readonly object sync=new object();readonly GmgnImmutableSources sources;
 readonly GmgnLegacyLifetimePins lifetime;readonly GmgnLegacyCapture capture;
 readonly string generation,instance,fixture;string phase="ADMITTING";
 Action beforeAtomicRetireFixtureProbe=null;
 readonly Action authenticationCheck;readonly Action<Action> authenticatedBoundary;
 const string Tools=@"%USERPROFILE%\Desktop\IMC_TRADING_MAIN\TOOLS\";
 public GmgnLegacyRetirement(GmgnImmutableSources sourcePins,GmgnLegacyLifetimePins owner,GmgnLegacyCapture captured,string directory,string fixtureScope)
  :this(sourcePins,owner,captured,directory,fixtureScope,null,null,null){}
 GmgnLegacyRetirement(GmgnImmutableSources sourcePins,GmgnLegacyLifetimePins owner,GmgnLegacyCapture captured,string directory,string fixtureScope,
  Action<PowerShell> injectAuthentication,Action checkAuthentication,Action<Action> authenticationBoundary){
  try{
   authenticationCheck=checkAuthentication;authenticatedBoundary=authenticationBoundary;
   if(sourcePins==null||owner==null||captured==null||sourcePins.GetType()!=typeof(GmgnImmutableSources)||
    owner.GetType()!=typeof(GmgnLegacyLifetimePins)||captured.GetType()!=typeof(GmgnLegacyCapture)||
    sourcePins.GetType().Assembly!=GetType().Assembly||owner.GetType().Assembly!=GetType().Assembly||captured.GetType().Assembly!=GetType().Assembly||
    !captured.MatchesOwner(owner))throw new InvalidOperationException("GMGN_LEGACY_RETIRE_CONTEXT");
   sources=sourcePins;lifetime=owner;capture=captured;fixture=fixtureScope;sources.Check();
   foreach(string name in new string[]{"GmgnLegacyRetirement.cs","GmgnLegacyLifetimePins.cs","GmgnLegacyCapture.cs","GMGN-LegacyReady.ps1","GMGN-ReadSupervisor.ps1"})
    if(!sources.Contains(Tools+name))throw new InvalidOperationException("GMGN_LEGACY_RETIRE_SOURCE_UNPINNED");
   generation=sources.Fingerprint();string path=Tools+"GMGN-LegacyReady.ps1",text=sources.ReadText(path);
   const string invoke=@"param($text,$path,$capture,$owner,$directory,$fixture,$authentication)
$ErrorActionPreference='Stop';$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseInput($text,$path,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'GMGN_LEGACY_RETIRE_VALIDATOR_PARSE'}
. ($ast.GetScriptBlock())
$imcLegacyArguments=@{Capture=$capture;Lifetime=$owner;Directory=$directory;FixtureScope=$fixture}
if($PSBoundParameters.ContainsKey('authentication')){$imcLegacyArguments.Authentication=$authentication}
Assert-GmgnCapturedLegacyReady @imcLegacyArguments";
   try{using(PowerShell ps=PowerShell.Create()){
    ps.AddScript(invoke).AddArgument(text).AddArgument(path).AddArgument(capture).AddArgument(lifetime).AddArgument(directory).AddArgument(fixtureScope);
    if(authenticationCheck!=null)authenticationCheck();
    if(injectAuthentication!=null)injectAuthentication(ps);
    var output=ps.Invoke();
    if(ps.HadErrors||output.Count!=1||!(output[0].BaseObject is string)||!Regex.IsMatch((string)output[0].BaseObject,@"\A[0-9a-f]{32}\z"))
     throw new InvalidOperationException("GMGN_LEGACY_RETIRE_ROLE_INVALID");
    instance=(string)output[0].BaseObject;
   }}catch{throw new InvalidOperationException("GMGN_LEGACY_RETIRE_ROLE_INVALID");}
   if(authenticationCheck!=null)authenticationCheck();sources.Check();lifetime.CheckAlive();phase="ADMITTED";
  }catch{phase="FAULT";throw;}
 }
 public void Retire(long validatedOwnerObservedQpc,long validatedOwnerFrequency){RetireWithBoundary(validatedOwnerObservedQpc,validatedOwnerFrequency,null);}
 internal void RetireWithBoundary(long validatedOwnerObservedQpc,long validatedOwnerFrequency,Action<Action> effectBoundary){RetireWithBoundary(validatedOwnerObservedQpc,validatedOwnerFrequency,effectBoundary,null);}
 internal void RetireWithBoundary(long validatedOwnerObservedQpc,long validatedOwnerFrequency,Action<Action> effectBoundary,Action checkCancellation){lock(sync){
  if(phase!="ADMITTED")throw new InvalidOperationException("GMGN_LEGACY_RETIRE_USED");
  phase="RETIRE_REQUESTED";
  try{
   long now=System.Diagnostics.Stopwatch.GetTimestamp();
   if(validatedOwnerFrequency!=System.Diagnostics.Stopwatch.Frequency||validatedOwnerObservedQpc<=0||validatedOwnerObservedQpc>now)throw new InvalidOperationException("GMGN_LEGACY_OWNER_PROOF_EXPIRED");
   long deadline=checked(validatedOwnerObservedQpc+checked(15*validatedOwnerFrequency));
   if(now>=deadline)throw new InvalidOperationException("GMGN_LEGACY_OWNER_PROOF_EXPIRED");
   sources.Check();if(sources.Fingerprint()!=generation||!capture.MatchesOwner(lifetime))throw new InvalidOperationException("GMGN_LEGACY_RETIRE_CONTEXT");
   if(beforeAtomicRetireFixtureProbe!=null){
    if(!Regex.IsMatch(fixture??"",@"\AC:\\Users\\<local-user>\\Desktop\\IMC_TRADING_MAIN\\[.]local-tools\\legacy-capture-native-[0-9a-f]{32}\z"))throw new InvalidOperationException("GMGN_LEGACY_RETIRE_FIXTURE_FORBIDDEN");
    beforeAtomicRetireFixtureProbe();
   }
   // An already completed Abort takes priority here. This read grants no
   // effect; cancellation and the original deadline are checked at SEND too.
   if(checkCancellation!=null)checkCancellation();
   // Continuous lock order: Controller -> Sources -> optional Authentication
   // -> Capture -> Lifetime.
   // Native retirement cannot run after concurrent source/capture invalidation.
   sources.InHealthyGeneration(delegate{
    Action retire=delegate{capture.RetireAdmittedOwner(lifetime,fixture,deadline,effectBoundary);};
    if(authenticatedBoundary!=null)authenticatedBoundary(retire);else retire();
   });
   if(!lifetime.Retired||!lifetime.ObserveOriginalExits()[0])throw new InvalidOperationException("GMGN_LEGACY_RETIRE_EXIT_UNCONFIRMED");
   phase="RETIRED";
  }catch{phase="FAULT";throw;}
 }}
 public string Phase{get{lock(sync)return phase;}}
 public string GenerationFingerprint{get{return generation;}}
 public string InstanceId{get{return instance;}}
 public bool MatchesContext(GmgnImmutableSources sourcePins,GmgnLegacyLifetimePins owner,GmgnLegacyCapture captured){return Object.ReferenceEquals(sources,sourcePins)&&Object.ReferenceEquals(lifetime,owner)&&Object.ReferenceEquals(capture,captured);}
 internal bool MatchesFixtureScope(string scope){return scope==fixture&&Regex.IsMatch(scope??"",@"\AC:\\Users\\<local-user>\\Desktop\\IMC_TRADING_MAIN\\[.]local-tools\\legacy-capture-native-[0-9a-f]{32}\z");}
}
