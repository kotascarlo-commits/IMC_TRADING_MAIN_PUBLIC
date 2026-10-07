// Optional strongly typed local IPC authentication for the existing native
// legacy role validator. No provider credentials or new retirement authority.
using System;using System.Management.Automation;
public sealed partial class GmgnLegacyRetirement {
 public static GmgnLegacyRetirement CreateAuthenticated(GmgnImmutableSources sourcePins,GmgnLegacyLifetimePins owner,
  GmgnLegacyCapture captured,string directory,string fixtureScope,GmgnReadLocalAuth authentication){
  if(sourcePins==null||authentication==null||sourcePins.GetType().Assembly!=typeof(GmgnLegacyRetirement).Assembly||
   authentication.GetType().Assembly!=typeof(GmgnLegacyRetirement).Assembly)throw new InvalidOperationException("GMGN_LEGACY_AUTH_CONTEXT");
  sourcePins.Check();authentication.Check();
  foreach(string name in new string[]{"GmgnReadLocalAuth.cs","GmgnLegacyRetirement.Auth.cs"})
   if(!sourcePins.Contains(Tools+name))throw new InvalidOperationException("GMGN_LEGACY_AUTH_SOURCE");
  var result=new GmgnLegacyRetirement(sourcePins,owner,captured,directory,fixtureScope,
   delegate(PowerShell ps){ps.AddArgument(authentication);},authentication.Check,authentication.InHealthyAuthentication);
  sourcePins.Check();authentication.Check();return result;
 }
}
