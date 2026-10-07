param([string]$Workspace,[string]$Python,[ValidateSet('assign','resume','start_wait','start_terminate','job_query','job_terminate','job_wait','job_timeout','active_timeout','late_query','close','release_close','member_query','member_moredata','member_open','member_api','member_not_owned','member_reject_close')][string]$Fault)
$ErrorActionPreference='Stop'
# Compile an isolated fault fixture. No running helper or IMC source is patched.
$source=Get-Content -Raw -Encoding UTF8 (Join-Path (Split-Path -Parent $PSScriptRoot) 'TOOLS\IMC_RUNTIME_JOB.ps1')
$match=[regex]::Match($source,"(?s)@'\r?\n(.*?)\r?\n'@")
if (-not $match.Success) { throw 'fixture_source_missing' }
$code=$match.Groups[1].Value
function Inject([string]$Before,[string]$After) {
    if ([regex]::Matches($script:code,[regex]::Escape($Before)).Count -ne 1) { throw 'fixture_injection_anchor_changed' }
    $script:code=$script:code.Replace($Before,$After)
}
switch($Fault) {
    'assign' { Inject 'if(!AssignProcessToJobObject(job,child.Process))' 'if(DateTime.UtcNow.Ticks>0)' }
    'resume' { Inject 'if(ResumeThread(child.Thread)==0xffffffff)' 'if(DateTime.UtcNow.Ticks>0)' }
    'start_wait' {
        Inject 'if(!AssignProcessToJobObject(job,child.Process))' 'if(DateTime.UtcNow.Ticks>0)'
        Inject 'uint wait=WaitForSingleObject(process,milliseconds);' 'uint wait=FixtureWait(process,milliseconds);'
    }
    'start_terminate' {
        Inject 'if(!AssignProcessToJobObject(job,child.Process))' 'if(DateTime.UtcNow.Ticks>0)'
        Inject 'if(!TerminateProcess(child.Process,126))' 'if(!FixtureTerminate(child.Process,126))'
    }
    'job_query' { Inject 'if(!QueryInformationJobObject(job,1,out info,(uint)Marshal.SizeOf(typeof(Accounting)),IntPtr.Zero))' 'if(DateTime.UtcNow.Ticks>0)' }
    'job_terminate' { Inject 'if(intactJob && !TerminateJobObject(job,126))' 'if(DateTime.UtcNow.Ticks>0)' }
    'job_wait' {
        Inject 'uint wait=WaitForSingleObject(process,milliseconds);' 'uint wait=FixtureWait(process,milliseconds);'
    }
    'job_timeout' { Inject 'uint wait=WaitForSingleObject(process,milliseconds);' 'uint wait=FixtureWait(process,milliseconds);' }
    'active_timeout' { Inject 'QueryInformationJobObject(job,1,out info,' 'FixtureQuery(job,1,out info,' }
    'late_query' { Inject 'QueryInformationJobObject(job,1,out info,' 'FixtureQuery(job,1,out info,' }
    'close' { Inject 'if(!CloseHandle(handle))' 'if(!FixtureClose(handle,child))' }
    'release_close' { Inject 'if(!CloseHandle(handle))' 'if(!FixtureClose(handle,child))' }
    'member_query' {Inject 'bool queried=QueryJobProcessIds(job,3,data,(uint)length,IntPtr.Zero);' 'bool queried=FixtureIds(job,3,data,(uint)length,IntPtr.Zero);'}
    'member_moredata' {Inject 'bool queried=QueryJobProcessIds(job,3,data,(uint)length,IntPtr.Zero);' 'bool queried=FixtureIds(job,3,data,(uint)length,IntPtr.Zero);'}
    'member_open' {Inject 'member.Process=OpenProcess(0x101000,false,(uint)id);' 'member.Process=FixtureOpen(0x101000,false,(uint)id);'}
    'member_api' {Inject 'if(!IsProcessInJob(member.Process,job,out belongs))' 'if(!FixtureMembership(member.Process,job,out belongs))'}
    'member_not_owned' {Inject 'if(!IsProcessInJob(member.Process,job,out belongs))' 'if(!FixtureMembership(member.Process,job,out belongs))'}
    'member_reject_close' {
        Inject 'if(!IsProcessInJob(member.Process,job,out belongs))' 'if(!FixtureMembership(member.Process,job,out belongs))'
        Inject 'if(!CloseHandle(handle))' 'if(!FixtureClose(handle,child))'
    }
}
# Query fixture intentionally throws before its out-variable assignment.
if ($Fault -ceq 'job_query') { $code=$code.Replace('Accounting info;','Accounting info=new Accounting();') }
$fixtureMethods=@'
    public static string FixtureMode;
    public static int FixtureCloseCalls, FixtureWaitCalls;
    public static IntPtr FixtureUnknown;
    public static bool FixtureCaptureFaults;
    public static int FixtureIdsCalls;
    [DllImport("kernel32.dll",EntryPoint="SetLastError",SetLastError=true)] static extern void FixtureLastError(uint error);
    static bool FixtureIds(IntPtr handle,int kind,IntPtr data,uint length,IntPtr returned) {
        FixtureIdsCalls++;
        if(FixtureCaptureFaults && FixtureMode=="member_query") {FixtureLastError(5);return false;}
        if(FixtureCaptureFaults && FixtureMode=="member_moredata" && FixtureIdsCalls==1) {
            int capacity=((int)length-8)/IntPtr.Size;
            Marshal.WriteInt32(data,capacity+1);Marshal.WriteInt32(data,4,capacity);return true;
        }
        return QueryJobProcessIds(handle,kind,data,length,returned);
    }
    static IntPtr FixtureOpen(uint access,bool inherit,uint id) {
        if(FixtureCaptureFaults) {FixtureLastError(5);return IntPtr.Zero;}
        return OpenProcess(access,inherit,id);
    }
    static bool FixtureMembership(IntPtr process,IntPtr handle,out bool belongs) {
        if(FixtureCaptureFaults) {belongs=false;if(FixtureMode=="member_api") {FixtureLastError(5);return false;}return true;}
        return IsProcessInJob(process,handle,out belongs);
    }
    static bool FixtureClose(IntPtr handle,Child child) {
        if(child!=null && handle==child.Process) {
            if(FixtureUnknown==IntPtr.Zero) FixtureUnknown=handle;
            if(handle==FixtureUnknown) { FixtureCloseCalls++; return false; }
        }
        return CloseHandle(handle);
    }
    static bool FixtureTerminate(IntPtr handle,uint code) { return false; }
    static uint FixtureWait(IntPtr handle,uint milliseconds) {
        FixtureWaitCalls++;
        if(FixtureMode=="start_wait" && milliseconds>0 && FixtureWaitCalls<=2) return 258;
        if(FixtureMode=="job_wait") return milliseconds==0 ? 258u : 0xffffffffu;
        if(FixtureMode=="job_timeout") {
            if(milliseconds>0) System.Threading.Thread.Sleep((int)milliseconds+40);
            return 258;
        }
        return WaitForSingleObject(handle,milliseconds);
    }
    static bool FixtureQuery(IntPtr handle,int kind,out Accounting data,uint length,IntPtr returned) {
        bool result=QueryInformationJobObject(handle,kind,out data,length,returned);
        if(FixtureMode=="active_timeout") data.Active=1;
        if(FixtureMode=="late_query") System.Threading.Thread.Sleep(3200);
        return result;
    }
'@
Inject 'static readonly List<IMCRuntimeJob> retained' ($fixtureMethods+"`n    static readonly List<IMCRuntimeJob> retained")
Add-Type -TypeDefinition $code
[IMCRuntimeJob]::FixtureMode=$Fault
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class IMCFixtureNative {
    [StructLayout(LayoutKind.Sequential)] public struct Accounting {
        public long User, Kernel, PeriodUser, PeriodKernel;
        public uint PageFaults, Total, Active, Terminated;
    }
    [DllImport("kernel32.dll")] public static extern bool TerminateProcess(IntPtr process,uint code);
    [DllImport("kernel32.dll")] public static extern bool TerminateJobObject(IntPtr job,uint code);
    [DllImport("kernel32.dll")] public static extern uint WaitForSingleObject(IntPtr process,uint ms);
    [DllImport("kernel32.dll")] public static extern bool CloseHandle(IntPtr handle);
    [DllImport("kernel32.dll",SetLastError=true)] static extern bool QueryInformationJobObject(IntPtr job,int type,out Accounting data,uint length,IntPtr returned);
    public static uint Active(IntPtr job) {
        Accounting info;
        if(!QueryInformationJobObject(job,1,out info,(uint)Marshal.SizeOf(typeof(Accounting)),IntPtr.Zero))
            throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
        return info.Active;
    }
}
'@
$flags=[Reflection.BindingFlags]'Instance,NonPublic'
$job=[IMCRuntimeJob]::new()
$parent=$null; $descendant=$null
try {
    if ($Fault -ceq 'release_close') {
        $finished=$job.Start($Python,[string[]]@((Join-Path $Workspace 'args.py')),$Workspace)
        $wait=[Diagnostics.Stopwatch]::StartNew()
        while ($job.Running($finished) -and $wait.ElapsedMilliseconds -lt 6000) { Start-Sleep -Milliseconds 10 }
        if ($job.Running($finished)) { throw 'fixture_completed_child_not_ended' }
    }
    $startFailed=$false
    try { $ownPid=$job.Start($Python,[string[]]@((Join-Path $Workspace 'tree.py')),$Workspace) } catch { $startFailed=$true }
    $children=[IMCRuntimeJob].GetField('children',$flags).GetValue($job)
    if ($Fault -in @('assign','resume')) {
        if (-not $startFailed -or $children.Count -ne 0 -or $job.ActiveProcessCount() -ne 0) { throw 'failed_start_not_cleaned' }
        $job.Dispose()
        if (-not $job.CleanupConfirmed) { throw 'failed_start_cleanup_unconfirmed' }
    } elseif ($Fault -in @('start_wait','start_terminate')) {
        if (-not $startFailed -or $children.Count -ne 1 -or $job.CleanupConfirmed) { throw 'failed_start_handle_not_retained' }
        $child=$children[0]; $type=$child.GetType()
        $handle=[IntPtr]$type.GetField('Process').GetValue($child)
        if ($handle -eq [IntPtr]::Zero -or [IntPtr]$type.GetField('Thread').GetValue($child) -eq [IntPtr]::Zero -or
            [IntPtr]$type.GetField('Stream').GetValue($child) -eq [IntPtr]::Zero) { throw 'failed_start_handle_closed' }
        $failed=$false
        try { $job.Release([int]$type.GetField('Id').GetValue($child)) } catch { $failed=$true }
        if (-not $failed -or $children.Count -ne 1) { throw 'partial_start_release_lost_handles' }
        $failed=$false
        try { $null=$job.Start($Python,[string[]]@(),$Workspace) } catch { $failed=$true }
        if (-not $failed -or $children.Count -ne 1) { throw 'faulted_start_not_blocked' }
        if ($Fault -ceq 'start_terminate') {
            # A failed Assign leaves this native suspended child OUTSIDE the job.
            if ($job.ActiveProcessCount() -ne 0 -or [IMCFixtureNative]::WaitForSingleObject($handle,0) -ne 258) {
                throw 'outside_job_child_not_proved'
            }
            $parent=Get-Process -Id ([int]$type.GetField('Id').GetValue($child)) -ErrorAction Stop
            $null=$parent.Handle
            # The helper, not fixture teardown, must kill this unassigned child.
            $job.ShutdownWithin(3000)
            if (-not $parent.WaitForExit(0)) { throw 'helper_unassigned_child_still_alive' }
        } else {
            if ([IMCFixtureNative]::WaitForSingleObject($handle,3000) -ne 0) { throw 'fixture_native_exit_unconfirmed' }
            if ([IMCRuntimeJob]::FixtureWaitCalls -lt 2) { throw 'start_wait_fault_not_exercised' }
            $job.ShutdownWithin(3000)
        }
        if (-not $job.CleanupConfirmed) { throw 'retained_start_cleanup_unconfirmed' }
    } else {
        if ($startFailed) { throw 'unexpected_fixture_start_failure' }
        $deadline=[Diagnostics.Stopwatch]::StartNew()
        while (-not (Test-Path -LiteralPath (Join-Path $Workspace 'child.pid')) -and $deadline.ElapsedMilliseconds -lt 6000) {
            Start-Sleep -Milliseconds 20
        }
        $parent=Get-Process -Id ([int](Get-Content -LiteralPath (Join-Path $Workspace 'parent.pid'))) -ErrorAction Stop
        $descendant=Get-Process -Id ([int](Get-Content -LiteralPath (Join-Path $Workspace 'child.pid'))) -ErrorAction Stop
        $null=$parent.Handle; $null=$descendant.Handle
        $job.ObserveMember($descendant.Id)
        if ($Fault -ceq 'release_close') {
            $failed=$false
            try { $job.Release($finished) } catch { $failed=$true }
            if (-not $failed -or $parent.HasExited -or $descendant.HasExited) { throw 'release_close_fault_missing' }
        }
        $failed=$false
        $budget=[Diagnostics.Stopwatch]::StartNew()
        [IMCRuntimeJob]::FixtureCaptureFaults=$true
        try { $job.ShutdownWithin(3000) } catch { $failed=$true }
        if ($Fault -ceq 'member_moredata') {
            if ($failed -or -not $job.CleanupConfirmed -or [IMCRuntimeJob]::FixtureIdsCalls -ne 2 -or
                -not $parent.WaitForExit(0) -or -not $descendant.WaitForExit(0)) {throw 'native_member_buffer_growth_failed'}
        } else {
        if (-not $failed -or $job.CleanupConfirmed) { throw 'unconfirmed_shutdown_accepted' }
        $nativeJob=[IntPtr][IMCRuntimeJob].GetField('job',$flags).GetValue($job)
        if ($nativeJob -eq [IntPtr]::Zero -or $children.Count -eq 0) { throw 'unknown_cleanup_handles_lost' }
        if ($Fault -ceq 'job_terminate' -and ($parent.HasExited -or $descendant.HasExited -or $job.ActiveProcessCount() -lt 2)) {
            throw 'failed_terminate_released_running_children'
        }
        if ($Fault -in @('job_timeout','active_timeout','late_query') -and $budget.ElapsedMilliseconds -lt 2900) {
            throw 'shutdown_budget_not_exercised'
        }
        if ($Fault -in @('job_wait','job_timeout') -and [IMCRuntimeJob]::FixtureWaitCalls -lt 2) {
            throw 'native_wait_failure_not_exercised'
        }
        if ($Fault -in @('close','release_close','member_reject_close')) {
            $first=[IMCRuntimeJob]::FixtureUnknown
            if ([IMCFixtureNative]::WaitForSingleObject($first,0) -ne 0) { throw 'known_closed_child_handle_missing' }
            if (-not $parent.WaitForExit(0) -or -not $descendant.WaitForExit(0) -or $job.ActiveProcessCount() -ne 0) {
                throw 'close_fault_prevented_other_child_shutdown'
            }
            if ([IMCRuntimeJob]::FixtureCloseCalls -ne 1) { throw 'unknown_close_call_count_wrong' }
            $failed=$false
            try { $job.Dispose() } catch { $failed=$true }
            if (-not $failed -or $job.CleanupConfirmed -or [IMCRuntimeJob]::FixtureCloseCalls -ne 1 -or
                [IMCFixtureNative]::WaitForSingleObject($first,0) -ne 0) {
                throw 'sticky_close_fault_lost_or_retried'
            }
        }
        if ($Fault -in @('member_query','member_open','member_api','member_not_owned')) {
            if (-not $parent.WaitForExit(0) -or -not $descendant.WaitForExit(0) -or $job.ActiveProcessCount() -ne 0) {
                throw 'capture_failure_prevented_best_effort_shutdown'
            }
            $failed=$false
            try {$job.Dispose()} catch {$failed=$true}
            if (-not $failed -or $job.CleanupConfirmed) {throw 'capture_gap_forgotten_on_empty_retry'}
        }
        }
    }
    Write-Output ('runtime native failure checks passed: '+$Fault)
} finally {
    # Only this fixture's held native objects; unknown production handles are
    # never retried. External native probes provide fixture teardown evidence.
    $nativeJob=[IntPtr][IMCRuntimeJob].GetField('job',$flags).GetValue($job)
    if ($nativeJob -ne [IntPtr]::Zero) {
        if (-not [IMCFixtureNative]::TerminateJobObject($nativeJob,126)) { throw 'fixture_job_termination_failed' }
        $exitBudget=[Diagnostics.Stopwatch]::StartNew()
        while ([IMCFixtureNative]::Active($nativeJob) -ne 0 -and $exitBudget.ElapsedMilliseconds -lt 3000) {
            Start-Sleep -Milliseconds 10
        }
        if ([IMCFixtureNative]::Active($nativeJob) -ne 0) { throw 'fixture_native_job_end_unconfirmed' }
    }
    $members=[IMCRuntimeJob].GetField('members',$flags).GetValue($job)
    foreach($child in (@($children)+@($members))) {
        $t=$child.GetType(); $h=[IntPtr]$t.GetField('Process').GetValue($child)
        if ($h -ne [IntPtr]::Zero) {
            if ([IMCFixtureNative]::WaitForSingleObject($h,0) -eq 258) { $null=[IMCFixtureNative]::TerminateProcess($h,126) }
            if ([IMCFixtureNative]::WaitForSingleObject($h,3000) -ne 0) { throw 'fixture_process_cleanup_unconfirmed' }
        }
        foreach($name in @('Thread','Stream','Process')) {
            $h=[IntPtr]$t.GetField($name).GetValue($child)
            if ($h -ne [IntPtr]::Zero -and -not [IMCFixtureNative]::CloseHandle($h)) { throw 'fixture_handle_cleanup_failed' }
        }
    }
    if ($parent) { if (-not $parent.WaitForExit(3000)) { throw 'fixture_parent_still_alive' }; $parent.Dispose() }
    if ($descendant) { if (-not $descendant.WaitForExit(3000)) { throw 'fixture_descendant_still_alive' }; $descendant.Dispose() }
    if ($nativeJob -ne [IntPtr]::Zero -and -not [IMCFixtureNative]::CloseHandle($nativeJob)) { throw 'fixture_job_close_failed' }
}
