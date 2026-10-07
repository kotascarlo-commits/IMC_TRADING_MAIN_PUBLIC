// Native source and namespace handover vector. Contains only public metadata.
using System;using System.Collections.Generic;using System.Security.Cryptography;using System.Text;
public sealed class GmgnSourcePinBinding {
 readonly object sync=new object();readonly GmgnImmutableSources sources;
 readonly Action retirementCancellation;
 readonly List<GmgnReadDirectoryIdentity> directories=new List<GmgnReadDirectoryIdentity>();
 readonly string[] directoryPaths,directoryIdentities;
 readonly string fileVector,fileDigest,directoryVector,directoryDigest;bool fault;
 static readonly List<GmgnSourcePinBinding> retained=new List<GmgnSourcePinBinding>();
 static string Quote(string value){return "\""+value.Replace("\\","\\\\").Replace("\"","\\\"")+"\"";}
 static string Hash(string value){using(var sha=SHA256.Create())return BitConverter.ToString(sha.ComputeHash(new UTF8Encoding(false,true).GetBytes(value))).Replace("-","").ToLowerInvariant();}
 public GmgnSourcePinBinding(GmgnImmutableSources generation):this(generation,null){}
 internal GmgnSourcePinBinding(GmgnImmutableSources generation,Action cancelRetirement){
  retirementCancellation=cancelRetirement;
  lock(retained)retained.Add(this);
  try{
   if(generation==null||generation.GetType()!=typeof(GmgnImmutableSources)||
    typeof(GmgnImmutableSources).Assembly!=GetType().Assembly||typeof(GmgnReadDirectoryIdentity).Assembly!=GetType().Assembly)throw new InvalidOperationException("GMGN_PIN_BINDING_SOURCE");
   sources=generation;sources.Check();fileVector=sources.NativeInventoryJson();fileDigest=Hash(fileVector);
   string[] leaves=sources.ReadNamespaceLeaves();
   if(leaves.Length==0||leaves.Length>2048)throw new InvalidOperationException("GMGN_PIN_BINDING_NAMESPACES");
   var unique=new HashSet<string>(StringComparer.OrdinalIgnoreCase);
   foreach(string path in leaves){if(path==null||!unique.Add(path))throw new InvalidOperationException("GMGN_PIN_BINDING_NAMESPACES");directories.Add(new GmgnReadDirectoryIdentity(path));}
   // Populate only here, after all native directories have been constructed,
   // preserving the original Check -> Path -> Identity capture order.
   directoryPaths=new string[directories.Count];directoryIdentities=new string[directories.Count];
   var initial=new StringBuilder("{\"schema_version\":1,\"directories\":[");
   for(int i=0;i<directories.Count;i++){
    GmgnReadDirectoryIdentity item=directories[i];item.Check();
    directoryPaths[i]=item.Path;directoryIdentities[i]=item.Identity;
    AppendDirectory(initial,i,directoryPaths[i],directoryIdentities[i]);
   }
   directoryVector=initial.Append("]}").ToString();directoryDigest=Hash(directoryVector);Check();
  }catch{MarkFault();throw;}
 }
 string ReadDirectoryVector(){
  var text=new StringBuilder("{\"schema_version\":1,\"directories\":[");
  for(int i=0;i<directories.Count;i++){
   GmgnReadDirectoryIdentity item=directories[i];item.Check();
   AppendDirectory(text,i,directoryPaths[i],directoryIdentities[i]);
   // Retain a complete native check after consuming each cached snapshot.
   item.Check();
  }
  return text.Append("]}").ToString();
 }
 static void AppendDirectory(StringBuilder text,int index,string path,string identity){
  if(index>0)text.Append(',');
  text.Append("{\"path\":").Append(Quote(path)).Append(",\"native_identity\":").Append(Quote(identity)).Append('}');
 }
 public void Check(){lock(sync)try{
  if(fault||sources==null||fileVector==null||directoryVector==null)throw new InvalidOperationException("GMGN_PIN_BINDING_FAULT");
  // Sources.Check validates every held file's original identity/path/length
  // and namespace. Inventory fields have no post-construction mutator;
  // serializing them again adds two redundant native generation walks.
  sources.Check();if(ReadDirectoryVector()!=directoryVector)throw new InvalidOperationException("GMGN_PIN_BINDING_CHANGED");sources.Check();
 }catch{MarkFault();throw;}}
 public string[] ReadDigests(){lock(sync){Check();var value=new string[]{"1",fileDigest,directoryDigest};Check();return value;}}
 // A native checkpoint plus comparison, never a dispatch/installation grant.
 // Caller retains its Auth/error ordering and its final native pin checkpoint.
 internal bool MatchDigests(string expectedFiles,string expectedDirectories){lock(sync){
  Check();return fileDigest==expectedFiles&&directoryDigest==expectedDirectories;
 }}
 public string[] ReadNativeVectors(){lock(sync){Check();var value=new string[]{fileVector,directoryVector};Check();return value;}}
 public string ReadManifestHash(string manifestPath){lock(sync)try{
  Check();if(manifestPath==null||System.IO.Path.GetFullPath(manifestPath)!=manifestPath||
   System.IO.Path.GetFileName(manifestPath)!="package.json")throw new InvalidOperationException("GMGN_PIN_BINDING_MANIFEST");
  string hash=sources.HashForPath(manifestPath);Check();return hash;
 }catch{MarkFault();throw;}}
 // Admission brackets this checkpoint and native Self4 with a final Check.
 // Binding mismatches fault the admission, not shared pins; native/manifest
 // failures retain the existing pin fault ownership and error priority.
 internal void CheckAdmissionBinding(string manifestPath,string generation,string[] expectedDigests){lock(sync){
  if(expectedDigests==null||expectedDigests.Length!=3)throw new InvalidOperationException("GMGN_SOURCE_ADMISSION_INPUT");
  Check();string actualHash;
  try{
   if(manifestPath==null||System.IO.Path.GetFullPath(manifestPath)!=manifestPath||
    System.IO.Path.GetFileName(manifestPath)!="package.json")throw new InvalidOperationException("GMGN_PIN_BINDING_MANIFEST");
   actualHash=sources.HashForPath(manifestPath);
  }catch{MarkFault();throw;}
  if(actualHash!=generation)throw new InvalidOperationException("GMGN_SOURCE_ADMISSION_GENERATION");
  if(expectedDigests[0]!="1"||expectedDigests[1]!=fileDigest||expectedDigests[2]!=directoryDigest)
   throw new InvalidOperationException("GMGN_SOURCE_ADMISSION_DIGEST");
 }}
 // The sole non-null production binding is the original WarmPlan's terminal
 // MarkFault. This callback takes no Source/Admission/Plan monitor or waits.
 internal void SignalRetirementCancellation(){if(retirementCancellation!=null)retirementCancellation();}
 void MarkFault(){fault=true;SignalRetirementCancellation();}
 public void Abort(){lock(sync)MarkFault();}
 internal bool MatchesSources(GmgnImmutableSources value){lock(sync){Check();return Object.ReferenceEquals(sources,value);}}
 // A complete native checkpoint of every original directory/file pin.
 // Reference comparison does not grant admission or retirement authority.
 internal bool CheckOriginalSource(GmgnImmutableSources value){lock(sync){Check();return Object.ReferenceEquals(sources,value);}}
 public bool Faulted{get{lock(sync)return fault;}}
}
