# Local milestone accounting. No service, model, or trading actions.
$script:IMCProgressDefaultPath = Join-Path (Split-Path -Parent $PSScriptRoot) 'RUNTIME\work_progress.json'
$script:IMCProgressConfigPath = Join-Path (Split-Path -Parent $PSScriptRoot) 'config\imc-work-progress.json'

function Get-IMCProgressConfig {
    $config = Get-Content -LiteralPath $script:IMCProgressConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($key in @('poll_seconds','stale_seconds','lock_timeout_seconds','max_status_bytes','bar_width')) {
        $number = $config.$key
        if ($number -isnot [ValueType] -or $number -is [bool] -or
            [double]::IsNaN([double]$number) -or [double]::IsInfinity([double]$number) -or $number -le 0) {
            throw "Invalid progress config: $key"
        }
    }
    if ($config.poll_seconds -lt 1 -or $config.bar_width -gt 100) { throw 'Invalid progress polling/bar width' }
    return $config
}

function Get-IMCWorkProgress([string]$Path = $script:IMCProgressDefaultPath) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return }
    $config = Get-IMCProgressConfig
    if ((Get-Item -LiteralPath $Path).Length -gt $config.max_status_bytes) { throw 'Progress file too large' }
    $status = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    $null = Test-IMCProgressStatus $status
    return $status
}

function Test-IMCProgressStatus($Status) {
    foreach ($key in @('task','task_id','session_id','current_step')) {
        if ($Status.$key -isnot [string] -or [string]::IsNullOrWhiteSpace($Status.$key) -or $Status.$key.Length -gt 500) {
            throw "Invalid progress text: $key"
        }
    }
    if ($Status.schema_version -ne 1 -or $Status.state -notin @('running','paused','blocked','failed','completed') -or
        $Status.astra_status -notin @('pending','reviewing','checked','change','risk','not_required')) { throw 'Invalid progress state' }
    if ($Status.revision -isnot [int] -or $Status.revision -lt 1) { throw 'Invalid progress revision' }
    $ids = @{}; $weight = 0.0; $done = 0.0
    if (@($Status.work_packages).Count -eq 0) { throw 'No weighted work packages' }
    foreach ($package in $Status.work_packages) {
        if ($package.id -isnot [string] -or [string]::IsNullOrWhiteSpace($package.id) -or $ids.ContainsKey($package.id)) {
            throw 'Invalid/duplicate work package ID'
        }
        $ids[$package.id] = $true
        foreach ($key in @('weight','percent')) {
            $v = $package.$key
            if ($v -isnot [ValueType] -or $v -is [bool] -or [double]::IsNaN([double]$v) -or [double]::IsInfinity([double]$v)) {
                throw 'Invalid work package number'
            }
        }
        if ($package.weight -le 0 -or $package.percent -lt 0 -or $package.percent -gt 100) { throw 'Invalid package weight/percent' }
        $weight += $package.weight
        $done += $package.weight * $package.percent / 100.0
    }
    if ([math]::Abs($weight - 100) -gt 0.000001) { throw 'Weights must sum to 100' }
    foreach ($key in @('tests_passed','tests_total')) {
        if ($Status.$key -isnot [int] -or $Status.$key -lt 0) { throw 'Invalid test counts' }
    }
    if ($Status.tests_passed -gt $Status.tests_total -or $Status.tests_verified -isnot [bool]) { throw 'Invalid test results' }
    $allDone = @($Status.work_packages | Where-Object { $_.percent -ne 100 }).Count -eq 0
    if ($Status.state -eq 'completed') {
        if (-not $allDone -or -not $Status.tests_verified -or $Status.tests_total -le 0 -or
            $Status.tests_passed -ne $Status.tests_total -or $Status.astra_status -notin @('checked','not_required')) {
            throw '100% requires all packages, verified passing tests and considered review'
        }
        return 100
    }
    return [math]::Min(99, [int][math]::Floor($done))
}

function Set-IMCWorkProgress {
    param(
        [Parameter(Mandatory)][string]$SessionId,
        [Parameter(Mandatory)][string]$TaskId,
        [string]$Path = $script:IMCProgressDefaultPath,
        [int]$ExpectedRevision = -1,
        [switch]$Begin, [switch]$Adopt,
        [string]$Task, [string]$PackagesJson,
        [string]$CurrentStep, [string]$PackageId,
        [double]$PackagePercent = -1,
        [string]$AstraStatus, [string]$State,
        [int]$TestsPassed = -1, [int]$TestsTotal = -1,
        [Nullable[bool]]$TestsVerified
    )
    $config = Get-IMCProgressConfig
    $absolute = [IO.Path]::GetFullPath($Path)
    $hash = [Security.Cryptography.SHA256]::Create()
    try { $key = [BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($absolute.ToLowerInvariant()))).Replace('-','') }
    finally { $hash.Dispose() }
    $mutex = [Threading.Mutex]::new($false, 'Local\IMCWorkProgress_' + $key)
    $owned = $false; $temporary = $null
    try {
        try { $owned = $mutex.WaitOne([int]($config.lock_timeout_seconds * 1000)) }
        catch [Threading.AbandonedMutexException] { $owned = $true }
        if (-not $owned) { throw 'Progress writer lock timeout' }
        $previous = Get-IMCWorkProgress $absolute
        $revision = if ($previous) { $previous.revision } else { 0 }
        if ($ExpectedRevision -ne $revision) { throw "Progress revision changed (expected $ExpectedRevision, actual $revision)" }
        if ($Begin -and $Adopt) { throw 'Begin and Adopt are mutually exclusive' }
        if ($Begin) {
            if ($previous -and $previous.session_id -ne $SessionId -and $previous.state -ne 'completed') {
                throw 'Foreign active task: inspect and explicitly Adopt its TaskId first'
            }
            $packages=$PackagesJson | ConvertFrom-Json
            $status = [pscustomobject]@{schema_version=1; task=$Task; task_id=$TaskId; session_id=$SessionId;
                revision=($revision+1); state='running'; percent=0; current_step=$CurrentStep;
                astra_status='pending'; tests_passed=0; tests_total=0; tests_verified=$false;
                work_packages=@($packages); updated_at=[DateTimeOffset]::UtcNow.ToString('o')}
        } else {
            if (-not $previous -or $previous.task_id -ne $TaskId) { throw 'Progress task identity mismatch' }
            if (-not $Adopt -and $previous.session_id -ne $SessionId) { throw 'Progress session identity mismatch' }
            $status = $previous
            $status.session_id = $SessionId
            $status.revision++
        }
        if ($PackageId) {
            $package = @($status.work_packages | Where-Object { $_.id -ceq $PackageId })
            if ($package.Count -ne 1) { throw 'Unknown work package' }
            $package[0].percent = $PackagePercent
            # New code/work invalidates the old passing-test attestation by default.
            $status.tests_verified = $false
        } elseif ($PackagePercent -ne -1) { throw 'PackagePercent requires PackageId' }
        if ($PSBoundParameters.ContainsKey('CurrentStep')) { $status.current_step = $CurrentStep }
        if ($AstraStatus) { $status.astra_status = $AstraStatus }
        if ($State) { $status.state = $State }
        if ($TestsPassed -ne -1) { $status.tests_passed = $TestsPassed; $status.tests_verified = $false }
        if ($TestsTotal -ne -1) { $status.tests_total = $TestsTotal; $status.tests_verified = $false }
        if ($null -ne $TestsVerified) { $status.tests_verified = [bool]$TestsVerified }
        $status.updated_at = [DateTimeOffset]::UtcNow.ToString('o')
        $status.percent = Test-IMCProgressStatus $status
        $json = $status | ConvertTo-Json -Depth 8
        $bytes = [Text.UTF8Encoding]::new($false).GetBytes($json)
        if ($bytes.Length -gt $config.max_status_bytes) { throw 'Progress file too large' }
        $directory = [IO.Path]::GetDirectoryName($absolute)
        $null = [IO.Directory]::CreateDirectory($directory)
        $temporary = Join-Path $directory ('.work_progress.' + [guid]::NewGuid().ToString('N') + '.tmp')
        [IO.File]::WriteAllBytes($temporary, $bytes)
        if ([IO.File]::Exists($absolute)) { [IO.File]::Replace($temporary, $absolute, [NullString]::Value) }
        else { [IO.File]::Move($temporary, $absolute) }
        $temporary = $null
        return $status
    } finally {
        if ($temporary -and [IO.File]::Exists($temporary)) { [IO.File]::Delete($temporary) }
        if ($owned) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
}

function Format-IMCWorkProgress($Status, [string]$SessionId = '') {
    if (-not $Status) { return '[IMC WORK] --% | Warte auf gewichteten Aufgabenplan' }
    $verifiedPercent = Test-IMCProgressStatus $Status
    if ($SessionId -and $Status.session_id -ne $SessionId) { return '[IMC WORK] --% | Andere Sitzung; keine automatische Uebernahme' }
    $date = [DateTimeOffset]::Parse($Status.updated_at)
    $age = ([DateTimeOffset]::UtcNow - $date).TotalSeconds
    $stale = if ($age -gt (Get-IMCProgressConfig).stale_seconds -or $age -lt 0) { ' | STALE' } else { '' }
    $step = [regex]::Replace($Status.current_step, '[\x00-\x1f\x7f]', ' ')
    return ('[IMC WORK] {0:D2}% | {1} | Astra: {2} | Tests: {3}/{4} | {5}{6}' -f
        [int]$verifiedPercent,$step,$Status.astra_status.ToUpperInvariant(),$Status.tests_passed,$Status.tests_total,$Status.state.ToUpperInvariant(),$stale)
}

function Start-IMCProgressTitleWatcher([string]$SessionId, $RawUI, [string]$Path = $script:IMCProgressDefaultPath) {
    $shared = [hashtable]::Synchronized(@{Stop=$false})
    $runner = [powershell]::Create()
    $scriptBlock = {
        param($Helper,$StatusPath,$Owner,$TitleUI,$Shared)
        . $Helper
        while (-not $Shared.Stop) {
            try { $TitleUI.WindowTitle = Format-IMCWorkProgress (Get-IMCWorkProgress $StatusPath) $Owner } catch {}
            $null = [Threading.Thread]::Sleep([int]((Get-IMCProgressConfig).poll_seconds * 1000))
        }
    }
    try {
        $null = $runner.AddScript($scriptBlock.ToString()).AddArgument($PSCommandPath).AddArgument($Path).AddArgument($SessionId).AddArgument($RawUI).AddArgument($shared)
        $handle = $runner.BeginInvoke()
        return [pscustomobject]@{Runner=$runner; Handle=$handle; Shared=$shared}
    } catch { $runner.Dispose(); throw }
}

function Stop-IMCProgressTitleWatcher($Watcher) {
    if (-not $Watcher) { return }
    $Watcher.Shared.Stop = $true
    try {
        if (-not $Watcher.Handle.AsyncWaitHandle.WaitOne(3000)) { $Watcher.Runner.Stop() }
        $null = $Watcher.Runner.EndInvoke($Watcher.Handle)
    } catch {} finally { $Watcher.Runner.Dispose() }
}
