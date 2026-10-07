param([switch]$ProbeOnly, [switch]$SignedQuoteProbe,
    [ValidateRange(0,43200)][int]$ShadowDurationSeconds=0, [switch]$ShadowDataOnly,
    [ValidateRange(1,6442450944)][long]$ShadowMaxStorageGrowthBytes=2147483648,
    [ValidateRange(-1,9223372036854775807)][long]$ShadowStorageBaselineBytes=-1,
    [ValidatePattern('\A[1-9A-HJ-NP-Za-km-z]{32,44}\z')][string]$ProbeWallet)
$ErrorActionPreference = 'Stop'
if ($SignedQuoteProbe -and -not $ProbeOnly) { throw 'GMGN_PROBE_MODE_INVALID' }
if ($ShadowDurationSeconds -and $ProbeOnly) { throw 'SHADOW_PROBE_CONFLICT' }
if ($ShadowDataOnly -and (-not $ShadowDurationSeconds -or $ProbeOnly)) { throw 'SHADOW_DATA_MODE_INVALID' }
if(($PSBoundParameters.ContainsKey('ShadowMaxStorageGrowthBytes') -or $PSBoundParameters.ContainsKey('ShadowStorageBaselineBytes')) -and
    -not $ShadowDataOnly){throw 'SHADOW_BUDGET_MODE_INVALID'}
$Root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'IMC_PYTHON_COMMON.ps1')
$Python = if($ShadowDataOnly){Get-IMCPythonPath}else{Get-IMCPython}
. (Join-Path $PSScriptRoot 'IMC_PROCESS_COMMON.ps1')
. (Join-Path $Root 'TOOLS\GMGN-ReadProtocol.ps1')
. (Join-Path $Root 'TOOLS\IMC_RUNTIME_JOB.ps1')
. (Join-Path $Root 'TOOLS\IMC_MAINTENANCE.ps1')
. (Join-Path $Root 'TOOLS\IMC_SHADOW_SESSION.ps1')
$script:shadowSession=$null;$shadowClock=$null;$nextShadowObservation=0.0
$startFence=$null
$mutexName=if ($ProbeOnly) { 'Local\IMC_READ_PROBE_V1' } else { 'Global\IMC_TRADING_MAIN_RUNTIME_V1' }
$mutex = [Threading.Mutex]::new($false, $mutexName)
$owned = $false; $job = $null; $workers=@{}
$statePath = Join-Path $Root 'RUNTIME\signal-runtime-status.json'
$supervisorBirth=(Get-Process -Id $PID).StartTime.ToUniversalTime().ToFileTimeUtc().ToString()
$stage = 'CONFIG'
$script:executionMode='SIGNAL_ONLY';$script:runtimeBroadcast=$false;$script:startupEvidence=$null
function Invoke-ShadowOwnedRead([string]$Code,[string[]]$Arguments) {
    $resultPath=Join-Path $Root ('RUNTIME/shadow-startup-'+[guid]::NewGuid().ToString('N')+'.json')
    $helperPid=$job.Start($Python,[string[]](@('-c',$Code)+$Arguments+@($resultPath)),$Root)
    $limit=10.0
    if($shadowClock){$limit=[math]::Min($limit,$ShadowDurationSeconds-$shadowClock.Elapsed.TotalSeconds)}
    $wait=[Diagnostics.Stopwatch]::StartNew()
    while($job.Running($helperPid) -and $wait.Elapsed.TotalSeconds -lt $limit){Start-Sleep -Milliseconds 100}
    if($job.Running($helperPid)){throw 'RUNTIME_EXECUTION_CONTRACT_TIMEOUT'}
    if($job.ExitCode($helperPid) -ne 0){throw 'RUNTIME_EXECUTION_CONTRACT_UNVERIFIED'}
    $job.Release($helperPid)
    if((Get-Item -LiteralPath $resultPath).Length -gt 65536){throw 'RUNTIME_EXECUTION_CONTRACT_UNVERIFIED'}
    $rows=@(Get-Content -LiteralPath $resultPath -Raw -Encoding UTF8)
    return ($rows[0]|ConvertFrom-Json)
}
function Read-ExecutionStartup {
    $bridge=Join-Path $Root 'RUNTIME\EXECUTOR_GATEWAY\GMGN_BRIDGE'
    $code='import sys,json,numpy,solders;assert sys.version_info[:2]==(3,12);from mykonos_meme_scout.execution_config import load_execution_config;load_execution_config();sys.path.insert(0,sys.argv[1]);from execution_validation import startup_state;from execution_policy import owner_gate;s=startup_state();s[''owner_gate'']=owner_gate();open(sys.argv[2],''w'').write(json.dumps(s))'
    $value=Invoke-ShadowOwnedRead $code @($bridge)
    if($value.mode -cnotin @('SIGNAL_ONLY','THREE_TRADE_VALIDATION') -or
        $value.config_identity -cnotmatch '\A[a-f0-9]{64}\z'){throw 'RUNTIME_EXECUTION_CONTRACT_UNVERIFIED'}
    return $value
}
function Get-ShadowSourceFingerprint {
    $paths=@('config/imc-execution.json','config/owner-broadcast-gate.json',
        'RUNTIME/EXECUTOR_GATEWAY/wallet_public.json','RUNTIME/EXECUTOR_GATEWAY/IMC_MASTER_MODE.json',
        'RUNTIME/EXECUTOR_GATEWAY/GMGN_BRIDGE/execution-ledger.db',
        'RUNTIME/EXECUTOR_GATEWAY/GMGN_BRIDGE/execution-ledger.db-wal')|ForEach-Object{Join-Path $Root $_}
    $milliseconds=5000
    if($shadowClock){$milliseconds=[int][math]::Min($milliseconds,1000*($ShadowDurationSeconds-$shadowClock.Elapsed.TotalSeconds))}
    return Get-IMCShadowFingerprint $paths $milliseconds
}
function Write-RuntimeStatus([string]$State, [string]$Code, $Workers) {
    if ($ProbeOnly) { return }
    if ($State -ceq 'RUNNING' -and ($Workers.Count -ne $(if($ShadowDataOnly){3}else{5}) -or
        @($Workers.Values | Where-Object { -not $_.pid }).Count)) {
        $State='UNKNOWN';$Code='WORKER_RECOVERY'
    }
    $status = @{schema_version=1; updated_at=[DateTimeOffset]::UtcNow.ToString('o');
        state=$State; code=$Code; pid=$PID; process_birth_filetime=$supervisorBirth; workers=$Workers;
        execution_mode=$script:executionMode; source_execution_mode=$script:startupEvidence.mode; broadcast=$script:runtimeBroadcast; live_ready=$false;
        execution_contract_identity=$script:startupEvidence.contract_identity; wallet=$script:startupEvidence.wallet}
    if($script:shadowSession){$status.shadow_session=$script:shadowSession}
    $temporary = $statePath + '.' + [guid]::NewGuid().ToString('N') + '.tmp'
    [IO.File]::WriteAllText($temporary,($status | ConvertTo-Json -Depth 6),[Text.UTF8Encoding]::new($false))
    if ([IO.File]::Exists($statePath)) { [IO.File]::Replace($temporary,$statePath,[NullString]::Value) }
    else { [IO.File]::Move($temporary,$statePath) }
}
try {
    $job = [IMCRuntimeJob]::new()
    if($ShadowDurationSeconds){$shadowClock=[Diagnostics.Stopwatch]::StartNew();$shadowStarted=[DateTime]::UtcNow}
    if($ShadowDataOnly){
        $startFence=Enter-IMCMaintenanceFence -Python $Python -Purpose Start -StateReader {
            $code='import sys,json;sys.path.insert(0,sys.argv[1]);from maintenance_state import read;s=read(sys.argv[2]);open(sys.argv[3],''w'').write(json.dumps(dict(state=s[''state''],nonce=s[''nonce''])))'
            Invoke-ShadowOwnedRead $code @((Join-Path $Root 'scripts'),(Join-Path $Root 'RUNTIME/maintenance-state.json'))
        }
    }else{$startFence=Enter-IMCMaintenanceFence -Python $Python -Purpose Start}
    try { $owned=$mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $owned=$true }
    if (-not $owned) { Write-Output 'IMC_SIGNAL_RUNTIME_ALREADY_RUNNING'; exit 0 }
    if($ProbeOnly){
        & $Python -c 'from mykonos_meme_scout.execution_config import load_execution_config; load_execution_config()'
        if($LASTEXITCODE -ne 0){throw 'RUNTIME_CONFIG_UNSAFE'}
    }
    $sourceFingerprint=Get-ShadowSourceFingerprint
    if(-not $ProbeOnly){
        $script:startupEvidence=Read-ExecutionStartup
        $script:executionMode=$script:startupEvidence.mode;$script:runtimeBroadcast=$script:startupEvidence.broadcast
        if($ShadowDurationSeconds){Assert-IMCShadowContract $script:startupEvidence ([bool]$ShadowDataOnly)}
        if($ShadowDataOnly){
            $script:executionMode='DATA_ONLY_SHADOW';$env:IMC_HEALTH_MODE='DATA_ONLY_SHADOW';$env:IMC_SHADOW_DATA_ONLY='1'
        }else{
            Remove-Item Env:IMC_HEALTH_MODE -ErrorAction SilentlyContinue
            Remove-Item Env:IMC_SHADOW_DATA_ONLY -ErrorAction SilentlyContinue
        }
        if((Get-ShadowSourceFingerprint) -cne $sourceFingerprint){throw 'SHADOW_CONTRACT_CHANGED'}
    }
    $stage='PROCESS_IDENTITY'
    $snapshot=Get-IMCProcessSnapshot
    $others=@($snapshot.Processes | Where-Object { $_.ProcessId -ne $PID })
    if ($snapshot.UnknownInterpreterPids.Count) { throw 'RUNTIME_PROCESS_CONFLICT' }
    if ($ProbeOnly) {
        $allowedRoles=@('SignalSupervisor','Core','Watcher','PureSignalGateway','Bridge','Health','RPCReadHelper','MLAutoOnce')
        if (@($others | Where-Object { $_.Role -cnotin $allowedRoles }).Count -or
            @($others | Group-Object Role | Where-Object { $_.Count -gt 1 }).Count -or
            (Get-IMCProcessRole (Get-CimInstance Win32_Process -Filter ('ProcessId={0}' -f $PID))).Role -cne 'GMGNReadProbe') {
            throw 'RUNTIME_PROCESS_CONFLICT'
        }
    } elseif ($others.Count) { throw 'RUNTIME_PROCESS_CONFLICT' }
    if(-not $ShadowDataOnly){
    # Discover only the host-installed CLI entry for the read-only broker.
    # Workers always use the repository shim and never a direct PATH fallback.
    $realCli = @(Get-Command gmgn-cli.cmd -CommandType Application -All | Where-Object {
        $_.Source -ine (Join-Path $Root 'TOOLS\GMGN_READ_SHIM\gmgn-cli.cmd')
    } | Select-Object -First 1)
    if ($realCli.Count -ne 1) { throw 'GMGN_CLI_UNAVAILABLE' }
    $shell = Join-Path $PSHOME 'powershell.exe'
    $env:IMC_GMGN_IPC_TOKEN = New-GmgnIpcToken
    $env:IMC_EXECUTION_TOKEN = New-GmgnIpcToken
    $env:IMC_GMGN_BROKER_DIR = Join-Path $env:TEMP ('imc-runtime-gmgn-' + $PID + '-' + [guid]::NewGuid().ToString('N'))
    $env:PATH = (Join-Path $Root 'TOOLS\GMGN_READ_SHIM') + ';' + $env:PATH
    $stage='BROKER_START'
    $brokerArgs=@('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',
        (Join-Path $Root 'TOOLS\GMGN-ReadBroker.ps1'),'-BrokerDir',$env:IMC_GMGN_BROKER_DIR,
        '-RealCli',$realCli[0].Source,'-ParentPid',[string]$PID,'-ParentRole','runtime')
    if ($SignedQuoteProbe) { $brokerArgs += '-SignedQuoteProbe' }
    $brokerPid=$job.Start($shell,[string[]]$brokerArgs,$Root)
    $deadline=[DateTime]::UtcNow.AddSeconds(10)
    $readyPath=Join-Path $env:IMC_GMGN_BROKER_DIR 'broker.ready'
    while ([DateTime]::UtcNow -lt $deadline -and -not [IO.File]::Exists($readyPath) -and $job.Running($brokerPid)) {
        Start-Sleep -Milliseconds 100
    }
    $stage='BROKER_READY'
    $cfg=Get-GmgnReadConfig
    $identity=Read-GmgnJson $readyPath $cfg.max_request_bytes
    Assert-GmgnReady $identity $env:IMC_GMGN_BROKER_DIR
    if ($identity.broker.pid -ne $brokerPid -or $identity.parent.pid -ne $PID) { throw 'GMGN_BROKER_IDENTITY_CHANGED' }
    $stage='PORTFOLIO'
    if ($ProbeOnly -and $ProbeWallet) {
        # A public address permits an isolated quote diagnostic after cooldown;
        # this branch never verifies or grants a runtime wallet binding.
        $wallets=@([pscustomobject]@{address=$ProbeWallet})
    } elseif(-not $ProbeOnly -and $script:executionMode -ceq 'THREE_TRADE_VALIDATION') {
        $wallets=@([pscustomobject]@{address=$script:startupEvidence.wallet})
    } else {
        $walletText = & (Join-Path $Root 'TOOLS\GMGN_READ_SHIM\gmgn-cli.cmd') portfolio info --raw 2>$null
        if ($LASTEXITCODE -ne 0) { throw 'GMGN_PORTFOLIO_UNAVAILABLE' }
        $wallets=@(($walletText | ConvertFrom-Json).wallets | Where-Object { $_.chain -ceq 'sol' })
    }
    if ($wallets.Count -ne 1) { throw 'GMGN_WALLET_BINDING_AMBIGUOUS' }
    if ($ProbeOnly) {
        $stage='QUOTE'
        # Avoid an initialization burst without assuming independent provider
        # buckets. This is a bounded READ-only diagnostic, never SEND.
        Start-Sleep -Seconds 5
        $previous=$ErrorActionPreference; $ErrorActionPreference='Continue'
        try {
            $quoteOutput=@(& (Join-Path $Root 'TOOLS\GMGN_READ_SHIM\gmgn-cli.cmd') order quote --chain sol --from $wallets[0].address `
                --input-token So11111111111111111111111111111111111111112 `
                --output-token EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v --amount 10000000 --slippage 1 --raw 2>&1)
            $quoteOk=$LASTEXITCODE -eq 0
        } finally { $ErrorActionPreference=$previous }
        $code='QUOTE_PASS'
        $diag=$null
        if (-not $quoteOk) {
            $rows=[regex]::Matches(($quoteOutput | ForEach-Object { [string]$_ }) -join "`n",'(?m)^GMGN_READ_EVIDENCE=(\{[^\r\n]*\})$')
            $code='GMGN_INTERNAL_ERROR'
            if ($rows.Count -eq 1) {
                $diag=$rows[0].Groups[1].Value | ConvertFrom-Json
                Assert-GmgnFailureEvidence $diag
                $code=$diag.code
            }
        }
        Write-Output ('GMGN_READ_PROBE=' + $code)
        if ($diag -and ($code -clike 'GMGN_RATE_*' -or $code -ceq 'GMGN_COOLDOWN_UNKNOWN')) {
            Write-Output ('GMGN_RETRY_AFTER_SECONDS=' + [int]$diag.retry_after_seconds)
            Write-Output ('GMGN_ERROR_ORIGIN=' + $diag.origin)
            Write-Output ('GMGN_HTTP_STATUS=' + $diag.http_status)
            Write-Output ('GMGN_API_CODE=' + $diag.api_code)
            $apiError=if ($null -ne $diag.api_error) { $diag.api_error } else { 'UNKNOWN' }
            Write-Output ('GMGN_API_ERROR=' + $apiError)
            Write-Output ('GMGN_RESET_SOURCE=' + $diag.reset_source)
            foreach ($name in @('quota_limit','quota_remaining')) {
                $hint=if ($diag.$name -is [int]) { [string]$diag.$name } else { 'UNKNOWN' }
                Write-Output ('GMGN_'+$name.ToUpperInvariant()+'_HINT='+$hint)
            }
        }
        exit $(if ($quoteOk) { 0 } else { 1 })
    }
    }
    # Start components individually. Never overwrite a master-mode file or
    # stop another runtime. This job owns every child and descendant it creates.
    $stage='WORKER_START'
    if($ShadowDurationSeconds){
        $script:shadowSession=New-IMCShadowSession $ShadowDurationSeconds $script:startupEvidence $shadowStarted (Get-IMCShadowStorage $Root) ([bool]$ShadowDataOnly) $ShadowMaxStorageGrowthBytes $ShadowStorageBaselineBytes
    }
    $definitions=@(
        @{role='CORE'; args=[string[]]@('-u','-m','mykonos_meme_scout','--database',(Join-Path $Root 'CORE\data\imc-main.db'),'run','--maximum','30','--interval','0.5')},
        @{role='WATCHER'; args=[string[]]@('-u',(Join-Path $Root 'RUNTIME\watch_breadth_v425_shadow.py'))},
        @{role='GATEWAY'; args=[string[]]@('-u',(Join-Path $Root 'RUNTIME\EXECUTOR_GATEWAY\GMGN_BRIDGE\imc_pure_signal_gateway.py'))},
        @{role='BRIDGE'; args=[string[]]@('-u',(Join-Path $Root 'RUNTIME\EXECUTOR_GATEWAY\GMGN_BRIDGE\gmgn_signal_bridge.py'))},
        @{role='HEALTH'; args=[string[]]@('-u',(Join-Path $Root 'RUNTIME\imc_system_health.py'))}
    )
    if($ShadowDataOnly){$definitions=@($definitions|Where-Object{$_.role -in @('CORE','WATCHER','HEALTH')})}
    $workers=@{}
    foreach($definition in $definitions) {
        $workers[$definition.role]=@{pid=$job.Start($Python,$definition.args,$Root); restarts=0; due=[DateTime]::MinValue}
    }
    $stage='MONITOR'
    Exit-IMCMaintenanceFence $startFence;$startFence=$null
    while ($true) {
        if($script:shadowSession){
            $report=$null
            if($shadowClock.Elapsed.TotalSeconds -ge $ShadowDurationSeconds -or
                [DateTime]::UtcNow -ge [datetime]::Parse($script:shadowSession.planned_end).ToUniversalTime()){
                $null=Test-IMCShadowTick $script:shadowSession $script:startupEvidence $shadowClock.Elapsed.TotalSeconds ([DateTime]::UtcNow) $null $null
                break
            }
            if($shadowClock.Elapsed.TotalSeconds -ge $nextShadowObservation){
                if($ShadowDataOnly){
                    if((Get-ShadowSourceFingerprint) -cne $sourceFingerprint){throw 'SHADOW_CONTRACT_CHANGED'}
                    $current=$script:startupEvidence
                }else{$current=Read-ExecutionStartup}
                $reportPath=Join-Path $Root 'RUNTIME\shadow-session-observation.json'
                try {
                    if((Get-Item -LiteralPath $reportPath -ErrorAction Stop).Length -gt 65536){throw 'report size'}
                    $report=Get-Content -LiteralPath $reportPath -Raw -Encoding UTF8 | ConvertFrom-Json
                } catch {$report=[pscustomobject]@{error='OBSERVATION_UNAVAILABLE';observed_at=[DateTime]::UtcNow.ToString('o')}}
                $nextShadowObservation=$shadowClock.Elapsed.TotalSeconds+30
            }else{$current=$script:startupEvidence}
            if(-not(Test-IMCShadowTick $script:shadowSession $current $shadowClock.Elapsed.TotalSeconds ([DateTime]::UtcNow) $report (Get-IMCShadowStorage $Root))){break}
            if($ShadowDataOnly -and $shadowClock.Elapsed.TotalSeconds -ge 90){
                try{$health=Get-Content -LiteralPath (Join-Path $Root 'RUNTIME/IMC_SYSTEM_HEALTH.json') -Raw -Encoding UTF8|ConvertFrom-Json}
                catch{throw 'SHADOW_HEALTH_UNVERIFIED'}
                Assert-IMCShadowHealth $health ([DateTime]::UtcNow) $shadowStarted $script:shadowSession
            }
        }
        if(-not $ShadowDataOnly){
            if (-not $job.Running($brokerPid)) { throw 'GMGN_BROKER_EXITED' }
            Assert-GmgnReady $identity $env:IMC_GMGN_BROKER_DIR
        }
        foreach($definition in $definitions) {
            $worker=$workers[$definition.role]
            if ($worker.pid -and -not $job.Running($worker.pid)) {
                if($script:shadowSession){throw 'SHADOW_WORKER_EXITED'}
                $worker.last_exit_code=$job.ExitCode($worker.pid)
                $worker.last_exit_pid=$worker.pid
                $worker.exit_observed_at=[DateTimeOffset]::UtcNow.ToString('o')
                $job.Release($worker.pid); $worker.pid=0; $worker.restarts++
                $worker.due=[DateTime]::UtcNow.AddSeconds([math]::Min(60,5*$worker.restarts))
            }
            if (-not $worker.pid -and [DateTime]::UtcNow -ge $worker.due) {
                & $Python -c 'from mykonos_meme_scout.execution_config import load_execution_config; load_execution_config()'
                if ($LASTEXITCODE -ne 0) { throw 'RUNTIME_CONFIG_UNSAFE' }
                $current=Read-ExecutionStartup
                if(($current|ConvertTo-Json -Compress) -cne ($script:startupEvidence|ConvertTo-Json -Compress)){
                    throw 'RUNTIME_EXECUTION_CONTRACT_CHANGED'
                }
                $worker.pid=$job.Start($Python,$definition.args,$Root)
            }
        }
        $modeCode=if($script:executionMode -ceq 'THREE_TRADE_VALIDATION'){
            if($script:runtimeBroadcast){'OWNER_GATED_THREE_TRADE_RUNTIME'}else{'JUPITER_PREPARED_GATES_OFF'}
        }else{'PASSIVE_NO_TRANSPORT'}
        if($ShadowDataOnly){$modeCode='DIAGNOSTIC_DATA_ONLY_NO_EXECUTOR'}
        Write-RuntimeStatus 'RUNNING' $modeCode $workers
        Start-Sleep -Seconds 5
    }
} catch {
    $code=[string]$_.Exception.Message
    if ($code -cnotmatch '\A[A-Z][A-Z0-9_]+\z') { $code='RUNTIME_START_OR_MONITOR_FAILURE' }
    if($script:shadowSession){$script:shadowSession.stop_reason=$code}
    if($owned){try { Write-RuntimeStatus 'UNKNOWN' $code $workers } catch {}}
    [Console]::Error.WriteLine($code + ':' + $stage + ':LINE_' + $_.InvocationInfo.ScriptLineNumber)
    exit 1
} finally {
    if ($job) {
        $job.Dispose()
        if($script:shadowSession -and $job.CleanupConfirmed){
            Write-RuntimeStatus 'STOPPED' $script:shadowSession.stop_reason @{}
        }
    }
    if ($owned) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
    if($startFence){Exit-IMCMaintenanceFence $startFence}
}
