# Named-pipe inspection of the independently retained OWN Scheduler child.
# Never attaches a production process or supplies a provider credential.
function Invoke-OwnWarmRetireChild {
 param([string[]]$Native,[string]$Case,[string]$RequestHash)
 $peer=[Management.Automation.Runspaces.RunspaceFactory]::CreateRunspace([Management.Automation.Runspaces.NamedPipeConnectionInfo]::new([int]$Native[0]))
 $peer.Open();$ps=[Management.Automation.PowerShell]::Create();$ps.Runspace=$peer
 try{
  $null=$ps.AddScript({param($case,$requestHash,$native)
   $ErrorActionPreference='Stop';$f=[Reflection.BindingFlags]'Instance,NonPublic,Public'
   $r=Get-Runspace -Id 1;$engine=$r.GetType().GetField('_engine',$f).GetValue($r)
   $context=$engine.GetType().GetField('_context',$f).GetValue($engine);$session=$context.GetType().GetProperty('EngineSessionState',$f).GetValue($context,$null)
   $scope=$session.GetType().GetProperty('GlobalScope',$f).GetValue($session,$null);$vars=$scope.GetType().GetProperty('Variables',$f).GetValue($scope,$null)
   $stage=$vars['IMCGmgnSupervisorStage'].Value
   if(-not $stage -or $stage.Phase -cne 'SOURCE_BOUND' -or ($stage.NativeSelf -join '|') -cne ($native -join '|')){throw 'OWN_RETIRE_CHILD_CONTEXT'}
   $plan=$stage.InstallPlan;$type=$plan.GetType()
   if($case -ceq 'Prepare'){
    if('OwnWarmEndAbort' -as [type]){throw 'OWN_RETIRE_ABORT_HELPER_ALREADY_PRESENT'}
    # Compile before the Root creates its immutable fifteen-second proof.
    # No abort callback is installed and no cancellation is executed here.
    Add-Type -TypeDefinition @'
using System;using System.Reflection;using System.Threading;
public static class OwnWarmEndAbort {
 public static bool Reached,Joined;public static string WorkerCode;public static int Seen;
 static System.Diagnostics.Stopwatch clock;public static string Timing="";
 public static void BeginTiming(){clock=System.Diagnostics.Stopwatch.StartNew();}
 public static Action<string> MakeTiming(){return delegate(string phase){Timing+=phase+"_"+clock.ElapsedMilliseconds+"_";};}
 public static Action MakeAtomic(object target){var end=Make(target,"ATOMIC",1);return delegate(){end("ATOMIC");};}
 public static Action<string> Make(object target,string wanted,int occurrence){return delegate(string phase){
  if(phase!=wanted)return;Seen++;if(Seen!=occurrence)return;Reached=true;
  var worker=new Thread(delegate(){try{target.GetType().GetMethod("Abort").Invoke(target,null);WorkerCode="OWN_ABORT_DONE";}
   catch{WorkerCode="OWN_ABORT_WORKER_FAILED";}});
  worker.IsBackground=true;worker.Start();Joined=worker.Join(3000);
  if(!Joined||WorkerCode!="OWN_ABORT_DONE")throw new InvalidOperationException("OWN_END_ABORT_NOT_JOINED");
 };}
}
'@
    $admission=$plan.CreateWarmValidationAdmission();$admission.Check()
    if($admission.State -cne 'LEGACY_CAPTURED'){throw 'OWN_RETIRE_CHILD_NOT_CAPTURED'}
    $contextClock=[Diagnostics.Stopwatch]::StartNew()
    $null=$admission.GetType().GetMethod('CheckCapturedContext',$f).Invoke($admission,$null)
    [pscustomobject]@{prepared=$true;context_ms=$contextClock.ElapsedMilliseconds}|ConvertTo-Json -Compress
    return
   }
   $admission=$type.GetField('legacyAdmission',$f).GetValue($plan)
   if(-not $admission){throw 'OWN_RETIRE_CHILD_NO_ADMISSION'}
   $life=$admission.GetType().GetField('lifetime',$f).GetValue($admission)
   if($case -ceq 'PlanAbort'){$plan.Abort()}
   if($case -ceq 'Expired'){Start-Sleep -Seconds 16}
   $probeField=$null;$probeOwner=$null;$timingField=$null;$endProbe=$false
   $atomicProbe=$case -cin @('PinAbortAtomic','AdmissionAbortAtomic')
   if($case -cmatch '\A(Pin|Admission)Abort(Common|Captured)(Second)?\z' -or $atomicProbe){
    if($atomicProbe){$phase='ATOMIC';$occurrence=1;$kind=if($case.StartsWith('Pin')){'Pin'}else{'Admission'}}
    else{$endProbe=$true;$phase=if($Matches[2] -ceq 'Common'){'COMMON_END'}else{'CAPTURED_END'};$occurrence=if($Matches[3]){2}else{1};$kind=$Matches[1]}
    $target=if($kind -ceq 'Pin'){$type.GetField('warmPins',$f).GetValue($plan)}else{$type.GetField('warmAdmission',$f).GetValue($plan)}
    if(-not ('OwnWarmEndAbort' -as [type])){throw 'OWN_RETIRE_ABORT_HELPER_NOT_PREPARED'}
    if($atomicProbe){
     [OwnWarmEndAbort]::BeginTiming()
     $timingField=$admission.GetType().GetField('checkpointFixtureProbe',$f)
     $timingField.SetValue($admission,[OwnWarmEndAbort]::MakeTiming())
     $probeOwner=$admission.GetType().GetField('retirement',$f).GetValue($admission)
     $probeField=$probeOwner.GetType().GetField('beforeAtomicRetireFixtureProbe',$f)
     $probeField.SetValue($probeOwner,[OwnWarmEndAbort]::MakeAtomic($target))
    }else{
     $probeOwner=$admission;$probeField=$admission.GetType().GetField('checkpointFixtureProbe',$f)
     $probeField.SetValue($probeOwner,[OwnWarmEndAbort]::Make($target,$phase,$occurrence))
    }
   }
   $guardsVerified=$false
   if($case -ceq 'GuardOnly'){
    Add-Type -TypeDefinition 'using System;public static class OwnWarmProbeGuard{public static int Calls;public static Action<string> Make(){return delegate(string phase){Calls++;};}}'
    $probe=$admission.GetType().GetMethod('OwnCheckpointProbe',$f)
    $field=$admission.GetType().GetField('checkpointFixtureProbe',$f)
    $fixture=$admission.GetType().GetField('fixture',$f);$savedFixture=$fixture.GetValue($admission)
    $published=$type.GetField('legacyAdmission',$f);$savedPublished=$published.GetValue($plan)
    $planLock=$type.GetField('sync',$f).GetValue($plan)
    $phaseLock=$admission.GetType().GetField('retirementSync',$f).GetValue($admission)
    $field.SetValue($admission,[OwnWarmProbeGuard]::Make())
    try{
     foreach($guard in @('MissingFixture','MissingPublication','ForeignAdmission','PlanMonitor','PhaseMonitor')){
      $held=$null
      try{
       switch($guard){
        'MissingFixture'{$fixture.SetValue($admission,$null)}
        'MissingPublication'{$published.SetValue($plan,$null)}
        'ForeignAdmission'{$published.SetValue($plan,[Runtime.Serialization.FormatterServices]::GetUninitializedObject($admission.GetType()))}
        'PlanMonitor'{$held=$planLock;[Threading.Monitor]::Enter($held)}
        'PhaseMonitor'{$held=$phaseLock;[Threading.Monitor]::Enter($held)}
       }
       $guardCode='';try{$null=$probe.Invoke($admission,[object[]]@('COMMON_END'))}catch{$e=$_.Exception;while($e.InnerException){$e=$e.InnerException};$guardCode=$e.Message}
       if($guardCode -cne 'GMGN_WARM_CHECKPOINT_FIXTURE_FORBIDDEN' -or [OwnWarmProbeGuard]::Calls -ne 0){throw 'OWN_PROBE_GUARD_BYPASSED'}
      }finally{if($held){[Threading.Monitor]::Exit($held)};$fixture.SetValue($admission,$savedFixture);$published.SetValue($plan,$savedPublished)}
     }
     $guardsVerified=$true
    }finally{$field.SetValue($admission,$null);$fixture.SetValue($admission,$savedFixture);$published.SetValue($plan,$savedPublished)}
    $admission.Check()
    [pscustomobject]@{guards_verified=$guardsVerified;guard_count=5;callbacks=[OwnWarmProbeGuard]::Calls}|ConvertTo-Json -Compress
    return
   }
   $executionClock=[Diagnostics.Stopwatch]::StartNew()
   $tickField=$type.GetField('checkTimingTicks',$f);$countField=$type.GetField('checkTimingCount',$f)
   $beforeTicks=[long[]]$tickField.GetValue($plan).Clone();$beforeCount=[long]$countField.GetValue($plan)
   $code='';try{$admission.RetireFromRequest($requestHash)}catch{$e=$_.Exception;while($e.InnerException){$e=$e.InnerException};$code=$e.Message}
   finally{if($probeField){$probeField.SetValue($probeOwner,$null)};if($timingField){$timingField.SetValue($admission,$null)}}
   $exits=[bool[]]@($false,$false);$state=$admission.State
   if($case -cin @('Normal','Replay','ProbeGuards')){
    if($code){
     $afterTicks=[long[]]$tickField.GetValue($plan).Clone();$costs=@(0..9|ForEach-Object {[long](1000*($afterTicks[$_]-$beforeTicks[$_])/[Diagnostics.Stopwatch]::Frequency)})
     throw ('OWN_RETIRE_UNEXPECTED_'+$code+'_ELAPSED_MS_'+$executionClock.ElapsedMilliseconds+'_PLAN_COUNT_'+([long]$countField.GetValue($plan)-$beforeCount)+'_PLAN_MS_'+($costs -join '_'))
    }
    $clock=[Diagnostics.Stopwatch]::StartNew()
    do{$admission.Check();$exits=$admission.ObserveRetiredOriginalExits();if(-not ($exits[0] -and $exits[1])){Start-Sleep -Milliseconds 10}}while(-not ($exits[0] -and $exits[1]) -and $clock.Elapsed.TotalSeconds -lt 5)
    $state=$admission.State
    if(-not ($exits[0] -and $exits[1]) -or $state -cne 'BOTH_EXITED'){throw 'OWN_RETIRE_ORIGINAL_EXITS_UNCONFIRMED'}
   }else{
    $wanted=if($endProbe -or $case -ceq 'PlanAbort'){'GMGN_INSTALL_PLAN_FAULT'}elseif($atomicProbe){'GMGN_INSTALL_RETIRE_CANCELLED'}else{'GMGN_STAGE_OWNER_PROOF_STALE'}
    if($code -cne $wanted -or $state -cne 'FAULT'){
     $abortStep=if($atomicProbe -or $endProbe){'REACHED_'+[OwnWarmEndAbort]::Reached+'_JOINED_'+[OwnWarmEndAbort]::Joined+'_COUNT_'+[OwnWarmEndAbort]::Seen+'_'+[OwnWarmEndAbort]::Timing}else{'NO_ABORT_PROBE'}
     $afterTicks=[long[]]$tickField.GetValue($plan).Clone();$costs=@(0..9|ForEach-Object {[long](1000*($afterTicks[$_]-$beforeTicks[$_])/[Diagnostics.Stopwatch]::Frequency)})
     throw ('OWN_RETIRE_WRONG_FAILURE_'+$code+'_'+$abortStep+'_PLAN_COUNT_'+([long]$countField.GetValue($plan)-$beforeCount)+'_PLAN_MS_'+($costs -join '_'))
    }
    $exits=$life.ObserveOriginalExits();if($exits[0] -or $exits[1]){throw 'OWN_RETIRE_FAILURE_SENT'}
    if($endProbe -or $atomicProbe){
     $origin=$type.GetField('warmOrigin',$f).GetValue($plan);$sourceAdmission=$type.GetField('warmAdmission',$f).GetValue($plan)
     # Check the independently faulted original origin as well; Plan's
     # earlier cancellation now intentionally has first error priority.
     $originCode='';try{$null=$origin.GetType().GetMethod('CheckCheckpoint',$f).Invoke($origin,[object[]]@($stage.Sources,$stage.Authentication,$sourceAdmission))}catch{$e=$_.Exception;while($e.InnerException){$e=$e.InnerException};$originCode=$e.Message}
     # The original Warm monitor can already have absorbed this fault before
     # this explicit check. Its exact terminal code is equally fail-closed;
     # the independent three sticky states remain mandatory below.
     if($originCode -cnotin @('GMGN_PIN_BINDING_FAULT','GMGN_SOURCE_ADMISSION_FAULT','GMGN_WARM_ORIGIN_FAULT')){throw 'OWN_ABORT_ORIGIN_INDEPENDENT_FAULT_MISSING'}
     if(-not [OwnWarmEndAbort]::Reached -or -not [OwnWarmEndAbort]::Joined -or [OwnWarmEndAbort]::Seen -ne $occurrence -or [OwnWarmEndAbort]::WorkerCode -cne 'OWN_ABORT_DONE' -or
      $origin.State -cne 'FAULT' -or -not $sourceAdmission.Faulted -or $plan.State -cne 'FAULT'){throw 'OWN_END_ABORT_LATCHES_NOT_PROVEN'}
    }
   }
   $retry='';try{$admission.RetireFromRequest($requestHash)}catch{$e=$_.Exception;while($e.InnerException){$e=$e.InnerException};$retry=$e.Message}
   if($retry -cne 'GMGN_WARM_RETIRE_USED' -or $admission.State -cne $state){throw 'OWN_RETIRE_REPLAY_NOT_BLOCKED'}
   $factory=$type.Assembly.GetType('GmgnReadProcessLifetime').GetField('retained',[Reflection.BindingFlags]'Static,NonPublic').GetValue($null)
   if($factory.Count -or $stage.Retirement -or $stage.Lifetime -or $stage.Capture){throw 'OWN_RETIRE_STAGE_SDK_FALLTHROUGH'}
   $afterTicks=[long[]]$tickField.GetValue($plan).Clone();$costs=@(0..9|ForEach-Object {[long](1000*($afterTicks[$_]-$beforeTicks[$_])/[Diagnostics.Stopwatch]::Frequency)})
   [pscustomobject]@{case=$case;state=$state;code=$code;retry_blocked=$true;end_probe_reached=$endProbe;atomic_probe_reached=$atomicProbe;guards_verified=$guardsVerified;original_broker_exit=$exits[0];original_parent_exit=$exits[1];sdk_factory_count=$factory.Count;provider_calls=0;production_verified=$false;plan_check_count=([long]$countField.GetValue($plan)-$beforeCount);plan_check_ms=$costs}|ConvertTo-Json -Compress
  }).AddArgument($Case).AddArgument($RequestHash).AddArgument($Native)
  $result=@($ps.Invoke());if($ps.HadErrors -or $result.Count -ne 1){throw 'OWN_RETIRE_CHILD_FAILED'}
  return ([string]$result[0]|ConvertFrom-Json)
 }finally{$ps.Dispose();$peer.Dispose()}
}
