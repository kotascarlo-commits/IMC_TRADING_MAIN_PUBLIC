param([ValidateSet('free','owned','foreign','abandoned','closed')][string]$Case)
$ErrorActionPreference='Stop'
. (Join-Path (Split-Path -Parent $PSScriptRoot) 'TOOLS\IMC_RUNTIME_JOB.ps1')
Add-Type -TypeDefinition @'
using System;
using System.Threading;
public static class IMCMutexFixture {
    static Thread thread;
    static ManualResetEvent ready,release;
    static Exception fault;
    public static void Start(Mutex mutex, bool abandon) {
            ready=new ManualResetEvent(false);release=new ManualResetEvent(false);
            thread=new Thread(delegate() {
                try {
                    mutex.WaitOne();ready.Set();
                    if(!abandon) { release.WaitOne();mutex.ReleaseMutex(); }
                } catch(Exception error) { fault=error;ready.Set(); }
            });
            thread.Start();
            if(!ready.WaitOne(5000))throw new InvalidOperationException("fixture_thread_timeout");
            if(abandon && !thread.Join(5000))throw new InvalidOperationException("fixture_join_timeout");
            if(fault!=null)throw fault;
    }
    public static void Finish() {
        release.Set();if(!thread.Join(5000))throw new InvalidOperationException("fixture_join_timeout");
        ready.Dispose();release.Dispose();if(fault!=null)throw fault;
    }
}
'@
$m=[Threading.Mutex]::new($false,('Local\IMC_MUTEX_TEST_'+[guid]::NewGuid().ToString('N')))
try {
    switch($Case) {
        free {
            $proof=[IMCNativeMutexState]::Read($m.SafeWaitHandle)
            if($proof.Owned -or $proof.Abandoned -or $proof.Count -ne 1 -or $proof.OwnerProcess -ne 0){throw 'fixture_free_changed'}
        }
        owned {
            if(-not $m.WaitOne(0)){throw 'fixture_owned_busy'}
            try {
                $proof=[IMCNativeMutexState]::Read($m.SafeWaitHandle)
                if(-not $proof.Owned -or $proof.Abandoned -or $proof.Count -ne 0 -or
                        $proof.OwnerProcess -ne $PID -or $proof.OwnerThread -ne $proof.CallerThread){throw 'fixture_owned_missing'}
            } finally {$m.ReleaseMutex()}
        }
        foreign {
            [IMCMutexFixture]::Start($m,$false)
            try {
                $proof=[IMCNativeMutexState]::Read($m.SafeWaitHandle)
                if($proof.Owned -or $proof.Abandoned -or $proof.OwnerThread -eq $proof.CallerThread -or
                        $proof.Count -ne 0 -or $proof.OwnerProcess -ne $PID){throw 'fixture_foreign_owner_unproved'}
            } finally {[IMCMutexFixture]::Finish()}
        }
        abandoned {
            [IMCMutexFixture]::Start($m,$true)
            try {
                $proof=[IMCNativeMutexState]::Read($m.SafeWaitHandle)
                if(-not $proof.Abandoned -or $proof.Owned){throw 'fixture_abandonment_unproved'}
            } finally {[IMCMutexFixture]::Finish()}
            $owned=$false
            try{$owned=$m.WaitOne(0)}catch [Threading.AbandonedMutexException]{$owned=$true}
            if(-not $owned){throw 'fixture_abandoned_not_acquired'}
            $m.ReleaseMutex()
        }
        closed {
            $m.Dispose();$rejected=$false
            try{$null=[IMCNativeMutexState]::Read($m.SafeWaitHandle)}catch {$rejected=$true}
            if(-not $rejected){throw 'fixture_closed_accepted'}
        }
    }
    Write-Output 'native mutex identity PASS'
} finally {$m.Dispose()}
