// Admission of source continuity only. This grants no order, retirement,
// SDK dispatch, token-transfer or cold-start authority.
using System;using System.Diagnostics;
public sealed class GmgnSupervisorSourceAdmission {
 readonly object sync=new object();readonly GmgnSourcePinBinding pins;
 readonly string manifest,generation;readonly string[] self,digests;bool fault;
 readonly string validatedManifest,validatedGeneration,validatedSchema,validatedFiles,validatedDirectories;
 readonly bool claimsValidated;
 internal GmgnSupervisorSourceAdmission(GmgnSourcePinBinding sourcePins,string manifestPath,
  string generationSha256,string[] nativeSelf,string[] sourceDigests){
  try{
   if(sourcePins==null||sourcePins.GetType().Assembly!=GetType().Assembly||
    nativeSelf==null||nativeSelf.Length!=4||sourceDigests==null||sourceDigests.Length!=3)
    throw new InvalidOperationException("GMGN_SOURCE_ADMISSION_INPUT");
   pins=sourcePins;manifest=manifestPath;generation=generationSha256;
   self=(string[])nativeSelf.Clone();digests=(string[])sourceDigests.Clone();Check();
   lock(sync){validatedManifest=manifest;validatedGeneration=generation;validatedSchema=digests[0];
    validatedFiles=digests[1];validatedDirectories=digests[2];claimsValidated=true;}
  }catch{MarkFault();throw;}
 }
 public void Check(){lock(sync)try{
  if(fault||pins==null)throw new InvalidOperationException("GMGN_SOURCE_ADMISSION_FAULT");
  pins.CheckAdmissionBinding(manifest,generation,digests);
  string[] actual;
  using(var current=Process.GetCurrentProcess())actual=GmgnRepairProcessBinding.Read(current);
  for(int i=0;i<4;i++)if(actual[i]!=self[i])throw new InvalidOperationException("GMGN_SOURCE_ADMISSION_SELF");
  pins.Check();
 }catch{MarkFault();throw;}}
 public string[] ReadBinding(){lock(sync){Check();var result=new string[]{"1",generation,digests[1],digests[2]};Check();return result;}}
 // Cancellation does not change the original pins' fault ownership and
 // deliberately acquires no Pins monitor before the terminal effect leaf.
 void MarkFault(){fault=true;if(pins!=null)pins.SignalRetirementCancellation();}
 public void Abort(){lock(sync)MarkFault();}
 internal bool MatchesPins(GmgnSourcePinBinding value){lock(sync){Check();return Object.ReferenceEquals(pins,value);}}
 internal void CheckLocalState(GmgnSourcePinBinding originalPins){lock(sync)try{
  if(fault||pins==null||!Object.ReferenceEquals(pins,originalPins))throw new InvalidOperationException("GMGN_SOURCE_ADMISSION_FAULT");
  if(self==null||self.Length!=4)throw new InvalidOperationException("GMGN_SOURCE_ADMISSION_SELF");
  string[] actual;using(var current=Process.GetCurrentProcess())actual=GmgnRepairProcessBinding.Read(current);
  for(int i=0;i<4;i++)if(actual[i]!=self[i])throw new InvalidOperationException("GMGN_SOURCE_ADMISSION_SELF");
 }catch{MarkFault();throw;}}
 // Independent scalar claims were captured only after the constructor's
 // complete native validation. Native failures retain first error priority.
 internal string[] ReadValidatedClaims(GmgnSourcePinBinding originalPins){lock(sync)try{
  if(fault||!claimsValidated||!Object.ReferenceEquals(pins,originalPins))throw new InvalidOperationException("GMGN_SOURCE_ADMISSION_FAULT");
  pins.Check();
  string[] result=CheckClaimScalars(originalPins);pins.Check();return result;
 }catch{MarkFault();throw;}}
 // Called only inside independently retained native FIRST/FINAL checks.
 // No shared Pins monitor is acquired here; this is never native health.
 internal string[] CheckClaimScalars(GmgnSourcePinBinding originalPins){lock(sync)try{
  if(fault||!claimsValidated||!Object.ReferenceEquals(pins,originalPins))throw new InvalidOperationException("GMGN_SOURCE_ADMISSION_FAULT");
  // A changed private scalar faults this admission, not shared native pins.
  // Native pin failures above keep their original pin fault ownership.
  if(manifest!=validatedManifest)throw new InvalidOperationException("GMGN_PIN_BINDING_MANIFEST");
  if(generation!=validatedGeneration)throw new InvalidOperationException("GMGN_SOURCE_ADMISSION_GENERATION");
  if(digests==null||digests.Length!=3)throw new InvalidOperationException("GMGN_SOURCE_ADMISSION_INPUT");
  if(digests[0]!=validatedSchema||digests[1]!=validatedFiles||digests[2]!=validatedDirectories)throw new InvalidOperationException("GMGN_SOURCE_ADMISSION_DIGEST");
  CheckLocalState(originalPins);
  return new string[]{validatedSchema,validatedGeneration,validatedFiles,validatedDirectories};
 }catch{MarkFault();throw;}}
 public bool Faulted{get{lock(sync)return fault;}}
}
