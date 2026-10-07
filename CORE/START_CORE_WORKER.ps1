$ErrorActionPreference = 'Stop'
. (Join-Path (Split-Path -Parent $PSScriptRoot) 'LAUNCHERS\IMC_PYTHON_COMMON.ps1')
$Python = Get-IMCPython
$ErrorActionPreference = 'Continue'
Set-Location $PSScriptRoot
$env:PYTHONPATH = $PSScriptRoot
$Root = $PSScriptRoot
$Db = Join-Path $Root "data\imc-main.db"
$Log = Join-Path $Root ("logs\imc-core-{0}.log" -f $PID)
$LogDir = Split-Path $Log
New-Item -ItemType Directory -Force (Split-Path $Db), $LogDir | Out-Null

# Log rotation: each run creates a new imc-core-<PID>.log and nothing ever
# removed the old ones (found during the 01.10.2026 cleanup audit - 8 stale
# files, ~67 MB of logs from long-dead PIDs). Keep only the newest 4 from
# previous runs before this run adds its own.
Get-ChildItem -Path $LogDir -Filter 'imc-core-*.log' -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -Skip 4 |
    Remove-Item -Force -ErrorAction SilentlyContinue

$Mutex = New-Object System.Threading.Mutex($false, 'Global\IMC_CORE_SINGLETON_V1')
$OwnsMutex = $false
try {
    try { $OwnsMutex = $Mutex.WaitOne(0, $false) }
    catch [System.Threading.AbandonedMutexException] { $OwnsMutex = $true }
    if(-not $OwnsMutex) { Write-Host 'IMC CORE already running - duplicate worker exits.' -ForegroundColor Yellow; exit 0 }

    while ($true) {
        try { "$(Get-Date -Format o) MAIN_START pid=$PID" | Out-File -FilePath $Log -Append -Encoding utf8 } catch {}
        & $Python -u -m mykonos_meme_scout --database $Db run --maximum 30 --interval 0.5 2>&1 |
            Tee-Object -FilePath $Log -Append
        $code = $LASTEXITCODE
        try { "$(Get-Date -Format o) MAIN_EXIT code=$code restart_in=10s pid=$PID" | Out-File -FilePath $Log -Append -Encoding utf8 } catch {}
        Start-Sleep -Seconds 10
    }
}
finally {
    if($OwnsMutex){ try { $Mutex.ReleaseMutex() } catch {} }
    $Mutex.Dispose()
}
