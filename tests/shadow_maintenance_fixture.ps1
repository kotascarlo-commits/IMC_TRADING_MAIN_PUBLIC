$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
. (Join-Path $root 'TOOLS/IMC_RUNTIME_JOB.ps1')
. (Join-Path $root 'TOOLS/IMC_MAINTENANCE.ps1')
$python=Join-Path $root '.venv/Scripts/python.exe'
$job=[IMCRuntimeJob]::new();$script:helperPid=0;$lease=$null
try {
    $blocked=$false
    try {
        $lease=Enter-IMCMaintenanceFence -Python $python -Purpose Start -StateReader {
            $script:helperPid=$job.Start($python,[string[]]@('-c','import time;time.sleep(60)'),$root)
            $clock=[Diagnostics.Stopwatch]::StartNew()
            while($job.Running($script:helperPid) -and $clock.Elapsed.TotalSeconds -lt 1){Start-Sleep -Milliseconds 50}
            throw 'IMC_MAINTENANCE_READ_TIMEOUT'
        }
    }catch{if($_.Exception.Message -ceq 'IMC_MAINTENANCE_READ_TIMEOUT'){$blocked=$true}else{throw}}
    if(-not $blocked -or $lease){throw 'hanging read admitted'}
}finally{$job.Dispose()}
if(-not $job.CleanupConfirmed){throw 'hanging maintenance helper survived'}
# A failed bounded read releases the same fence; ACTIVE still blocks admission.
$blocked=$false
try{Enter-IMCMaintenanceFence -Python $python -Purpose Start -StateReader {[pscustomobject]@{state='ACTIVE';nonce=('a'*32)}}|Out-Null}
catch{if($_.Exception.Message -ceq 'IMC_MAINTENANCE_RECOVERY_REQUIRED'){$blocked=$true}else{throw}}
if(-not $blocked){throw 'active maintenance admitted'}
$lease=Enter-IMCMaintenanceFence -Python $python -Purpose Start -StateReader {[pscustomobject]@{state='IDLE';nonce=('a'*32)}}
Exit-IMCMaintenanceFence $lease
'SHADOW_MAINTENANCE_TIMEOUT_CLEANUP_PASS'
