$ErrorActionPreference='Stop'
. (Join-Path (Split-Path -Parent $PSScriptRoot) 'LAUNCHERS\IMC_PYTHON_COMMON.ps1')
$Python = Get-IMCPython
$ErrorActionPreference='SilentlyContinue'
$Mutex=New-Object System.Threading.Mutex($false,'Global\IMC_RESEARCH_HEALTH_WATCHDOG_V483')
$OwnsMutex=$false
try{$OwnsMutex=$Mutex.WaitOne(0,$false)}catch [System.Threading.AbandonedMutexException]{$OwnsMutex=$true}
if(-not $OwnsMutex){exit 0}
$root="$env:USERPROFILE\Desktop\IMC_TRADING_MAIN\RESEARCH"
$state=Join-Path $root 'WATCHDOG_RESEARCH_V482_STATUS.json'

$lanes=@(
  @{
    Name='VR12_WATCH'
    Script=(Join-Path $root 'watch_safe100_vr12_balanced.py')
    Status=(Join-Path $root 'watch_safe100_vr12_balanced_status.json')
    StaleSeconds=75
    Complete=$null
  },
  @{
    Name='DG120_WATCH'
    Script=(Join-Path $root 'watch_safe100_dg120.py')
    Status=(Join-Path $root 'watch_safe100_dg120_status.json')
    StaleSeconds=75
    Complete=$null
  },
  @{
    Name='VR12_EXEC'
    Script=(Join-Path $root 'execution_v483_vr12_route_forward.py')
    Status=(Join-Path $root 'execution_v483_vr12_route_forward_status.json')
    StaleSeconds=12
    Complete=(Join-Path $root 'EXECUTION_V483_VR12_COMPLETE.json')
  }
)

function Get-LaneProcesses([string]$script){
  $needle=[IO.Path]::GetFileName($script)
  return @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -in @('py.exe','python.exe') -and $_.CommandLine -and $_.CommandLine -like "*$needle*"
  })
}

function Get-StatusInfo([string]$file,[int]$staleSeconds){
  $o=[ordered]@{exists=$false;age_seconds=$null;stale=$false;live_execution=$false;authority=$null;last_error=$null}
  if(Test-Path $file){
    $o.exists=$true
    $o.age_seconds=[math]::Round(((Get-Date)-(Get-Item $file).LastWriteTime).TotalSeconds,1)
    $o.stale=($o.age_seconds -gt $staleSeconds)
    try{
      $j=Get-Content $file -Raw | ConvertFrom-Json
      $o.live_execution=[bool]$j.live_execution
      $o.authority=$j.authority
      $o.last_error=$j.last_error
    }catch{
      $o.stale=$true
      $o.last_error="STATUS_PARSE: $($_.Exception.Message)"
    }
  }
  return $o
}

while($true){
  $report=[ordered]@{
    version='4.8.3-research-health-watchdog'
    updated=[DateTime]::UtcNow.ToString('o')
    authority='0%'
    live_execution=$false
    lanes=@{}
    last_error=$null
  }

  foreach($lane in $lanes){
    try{
      $complete=($lane.Complete -and (Test-Path $lane.Complete))
      $procs=Get-LaneProcesses $lane.Script
      $si=Get-StatusInfo $lane.Status $lane.StaleSeconds
      $action='NONE'

      if(-not $complete){
        if($procs.Count -eq 0){
          Start-Process $Python -WindowStyle Hidden -ArgumentList '-u',('"'+$lane.Script+'"')
          Start-Sleep -Milliseconds 800
          $procs=Get-LaneProcesses $lane.Script
          $action='STARTED'
        }elseif($si.stale){
          if($si.live_execution){
            $action='STALE_LIVE_RECONCILE_REQUIRED'
            $report.last_error="$($lane.Name) stale while live; automatic restart blocked"
          }else{
            foreach($p in $procs){ Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
            Start-Sleep -Milliseconds 500
            Start-Process $Python -WindowStyle Hidden -ArgumentList '-u',('"'+$lane.Script+'"')
            Start-Sleep -Milliseconds 800
            $procs=Get-LaneProcesses $lane.Script
            $action='RESTARTED_STALE'
          }
        }
      }else{
        $action='COMPLETE'
      }

      $report.lanes[$lane.Name]=[ordered]@{
        running=($procs.Count -gt 0)
        process_count=$procs.Count
        complete=[bool]$complete
        status_exists=$si.exists
        status_age_seconds=$si.age_seconds
        stale=$si.stale
        live_execution=$si.live_execution
        authority=$si.authority
        action=$action
        lane_last_error=$si.last_error
      }
    }catch{
      $report.last_error="$($lane.Name): $($_.Exception.Message)"
    }
  }

  $report | ConvertTo-Json -Depth 6 | Set-Content $state -Encoding UTF8
  Start-Sleep -Seconds 5
}
