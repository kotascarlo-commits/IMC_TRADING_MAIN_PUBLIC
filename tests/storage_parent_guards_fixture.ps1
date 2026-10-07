param([string]$Workspace,[string]$Python,[ValidateSet('Normal','Bounds','Unsigned','Duplicate','Hardlink','CloseFault','WrongStrong','Concurrency','RenameAssert','RenameMember','RenameSentinel','RenamePaths','UnpinnedStrong','LateUnpinned','ClosedThenException','PartialStrong','Junction','ReparseHeld','RebindPhases','RebindStale','RebindWrong','RebindLate','RebindUnpinned','RebindIdentity','RebindStrong','Scopes','ScopesEmpty','ScopesOutside')][string]$Case='Normal')
$ErrorActionPreference='Stop'
$Repo=Split-Path -Parent $PSScriptRoot
. (Join-Path $Repo 'TOOLS\IMC_STORAGE_GUARDS.ps1')
if($Case -ceq 'Unsigned') {
    $id=[byte[]]::new(16);$id[15]=128
    [IMCStorageGuards]::IdentityJSON([uint64]::MaxValue,$id,7)
    $id=[byte[]](0..15|ForEach-Object {255})
    [IMCStorageGuards]::IdentityJSON([uint64]::MaxValue,$id,9)
    exit 0
}
$group=[IMCStorageGuards]::new($Workspace,('a'*32),30)
if($Case.StartsWith('Scopes')) {
    if($Case -cne 'Scopes') {
        $scopes=[string[]]@()
        if($Case -ceq 'ScopesOutside'){$scopes=[string[]]@(Split-Path -Parent $Workspace)}
        $expectedError=if($Case -ceq 'ScopesEmpty'){'STORAGE_ACQUIRE_INVALID'}else{'STORAGE_PATH_OUTSIDE_ROOT'}
        try{$group.AcquireScopes($scopes);throw 'fixture_invalid_scopes_admitted'}
        catch{if($_.Exception.GetBaseException().Message -cne $expectedError){throw}}
        if(-not $group.Faulted -or $group.Closed -or [IMCStorageGuards]::RetainedCount -ne 1){throw 'fixture_scopes_fault_missing'}
        Write-Output 'INVALID_SCOPES_RETAINED';exit 0
    }
    $sourceOnly=[IMCStorageGuards]::new($Workspace,('a'*32),30)
    $sourceOnly.Acquire([string[]]@('source'),[string[]]@(Join-Path $Workspace 'source\imc-main.db'),[string[]]@())
    $group.AcquireScopes([string[]]@(Join-Path $Workspace 'journal'))
    $files=[IMCStorageGuards].GetField('files',[Reflection.BindingFlags]'Instance,NonPublic').GetValue($group)
    if($files.Count -ne 0){throw 'fixture_scope_dummy_file'}
    $sentinel=$group.SentinelPaths[0]
    $sourceOnly.Close();$group.AssertAll()
    if(-not $sourceOnly.Closed -or $group.Closed -or [IMCStorageGuards]::RetainedCount -ne 1){throw 'fixture_groups_not_independent'}
    try{[IO.File]::Move($sentinel,($sentinel+'.moved'));throw 'fixture_scope_sentinel_not_held'}catch [IO.IOException]{}
    try{[IO.Directory]::Move((Join-Path $Workspace 'journal'),(Join-Path $Workspace 'moved'));throw 'fixture_scope_directory_not_held'}catch [IO.IOException]{}
    $group.AssertAll();$group.Close()
    if(-not $group.Closed -or [IMCStorageGuards]::RetainedCount -ne 0){throw 'fixture_scope_cleanup_unconfirmed'}
    Write-Output 'INDEPENDENT_SCOPE_ONLY_LIFETIME_PASS';exit 0
}
if($Case -ceq 'Bounds') {
    try {$group.Acquire([string[]]@('outside'),[string[]]@(Join-Path (Split-Path -Parent $Workspace) 'outside.db'),[string[]]@())}
    catch {if(-not $group.Faulted){throw 'fixture_fault_not_retained'};Write-Output 'BOUND_REJECTED_RETAINED';exit 0}
    throw 'fixture_outside_admitted'
}
$source=Join-Path $Workspace 'source\imc-main.db';$work=Join-Path $Workspace 'work\imc-main.db'
$archive=Join-Path $Workspace 'archive\imc-main.db';$journal=Join-Path $Workspace 'journal'
if($Case -ceq 'Junction') {
    try{$group.Acquire([string[]]@('source_main'),[string[]]@($source),[string[]]@($Workspace))}
    catch {
        if($_.Exception.Message -notmatch 'STORAGE_MEMBER_SHAPE'){throw}
        $files=[IMCStorageGuards].GetField('files',[Reflection.BindingFlags]'Instance,NonPublic').GetValue($group)
        if($files.Count -ne 0 -or -not $group.Faulted -or [IMCStorageGuards]::RetainedCount -ne 1){throw 'fixture_junction_followed'}
        Write-Output 'JUNCTION_REJECTED_BEFORE_MEMBER_OPEN';exit 0
    }
    throw 'fixture_junction_admitted'
}
if($Case -cin @('Duplicate','Hardlink')) {
    $caseKeys=[string[]]@('source_main','source_main')
    $casePaths=[string[]]@($source,$work)
    if($Case -ceq 'Hardlink'){$caseKeys=[string[]]@('source_main');$casePaths=[string[]]@($source)}
    try{$group.Acquire($caseKeys,$casePaths,[string[]]@($Workspace))}
    catch {
        if(-not $group.Faulted -or $group.Closed -or [IMCStorageGuards]::RetainedCount -ne 1){throw 'fixture_partial_not_retained'}
        try{$group.Close();throw 'fixture_fault_close_allowed'}catch{if($_.Exception.Message -notmatch 'STORAGE_CLOSE_UNCONFIRMED'){throw}}
        Write-Output 'PARTIAL_ACQUIRE_RETAINED';exit 0
    }
    throw 'fixture_bad_acquire_admitted'
}
$paths=[string[]]@($source,($source+'-wal'),($source+'-shm'),$work)
$keys=[string[]]@('source_main','source_wal','source_shm','work_main')
$old=@();foreach($path in $paths){$old+=[IO.File]::Open($path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)}
$group.Acquire($keys,$paths,[string[]]@($Workspace,(Split-Path -Parent $archive),$journal))
if($Case.StartsWith('Rebind')) {
    foreach($stream in $old){$stream.Dispose()};$old=@()
    $allField=[IMCStorageGuards].GetField('all',[Reflection.BindingFlags]'Instance,NonPublic')
    $filesField=[IMCStorageGuards].GetField('files',[Reflection.BindingFlags]'Instance,NonPublic')
    $count=$allField.GetValue($group).Count
    $expected=[string[]]$paths.Clone()
    if($Case -ceq 'RebindPhases') {
        # Every independently selected phase is asserted before the next move.
        foreach($index in @(2,1,0,3)) {
            $target=@($archive,($archive+'-wal'),($archive+'-shm'),$source)[$index]
            [IO.File]::Move($expected[$index],$target);$expected[$index]=$target
            $group.RebindWeak($keys,$expected);$group.AssertAll()
            # Repeating the exact proven mapping is harmless and opens nothing.
            $group.RebindWeak($keys,$expected)
            if($allField.GetValue($group).Count -ne $count){throw 'fixture_rebind_opened_handle'}
        }
        $group.TakeStrong($keys,$expected);$group.Close()
        Write-Output 'FOUR_PHASE_REBIND_PASS';exit 0
    }
    switch($Case) {
        'RebindStale' {[IO.File]::Move($source,$archive)}
        'RebindWrong' {$expected[0]=$work;$expected[3]=$source}
        'RebindLate' {
            [IO.File]::Move($source,$archive);$expected[0]=$archive
            $expected[3]=$source # Fourth entry invalid after three valid checks.
        }
        'RebindUnpinned' {
            $target=Join-Path $Workspace 'unpinned\imc-main.db'
            [IO.File]::Move($work,$target);$expected[3]=$target
        }
        'RebindIdentity' {
            $files=$filesField.GetValue($group);$member=$files['work_main']
            $initial=$member.GetType().GetField('Initial',[Reflection.BindingFlags]'Instance,NonPublic').GetValue($member)
            $bytes=$initial.GetType().GetField('Bytes',[Reflection.BindingFlags]'Instance,NonPublic')
            $bytes.SetValue($initial,[long]($bytes.GetValue($initial)+1))
        }
        'RebindStrong' {$group.TakeStrong($keys,$expected);$count=$allField.GetValue($group).Count}
    }
    try{$group.RebindWeak($keys,$expected);throw 'fixture_invalid_rebind_admitted'}
    catch{if($_.Exception.Message -notmatch 'STORAGE_(NATIVE_PATH_CHANGED|REBIND_UNPINNED_DESTINATION|REBIND_IDENTITY|REBIND_INVENTORY)'){throw}}
    if(-not $group.Faulted -or $group.Closed -or $allField.GetValue($group).Count -ne $count){throw 'fixture_rebind_not_retained'}
    $files=$filesField.GetValue($group)
    for($index=0;$index -lt $keys.Length;$index++) {
        $member=$files[$keys[$index]]
        $path=$member.GetType().GetField('Path',[Reflection.BindingFlags]'Instance,NonPublic').GetValue($member)
        if($path -cne $paths[$index]){throw 'fixture_partial_rebind_path_changed'}
    }
    try{$group.Close();throw 'fixture_rebind_fault_close_allowed'}catch{if($_.Exception.Message -notmatch 'STORAGE_CLOSE_UNCONFIRMED'){throw}}
    Write-Output 'INVALID_REBIND_RETAINED_NO_PARTIAL_MAPPING';exit 0
}
if($Case -ceq 'ReparseHeld') {
    $probe=@'
import ctypes,struct,sys
from pathlib import Path
root=Path(sys.argv[1]);api=ctypes.WinDLL('kernel32',use_last_error=True)
api.CreateFileW.argtypes=[ctypes.c_wchar_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p];api.CreateFileW.restype=ctypes.c_void_p
api.CloseHandle.argtypes=[ctypes.c_void_p];api.CloseHandle.restype=ctypes.c_int
api.DeviceIoControl.argtypes=[ctypes.c_void_p,ctypes.c_uint32,ctypes.c_void_p,ctypes.c_uint32,ctypes.c_void_p,ctypes.c_uint32,ctypes.c_void_p,ctypes.c_void_p];api.DeviceIoControl.restype=ctypes.c_int
def convert(path):
    target=root/'redirect';sub=('\\??\\'+str(target)).encode('utf-16-le');display=str(target).encode('utf-16-le')
    names=sub+bytes(2)+display+bytes(2)
    data=struct.pack('<IHHHHHH',0xA0000003,8+len(names),0,0,len(sub),len(sub)+2,len(display))+names
    h=api.CreateFileW(str(path),0x40000000,7,None,3,0x2200000,None)
    if h in (None,ctypes.c_void_p(-1).value):raise RuntimeError('fixture_write_open_failed')
    try:
        buffer=ctypes.create_string_buffer(data);count=ctypes.c_uint32()
        result=api.DeviceIoControl(h,0x900A4,buffer,len(data),None,0,ctypes.byref(count),None)
        error=ctypes.get_last_error()
        return bool(result),error
    finally:
        if not api.CloseHandle(h):raise RuntimeError('fixture_probe_close_failed')
ok,error=convert(root/'control')
if not ok:raise RuntimeError('fixture_unprotected_control_failed_'+str(error))
ok,error=convert(root/'archive')
if ok or error!=145:raise RuntimeError('fixture_protected_sentinel_not_proven_'+str(error))
print('UNPROTECTED_JUNCTION_SUCCESS_HELD_SENTINEL_ERROR_145')
'@
    $result=@(& $Python -I -c $probe $Workspace 2>$null)
    if($LASTEXITCODE -ne 0 -or $result -cnotcontains 'UNPROTECTED_JUNCTION_SUCCESS_HELD_SENTINEL_ERROR_145'){throw 'fixture_reparse_control_failed'}
    $group.AssertAll();$group.Close();foreach($stream in $old){$stream.Dispose()};$old=@()
    Write-Output 'HELD_LEAF_REPARSE_PROTECTION_PASS';exit 0
}
if($Case -cin @('RenameAssert','RenameMember','RenameSentinel','RenamePaths','UnpinnedStrong','LateUnpinned')) {
    $knownSentinel=$group.SentinelPaths[0]
    foreach($stream in $old){$stream.Dispose()};$old=@()
    if($Case.StartsWith('Rename')) {
        [IO.File]::Move($source,(Join-Path $Workspace 'foreign.db'))
        try {
            switch($Case) {
                'RenameAssert' {$group.AssertAll()}
                'RenameMember' {$null=$group.MemberJSON('source_main')}
                'RenameSentinel' {$null=$group.SentinelJSON($knownSentinel)}
                # PS5 suppresses exceptions from property syntax. Invoke the
                # actual getter so its native failure is explicitly observable.
                'RenamePaths' {$null=$group.GetType().GetProperty('SentinelPaths').GetValue($group,$null)}
            }
            throw 'fixture_moved_path_admitted'
        }catch{if($_.Exception.Message -notmatch 'STORAGE_NATIVE_PATH_CHANGED'){throw}}
    } else {
        $unknown=Join-Path $Workspace 'unpinned\imc-main.db'
        if($Case -ceq 'LateUnpinned'){[IO.File]::Move($work,$unknown)}else{[IO.File]::Move($source,$unknown)}
        $allField=[IMCStorageGuards].GetField('all',[Reflection.BindingFlags]'Instance,NonPublic')
        $count=$allField.GetValue($group).Count
        $expected=[string[]]@($unknown,($source+'-wal'),($source+'-shm'),$work)
        if($Case -ceq 'LateUnpinned'){$expected=[string[]]@($source,($source+'-wal'),($source+'-shm'),$unknown)}
        try{$group.TakeStrong($keys,$expected);throw 'fixture_unpinned_admitted'}
        catch{if($_.Exception.Message -notmatch 'STORAGE_STRONG_UNPINNED_DESTINATION'){throw}}
        if($allField.GetValue($group).Count -ne $count){throw 'fixture_strong_open_before_preflight'}
    }
    if(-not $group.Faulted -or $group.Closed -or [IMCStorageGuards]::RetainedCount -ne 1){throw 'fixture_assert_not_retained'}
    try{$group.Close();throw 'fixture_fault_close_allowed'}catch{if($_.Exception.Message -notmatch 'STORAGE_CLOSE_UNCONFIRMED'){throw}}
    Write-Output 'ASSERT_OR_UNPINNED_RETAINED';exit 0
}
if($Case -ceq 'ClosedThenException') {
    Add-Type -TypeDefinition @'
using System;using System.Reflection;
public static class StorageCloseExceptionFixture {
 public static int Calls;
 public static void Attach(object group) {
  Type type=group.GetType();MethodInfo native=type.GetMethod("CloseHandle",BindingFlags.Static|BindingFlags.NonPublic);
  Func<IntPtr,bool> injected=delegate(IntPtr handle){Calls++;bool closed=(bool)native.Invoke(null,new object[]{handle});
   if(!closed)throw new Exception("fixture_real_close_failed");throw new Exception("fixture_after_actual_close");};
  type.GetField("closeNative",BindingFlags.Instance|BindingFlags.NonPublic).SetValue(group,injected);
 }
}
'@
    [StorageCloseExceptionFixture]::Attach($group)
    try{$group.Close();throw 'fixture_close_exception_admitted'}catch{if($_.Exception.Message -notmatch 'fixture_after_actual_close'){throw}}
    if(-not $group.Faulted -or $group.Closed -or [IMCStorageGuards]::RetainedCount -ne 1){throw 'fixture_close_exception_not_retained'}
    try{$group.Close();throw 'fixture_exception_close_retried'}catch{if($_.Exception.Message -notmatch 'STORAGE_CLOSE_UNCONFIRMED'){throw}}
    try{$group.AssertAll();throw 'fixture_fault_native_inspection_allowed'}catch{if($_.Exception.Message -notmatch 'STORAGE_GROUP_UNCONFIRMED'){throw}}
    if([StorageCloseExceptionFixture]::Calls -ne 1){throw 'fixture_fault_native_handle_reused'}
    Write-Output 'ACTUAL_CLOSED_THEN_INJECTED_EXCEPTION_RETAINED_NO_REUSE';exit 0
}
if($Case -ceq 'Concurrency') {
    Add-Type -TypeDefinition @'
using System;using System.Reflection;using System.Threading;
public static class StorageGuardConcurrentFixture {
 public static string Run(object group) {
  ManualResetEvent start=new ManualResetEvent(false);Exception hashError=null,closeError=null;string hash=null;
  Thread one=new Thread(delegate(){start.WaitOne();try{hash=(string)group.GetType().GetMethod("Hash").Invoke(group,new object[]{"source_main"});}catch(Exception e){hashError=e.GetBaseException();}});
  Thread two=new Thread(delegate(){start.WaitOne();try{group.GetType().GetMethod("Close").Invoke(group,null);}catch(Exception e){closeError=e.GetBaseException();}});
  one.Start();two.Start();start.Set();
  if(!one.Join(10000)||!two.Join(10000))throw new Exception("fixture_threads_unconfirmed");start.Dispose();
  if(closeError!=null)throw new Exception("fixture_close_failed");
  if(hashError!=null && hashError.Message!="STORAGE_GROUP_UNCONFIRMED")throw new Exception("fixture_hash_unclassified");
  return hashError==null?"HASH_THEN_CLOSE":"CLOSE_THEN_HASH_REJECTED";
 }
}
'@
    foreach($stream in $old){$stream.Dispose()};$old=@()
    $result=[StorageGuardConcurrentFixture]::Run($group)
    if(-not $group.Closed -or $group.Faulted -or [IMCStorageGuards]::RetainedCount -ne 0){throw 'fixture_concurrent_state_invalid'}
    Write-Output $result;exit 0
}
if($Case -ceq 'CloseFault') {
    $allField=[IMCStorageGuards].GetField('all',[Reflection.BindingFlags]'Instance,NonPublic')
    $members=$allField.GetValue($group)
    $item=$members[$members.Count-1]
    $field=$item.GetType().GetField('Handle',[Reflection.BindingFlags]'Instance,NonPublic')
    # Test double substitutes ZERO, so CloseHandle never receives the actual
    # retained native file handle. Process exit releases the untouched fixture.
    $saved=$field.GetValue($item);$field.SetValue($item,[IntPtr]::Zero)
    try{$group.Close();throw 'fixture_bad_close_accepted'}catch{if($_.Exception.Message -notmatch 'STORAGE_NATIVE_ERROR_6'){throw}}
    if(-not $group.Faulted -or $group.Closed -or [IMCStorageGuards]::RetainedCount -ne 1){throw 'fixture_close_not_retained'}
    try{$group.Close();throw 'fixture_close_retried'}catch{if($_.Exception.Message -notmatch 'STORAGE_CLOSE_UNCONFIRMED'){throw}}
    $field.SetValue($item,$saved) # No native close was submitted for this handle.
    Write-Output 'NATIVE_CLOSE_FAULT_RETAINED_NO_RETRY';exit 0
}
if($Case -ceq 'WrongStrong') {
    foreach($stream in $old){$stream.Dispose()};$old=@()
    try{$group.TakeStrong($keys,[string[]]@($work,($source+'-wal'),($source+'-shm'),$source));throw 'fixture_wrong_mapping_accepted'}
    catch{if($_.Exception.Message -notmatch 'STORAGE_HANDOFF_PATH'){throw}}
    if(-not $group.Faulted -or $group.Closed){throw 'fixture_wrong_mapping_not_retained'}
    Write-Output 'WRONG_STRONG_MAPPING_RETAINED';exit 0
}
$before=@{};foreach($key in $keys){$before[$key]=$group.Hash($key)}
$pairs=@();foreach($path in $group.SentinelPaths){$pairs+=(($path|ConvertTo-Json -Compress)+':'+$group.SentinelJSON($path))}
[IO.File]::WriteAllText((Join-Path $Workspace 'sentinels.json'),('{'+($pairs -join ',')+'}'),[Text.UTF8Encoding]::new($false))
if($Case -ceq 'PartialStrong') {
    . (Join-Path $Repo 'TOOLS\IMC_RUNTIME_JOB.ps1')
    $job=[IMCRuntimeJob]::new()
    foreach($stream in $old){$stream.Dispose()};$old=@()
    $hold=@'
import json,sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from storage_publish_guard import RenameGuards
root=Path(sys.argv[2]);work=root/'work/imc-main.db'
all_bindings=json.loads((root/'sentinels.json').read_text())
bindings={p:v for p,v in all_bindings.items() if Path(p).parent==work.parent}
with RenameGuards(root,{'work':work},deadline=time.monotonic()+20,nonce='a'*32,attached_sentinels=bindings):
    (root/'work/ready').write_text('native DELETE held')
    time.sleep(20)
'@
    $child=$job.Start($Python,[string[]]@('-I','-c',$hold,(Join-Path $Repo 'scripts'),$Workspace),$Workspace)
    try {
        $wait=[Diagnostics.Stopwatch]::StartNew()
        while(-not [IO.File]::Exists((Join-Path $Workspace 'work\ready')) -and $wait.Elapsed.TotalSeconds -lt 10) {
            if(-not $job.Running($child)){throw 'fixture_partial_worker_failed'};Start-Sleep -Milliseconds 10
        }
        if(-not [IO.File]::Exists((Join-Path $Workspace 'work\ready'))){throw 'fixture_partial_worker_not_ready'}
        $allField=[IMCStorageGuards].GetField('all',[Reflection.BindingFlags]'Instance,NonPublic')
        $previous=$allField.GetValue($group).Count
        try{$group.TakeStrong($keys,$paths);throw 'fixture_partial_strong_admitted'}catch{if($_.Exception.Message -notmatch 'STORAGE_NATIVE_ERROR_32'){throw}}
        $members=$allField.GetValue($group)
        if($members.Count -ne ($previous+3) -or -not $group.Faulted -or $group.Closed){throw 'fixture_partial_strong_not_retained'}
        foreach($item in $members) {
            $state=$item.GetType().GetField('State',[Reflection.BindingFlags]'Instance,NonPublic').GetValue($item)
            if($state.ToString() -cne 'Owned' -and $state.ToString() -cne 'Closed'){throw 'fixture_owned_handle_lost'}
        }
        for($index=$previous;$index -lt $members.Count;$index++) {
            $item=$members[$index]
            if($item.GetType().GetField('State',[Reflection.BindingFlags]'Instance,NonPublic').GetValue($item).ToString() -cne 'Owned') {
                throw 'fixture_partial_strong_not_owned'
            }
        }
        foreach($name in @('files','directories','sentinels')) {
            $current=[IMCStorageGuards].GetField($name,[Reflection.BindingFlags]'Instance,NonPublic').GetValue($group)
            if($name -ceq 'files' -and $current.Count -ne 4){throw 'fixture_original_weak_inventory_lost'}
            foreach($item in $current.Values) {
                if($item.GetType().GetField('State',[Reflection.BindingFlags]'Instance,NonPublic').GetValue($item).ToString() -cne 'Owned') {
                    throw 'fixture_original_weak_or_pins_not_owned'
                }
            }
        }
        # Strong Main survives: a DELETE open is rejected despite the old weak
        # sharing allowing DELETE; no metadata assertion can consume the fault.
        try{[IO.File]::Move($source,(Join-Path $Workspace 'foreign.db'));throw 'fixture_partial_strong_missing'}catch [IO.IOException]{}
        try{$group.Close();throw 'fixture_partial_close_allowed'}catch{if($_.Exception.Message -notmatch 'STORAGE_CLOSE_UNCONFIRMED'){throw}}
        Write-Output 'PARTIAL_STRONG_OWNED_RETAINED'
    } finally {$job.ShutdownWithin(10000);if(-not $job.CleanupConfirmed){throw 'fixture_partial_job_cleanup_unconfirmed'}}
    exit 0
}
$program=@'
import json,sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from storage_publish_guard import RenameGuards
root=Path(sys.argv[2]);source=root/'source/imc-main.db';work=root/'work/imc-main.db';archive=root/'archive/imc-main.db'
members={'source_main':source,'source_wal':Path(str(source)+'-wal'),'source_shm':Path(str(source)+'-shm'),'work_main':work}
destinations=[archive,Path(str(archive)+'-wal'),Path(str(archive)+'-shm'),source,root/'journal/generation.json',root/'binding.json']
try:
    with RenameGuards(root,members,deadline=time.monotonic()+20,destinations=destinations,nonce='a'*32,
                      attached_sentinels=json.loads((root/'sentinels.json').read_text())) as guard:
        if sys.argv[3]=='blocked':raise RuntimeError('fixture_old_strong_not_blocking')
        for key,target in [('source_shm',Path(str(archive)+'-shm')),('source_wal',Path(str(archive)+'-wal')),
                           ('source_main',archive),('work_main',source)]:guard.move(key,target)
except OSError:
    if sys.argv[3]=='blocked':print('OLD_STRONG_BLOCKED');sys.exit(0)
    raise
print('WORKER_MOVES_CLOSED')
'@
try {
    $result=@(& $Python -I -c $program (Join-Path $Repo 'scripts') $Workspace 'blocked' 2>$null)
    if($LASTEXITCODE -ne 0 -or $result -cnotcontains 'OLD_STRONG_BLOCKED'){throw 'fixture_old_handoff_missing'}
    foreach($stream in $old){$stream.Dispose()};$old=@()
    $result=@(& $Python -I -c $program (Join-Path $Repo 'scripts') $Workspace 'move' 2>$null)
    if($LASTEXITCODE -ne 0 -or $result -cnotcontains 'WORKER_MOVES_CLOSED'){throw 'fixture_worker_move_failed'}
    $group.TakeStrong($keys,[string[]]@($archive,($archive+'-wal'),($archive+'-shm'),$source))
    foreach($key in $keys){if($group.Hash($key) -cne $before[$key]){throw 'fixture_hash_changed'}}
    foreach($key in $keys){Write-Output ($key+'='+$group.MemberJSON($key))}
    $group.AssertAll();$group.Close()
    if(-not $group.Closed -or $group.Faulted -or [IMCStorageGuards]::RetainedCount -ne 0){throw 'fixture_close_unconfirmed'}
    Write-Output 'PS_NATIVE_PARENT_STRONG_HANDOFF_PASS'
} finally {foreach($stream in $old){$stream.Dispose()}}
