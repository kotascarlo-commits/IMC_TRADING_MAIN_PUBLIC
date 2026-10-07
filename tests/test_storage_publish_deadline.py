import ctypes
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

ROOT=Path(__file__).parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from storage_publish_deadline import Deadline

pytestmark=pytest.mark.skipif(os.name!='nt',reason='same-machine native Windows QPC')


def ticks():
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    for name in ('QueryPerformanceFrequency','QueryPerformanceCounter'):
        function=getattr(api,name)
        function.argtypes=[ctypes.POINTER(ctypes.c_int64)];function.restype=ctypes.c_int
    frequency=ctypes.c_int64();now=ctypes.c_int64()
    assert api.QueryPerformanceFrequency(ctypes.byref(frequency))
    assert api.QueryPerformanceCounter(ctypes.byref(now))
    return now.value,frequency.value


def test_shared_deadline_has_identical_native_binding_across_processes():
    now,frequency=ticks();end=now+frequency*5
    first=Deadline(end,frequency)
    script="""
import sys
sys.path.insert(0,sys.argv[1])
from storage_publish_deadline import Deadline
d=Deadline(int(sys.argv[2]),int(sys.argv[3]));d.check();print(d.binding)
"""
    result=subprocess.run([sys.executable,'-I','-c',script,str(ROOT/'scripts'),str(end),str(frequency)],
                          capture_output=True,text=True,timeout=5)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip()==str(first.binding)


@pytest.mark.parametrize('change',['expired','too_far','wrong_frequency','bool_end','bool_frequency'])
def test_bad_absolute_deadline_cannot_create_a_consumer(change):
    now,frequency=ticks();end=now+frequency*5
    if change=='expired':end=now-1
    elif change=='too_far':end=now+frequency*21601
    elif change=='wrong_frequency':frequency+=1
    elif change=='bool_end':end=True
    else:frequency=True
    with pytest.raises(ValueError):Deadline(end,frequency)


def test_later_consumer_does_not_reset_original_budget():
    now,frequency=ticks();end=now+int(frequency*.25)
    first=Deadline(end,frequency)
    time.sleep(.10)
    later=Deadline(end,frequency)
    assert later.binding==first.binding
    time.sleep(.17)
    for consumer in (first,later):
        with pytest.raises(TimeoutError,match='expired'):consumer.check()
        with pytest.raises(ValueError,match='faulted'):consumer.check()


def test_regressed_counter_permanently_faults_consumer(monkeypatch):
    now,frequency=ticks();consumer=Deadline(now+frequency*5,frequency)
    monkeypatch.setattr(consumer,'_read',lambda:consumer._last-1)
    with pytest.raises(ValueError,match='regressed'):consumer.check()
    with pytest.raises(ValueError,match='faulted'):consumer.check()


def test_initialization_uses_first_counter_as_regression_baseline(monkeypatch):
    _,frequency=ticks();values=iter((100,90))
    monkeypatch.setattr(Deadline,'_read',lambda self:next(values))
    with pytest.raises(ValueError,match='regressed'):Deadline(100+frequency*5,frequency)


@pytest.mark.parametrize('case',['regressed','expired'])
def test_csharp_constructor_transition_rechecks_before_native_pipe_open(case):
    script=r'''
$ErrorActionPreference='Stop'
. ./TOOLS/IMC_STORAGE_CHANNEL.ps1
Add-Type -TypeDefinition @'
using System;using System.Diagnostics;using System.Reflection;
public static class DeadlineConstructorFixture {
 public static void Inject(Type type,bool regression) {
  int call=0;long first=100;
  Func<long> clock=delegate(){call++;return call==1?first:regression?90:first+Stopwatch.Frequency*10;};
  type.GetField("constructorTicksNative",BindingFlags.Static|BindingFlags.NonPublic).SetValue(null,clock);
 }
}
'@
[DeadlineConstructorFixture]::Inject([IMCStorageChannel],CASE_REGRESSION)
$end=100+[Diagnostics.Stopwatch]::Frequency*5
try{$null=[IMCStorageChannel]::CreateServerUntil(('c'*32),'init',$end,[Diagnostics.Stopwatch]::Frequency);throw 'native_open_after_invalid_clock'}
catch{if($_.Exception.GetBaseException().Message -cne 'EXPECTED_ERROR'){throw}}
$pool=[IMCStorageChannel].GetField('retained',[Reflection.BindingFlags]'Static,NonPublic').GetValue($null)
if($pool.Count -ne 1){throw 'constructor_fault_not_retained'}
$group=$pool[0]
if(-not $group.Faulted -or $group.Closed -or $group.GetType().GetField('pipeState',[Reflection.BindingFlags]'Instance,NonPublic').GetValue($group).ToString() -cne 'Empty'){throw 'constructor_native_open_occurred'}
Write-Output 'PRE_NATIVE_OPEN_CLOCK_REJECTED'
'''
    import base64
    script=script.replace('CASE_REGRESSION','$true' if case=='regressed' else '$false')
    script=script.replace('EXPECTED_ERROR','STORAGE_CHANNEL_CLOCK_REGRESSED' if case=='regressed' else 'STORAGE_CHANNEL_DEADLINE')
    command=base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',command],
        cwd=ROOT,capture_output=True,timeout=10,creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode==0,(result.stdout+result.stderr).decode(errors='replace')
    assert b'PRE_NATIVE_OPEN_CLOCK_REJECTED' in result.stdout


@pytest.mark.parametrize('case',['regressed','expired'])
def test_csharp_client_rechecks_after_peer_bind_before_native_pipe_open(case):
    script=r'''
$ErrorActionPreference='Stop'
. ./TOOLS/IMC_STORAGE_CHANNEL.ps1
Add-Type -TypeDefinition @'
using System;using System.Diagnostics;using System.Reflection;
public static class DeadlineClientFixture {
 public static void Inject(Type type,bool regression) {
  int calls=0;Func<long> clock=delegate(){calls++;return calls<=3?99+calls:regression?90:100+Stopwatch.Frequency*10;};
  type.GetField("constructorTicksNative",BindingFlags.Static|BindingFlags.NonPublic).SetValue(null,clock);
 }
}
'@
[DeadlineClientFixture]::Inject([IMCStorageChannel],CASE_REGRESSION)
$birth=[Diagnostics.Process]::GetCurrentProcess().StartTime.ToUniversalTime().ToFileTimeUtc()
$end=100+[Diagnostics.Stopwatch]::Frequency*5
try{$null=[IMCStorageChannel]::ConnectClientUntil(('c'*32),'never_created',$PID,$birth,$end,[Diagnostics.Stopwatch]::Frequency);throw 'native_client_open_after_invalid_clock'}
catch{if($_.Exception.GetBaseException().Message -cne 'EXPECTED_ERROR'){throw}}
$pool=[IMCStorageChannel].GetField('retained',[Reflection.BindingFlags]'Static,NonPublic').GetValue($null)
if($pool.Count -ne 1){throw 'client_fault_not_retained'}
$group=$pool[0]
if(-not $group.Faulted -or $group.Closed){throw 'client_fault_state_missing'}
foreach($pair in @(@('pipeState','Empty'),@('peerState','Owned'))) {
 if($group.GetType().GetField($pair[0],[Reflection.BindingFlags]'Instance,NonPublic').GetValue($group).ToString() -cne $pair[1]){throw 'client_native_open_or_peer_loss'}
}
Write-Output 'PRE_CLIENT_NATIVE_OPEN_CLOCK_REJECTED'
'''
    import base64
    script=script.replace('CASE_REGRESSION','$true' if case=='regressed' else '$false')
    script=script.replace('EXPECTED_ERROR','STORAGE_CHANNEL_CLOCK_REGRESSED' if case=='regressed' else 'STORAGE_CHANNEL_DEADLINE')
    command=base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',command],
        cwd=ROOT,capture_output=True,timeout=10,creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode==0,(result.stdout+result.stderr).decode(errors='replace')
    assert b'PRE_CLIENT_NATIVE_OPEN_CLOCK_REJECTED' in result.stdout


@pytest.mark.parametrize('case',['frequency','expired','too_far','regressed'])
def test_csharp_until_rejects_bad_binding_before_native_open_or_cleanup(case):
    script=r'''
. ./TOOLS/IMC_STORAGE_CHANNEL.ps1
$frequency=[Diagnostics.Stopwatch]::Frequency
$end=[Diagnostics.Stopwatch]::GetTimestamp()+$frequency*5
switch($args[0]) {
 'frequency' {$frequency++}
 'expired' {$end=[Diagnostics.Stopwatch]::GetTimestamp()-1}
 'too_far' {$end=[Diagnostics.Stopwatch]::GetTimestamp()+$frequency*21601}
}
if($args[0] -cne 'regressed') {
 try{$null=[IMCStorageChannel]::CreateServerUntil(('d'*32),'deadline',$end,$frequency);throw 'bad_deadline_admitted'}
 catch{if($_.Exception.GetBaseException().Message -cne 'STORAGE_CHANNEL_SHARED_DEADLINE'){throw}}
 if([IMCStorageChannel]::RetainedCount -ne 0){throw 'bad_deadline_native_open'}
}else {
 $group=[IMCStorageChannel]::CreateServerUntil(('d'*32),'deadline',$end,$frequency)
 Add-Type -TypeDefinition @'
using System;using System.Reflection;
public static class DeadlineRegressionFixture {
 public static void Attach(object g){Type t=g.GetType();long last=(long)t.GetField("lastTick",BindingFlags.Instance|BindingFlags.NonPublic).GetValue(g);
 t.GetField("ticksNative",BindingFlags.Instance|BindingFlags.NonPublic).SetValue(g,new Func<long>(delegate(){return last-1;}));}
}
'@
 [DeadlineRegressionFixture]::Attach($group)
 try{$group.Close();throw 'regressed_close_admitted'}catch{if($_.Exception.GetBaseException().Message -cne 'STORAGE_CHANNEL_CLOCK_REGRESSED'){throw}}
 if(-not $group.Faulted -or $group.Closed -or [IMCStorageChannel]::RetainedCount -ne 1){throw 'regression_not_retained'}
 $state=$group.GetType().GetField('pipeState',[Reflection.BindingFlags]'Instance,NonPublic').GetValue($group).ToString()
 if($state -cne 'Owned'){throw 'regression_closed_pipe'}
}
Write-Output 'ABSOLUTE_DEADLINE_REJECTION_PASS'
'''
    # -Command text plus $args is awkward in PS5; use an encoded command with
    # the literal controlled enum rather than shell-interpolating user data.
    script=script.replace('$args[0]',"'"+case+"'")
    import base64
    command=base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',command],
        cwd=ROOT,capture_output=True,timeout=10,creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode==0,(result.stdout+result.stderr).decode(errors='replace')
    assert b'ABSOLUTE_DEADLINE_REJECTION_PASS' in result.stdout
