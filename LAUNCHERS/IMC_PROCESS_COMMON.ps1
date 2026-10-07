# Shared process identity. Loading this file does not start or stop services.
$script:IMCProcessRoot = Split-Path -Parent $PSScriptRoot
if(-not ('IMCSnapshotLifetime' -as [type])){Add-Type @'
using System;
using System.Runtime.InteropServices;
public sealed class IMCSnapshotLifeEvidence {
 public string State="UNVERIFIED"; public long NativeBirth; public int Error; public uint Wait=0xffffffff;
 public bool TimesRead,CloseConfirmed;
}
public static class IMCSnapshotLifetime {
 [DllImport("kernel32.dll",SetLastError=true)] static extern IntPtr OpenProcess(uint access,bool inherit,int pid);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool GetProcessTimes(IntPtr h,out long c,out long e,out long k,out long u);
 [DllImport("kernel32.dll",SetLastError=true)] static extern uint WaitForSingleObject(IntPtr h,uint ms);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool CloseHandle(IntPtr h);
 public static bool BoundEnd(uint wait,bool times,long native,long cim,bool closed){
  return wait==0 && times && native>0 && cim>0 && Math.Abs(native-cim)<10 && closed;
 }
 public static IMCSnapshotLifeEvidence Probe(int pid,long birth){
  var r=new IMCSnapshotLifeEvidence();if(pid<=0||birth<=0)return r;
  var h=OpenProcess(0x1000|0x100000,false,pid);
  if(h==IntPtr.Zero){r.Error=Marshal.GetLastWin32Error();if(r.Error==87)r.State="ABSENT_AT_PROBE";return r;}
  try {
   long exited,kernel,user,created;
   r.TimesRead=GetProcessTimes(h,out created,out exited,out kernel,out user);
   if(!r.TimesRead){r.Error=Marshal.GetLastWin32Error();return r;}
   r.NativeBirth=created;r.Wait=WaitForSingleObject(h,0);
   if(r.Wait==0xffffffff)r.Error=Marshal.GetLastWin32Error();
  }finally{r.CloseConfirmed=CloseHandle(h);if(!r.CloseConfirmed)r.Error=Marshal.GetLastWin32Error();}
  if(BoundEnd(r.Wait,r.TimesRead,r.NativeBirth,birth,r.CloseConfirmed))r.State="SIGNALED_BIRTH_BOUND";
  return r;
 }
}
'@}
if (-not ('IMCCommandLine' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class IMCCommandLine {
    [DllImport("shell32.dll", SetLastError=true)]
    static extern IntPtr CommandLineToArgvW([MarshalAs(UnmanagedType.LPWStr)] string line, out int count);
    [DllImport("kernel32.dll")] static extern IntPtr LocalFree(IntPtr ptr);
    public static string[] Split(string line) {
        int count; IntPtr ptr = CommandLineToArgvW(line, out count);
        if (ptr == IntPtr.Zero) throw new System.ComponentModel.Win32Exception();
        try {
            string[] args = new string[count];
            for (int i=0; i<count; i++) args[i] = Marshal.PtrToStringUni(Marshal.ReadIntPtr(ptr, i*IntPtr.Size));
            return args;
        } finally { LocalFree(ptr); }
    }
}
'@
}
$script:IMCScripts = @(
    @{Role='StartEntrypoint'; Kind='shell'; DevelopmentOnly=$true; NoScriptArguments=$true; Path='LAUNCHERS\START_IMC.ps1'},
    @{Role='SignalStartEntrypoint'; Kind='shell'; DevelopmentOnly=$true; NoScriptArguments=$true; Path='LAUNCHERS\START_IMC_SIGNAL_ONLY.ps1'},
    @{Role='StopEntrypoint'; Kind='shell'; DevelopmentOnly=$true; NoScriptArguments=$true; Path='LAUNCHERS\STOP_IMC.ps1'},
    @{Role='SignalStopEntrypoint'; Kind='shell'; DevelopmentOnly=$true; NoScriptArguments=$true; Path='LAUNCHERS\STOP_IMC_SIGNAL_ONLY.ps1'},
    @{Role='StatusEntrypoint'; Kind='shell'; DevelopmentOnly=$true; NoScriptArguments=$true; Path='LAUNCHERS\STATUS_IMC.ps1'},
    @{Role='WorkLauncher'; Kind='shell'; DevelopmentOnly=$true; Path='TOOLS\Start-IMC-Work.ps1'},
    @{Role='GMGNReadBroker'; Kind='shell'; DevelopmentOnly=$true; Path='TOOLS\GMGN-ReadBroker.ps1'},
    @{Role='AstraAdvisorBroker'; Kind='shell'; DevelopmentOnly=$true; Path='TOOLS\IMC-AdvisorBroker.ps1'},
    @{Role='AdvisorClient'; Kind='shell'; DevelopmentOnly=$true; Path='TOOLS\Invoke-IMC-AdvisorBroker.ps1'},
    @{Role='GMGNReadClient'; Kind='shell'; DevelopmentOnly=$true; Path='TOOLS\Invoke-GMGN-ReadBroker.ps1'},
    @{Role='SignalSupervisor'; Kind='shell'; Supervisor=$true; Path='LAUNCHERS\RUN_IMC_SIGNAL_ONLY.ps1'},
    @{Role='PaperPerformanceSupervisor'; Kind='shell'; Supervisor=$true; Path='LAUNCHERS\RUN_IMC_PAPER_PERFORMANCE.ps1'},
    @{Role='JupiterForwardSupervisor'; Kind='shell'; Supervisor=$true; Path='LAUNCHERS\RUN_JUPITER_FORWARD.ps1'},
    @{Role='RealitySupervisor'; Kind='shell'; Supervisor=$true; Path='LAUNCHERS\RUN_IMC_REALITY.ps1'},
    @{Role='RealityTerminal'; Kind='shell'; DevelopmentOnly=$true; Path='TOOLS\IMC_Terminal.ps1'},
    @{Role='RealityRuntime'; Kind='python'; Supervisor=$false; Path='RUNTIME\reality_runtime.py'},
    @{Role='RealityControlAcceptance'; Kind='shell'; DevelopmentOnly=$true; NoScriptArguments=$true; Path='tests\reality_terminal_acceptance.ps1'},
    @{Role='RealityAutoEndAcceptance'; Kind='shell'; DevelopmentOnly=$true; NoScriptArguments=$true; Path='tests\reality_auto_end_acceptance.ps1'},
    @{Role='JupiterForwardCommand'; Kind='shell'; DevelopmentOnly=$true; Path='LAUNCHERS\JUPITER_FORWARD.ps1'},
    @{Role='JupiterForward'; Kind='python'; Supervisor=$false; Path='RUNTIME\jupiter_forward_runtime.py'},
    @{Role='JupiterQuoteRead'; Kind='python'; Supervisor=$false; Path='TOOLS\imc_quote_diagnostic.py'},
    @{Role='JupiterPreparationRead'; Kind='python'; Supervisor=$false; Path='TOOLS\imc_jupiter_prepare_worker.py'},
    @{Role='LocalWalletVerification'; Kind='python'; Supervisor=$false; Path='TOOLS\imc_local_wallet_verify.py'},
    @{Role='CoreWrapper'; Kind='shell'; Supervisor=$true; Path='CORE\START_CORE_WORKER.ps1'},
    @{Role='RuntimeWatchdog'; Kind='shell'; Supervisor=$true; Path='RUNTIME\WATCHDOG_V460_TRUTH_TWIN.ps1'},
    @{Role='ResearchWatchdog'; Kind='shell'; Supervisor=$true; Path='RESEARCH\WATCHDOG_RESEARCH_V482.ps1'},
    @{Role='TwinWrapper'; Kind='shell'; Supervisor=$true; Path='RUNTIME\EXECUTOR_GATEWAY\LIVE_V490\RUN_V490_TWIN.ps1'},
    @{Role='SessionSupervisor'; Kind='shell'; Supervisor=$true; Path='RUNTIME\EXECUTOR_GATEWAY\LIVE_V490\SESSION_SUPERVISOR_V490.ps1'},
    @{Role='ManualGate'; Kind='shell'; Supervisor=$true; Path='LAUNCHERS\MANUAL_ORDER_GATE.ps1'},
    @{Role='Watcher'; Kind='python'; Supervisor=$false; Path='RUNTIME\watch_breadth_v425_shadow.py'},
    @{Role='ExecutionTwin'; Kind='python'; Supervisor=$false; Path='RUNTIME\real_market_execution_v480_build_parity.py'},
    @{Role='Learner'; Kind='python'; Supervisor=$false; Path='RUNTIME\learning_settler_v460.py'},
    @{Role='Health'; Kind='python'; Supervisor=$false; Path='RUNTIME\imc_system_health.py'},
    @{Role='PureSignalGateway'; Kind='python'; Supervisor=$false; Path='RUNTIME\EXECUTOR_GATEWAY\GMGN_BRIDGE\imc_pure_signal_gateway.py'},
    @{Role='Bridge'; Kind='python'; Supervisor=$false; Path='RUNTIME\EXECUTOR_GATEWAY\GMGN_BRIDGE\gmgn_signal_bridge.py'},
    @{Role='LiveSignalGateway'; Kind='python'; Supervisor=$false; Path='RUNTIME\EXECUTOR_GATEWAY\LIVE_V490\signal_gateway_v490.py'},
    @{Role='BuildGateway'; Kind='python'; Supervisor=$false; Path='RUNTIME\EXECUTOR_GATEWAY\gateway_v480.py'},
    @{Role='VR12Watcher'; Kind='python'; Supervisor=$false; Path='RESEARCH\watch_safe100_vr12_balanced.py'},
    @{Role='DG120Watcher'; Kind='python'; Supervisor=$false; Path='RESEARCH\watch_safe100_dg120.py'},
    @{Role='VR12Execution'; Kind='python'; Supervisor=$false; Path='RESEARCH\execution_v483_vr12_route_forward.py'},
    @{Role='LiveCalibration'; Kind='node'; Supervisor=$false; Path='RUNTIME\EXECUTOR_GATEWAY\v480_live_calibration_once.js'}
)

function Get-IMCScriptRole([string]$Path, [string]$Kind) {
    if (-not [IO.Path]::IsPathRooted($Path)) { return }
    try { $absolute = [IO.Path]::GetFullPath($Path) } catch { return }
    foreach ($definition in $script:IMCScripts) {
        if ($definition.Kind -eq $Kind -and $absolute -ieq (Join-Path $script:IMCProcessRoot $definition.Path)) {
            # Parameterless entrypoints are identities only in their original,
            # canonical absolute spelling. Do not promote drive/root-relative
            # paths or dot-segment aliases through GetFullPath.
            if ($definition.NoScriptArguments -and $Path -ine $absolute) { return }
            return $definition
        }
    }
}

function Get-IMCProcessRole($Process) {
    if (-not $Process.CommandLine) { return }
    $argv = @([IMCCommandLine]::Split([string]$Process.CommandLine))
    if ($argv.Count -lt 2) { return }
    $exe = [IO.Path]::GetFileName($argv[0]).ToLowerInvariant()
    if ($exe -ne ([string]$Process.Name).ToLowerInvariant()) { return }
    if ($exe -in @('powershell.exe','pwsh.exe')) {
        for ($i=1; $i -lt $argv.Count; $i++) {
            $arg = $argv[$i].ToLowerInvariant()
            if ($arg -eq '-file' -and $i+1 -lt $argv.Count) {
                $definition=Get-IMCScriptRole $argv[$i+1] 'shell'
                if ($definition.NoScriptArguments -and $argv.Count -ne ($i+2)) { return }
                if ($definition.Role -ceq 'SignalSupervisor') {
                    $probeArgs=@($argv | Select-Object -Skip ($i+2) | Where-Object { $_ -imatch '^-ProbeOnly' })
                    # Only one exact enabled switch identifies the read probe.
                    # False/abbreviated/duplicate switches retain the supervisor role.
                    if ($probeArgs.Count -eq 1 -and $probeArgs[0] -ceq '-ProbeOnly') {
                        return @{Role='GMGNReadProbe'; Supervisor=$true}
                    }
                }
                return $definition
            }
            if ($arg -in @('-noprofile','-noninteractive','-nologo')) { continue }
            if ($arg -in @('-executionpolicy','-windowstyle') -and $i+1 -lt $argv.Count) { $i++; continue }
            # Unknown options and inline/encoded commands are not script identities.
            return
        }
    } elseif ($exe -in @('python.exe','pythonw.exe','py.exe')) {
        for ($i=1; $i -lt $argv.Count; $i++) {
            $arg = $argv[$i]
            if ($arg -in @('-u','-B','-E','-s','-S','-I')) { continue }
            if ($exe -eq 'py.exe' -and $arg -match '^-[23](\.\d+)?$') { continue }
            if ($arg -eq '-m') {
                if($i+5 -lt $argv.Count -and $argv[$i+1] -ceq 'mykonos_meme_scout.paper_forward' -and
                    $argv[$i+2] -ceq '--database' -and $argv[$i+4] -ceq '--status-path'){
                    if($argv.Count -ne $i+15 -or $argv[$i+6] -cne '--virtual-eur' -or $argv[$i+7] -cne '1000' -or
                        $argv[$i+8] -cne '--quote-wallet' -or $argv[$i+9] -cnotmatch '\A[1-9A-HJ-NP-Za-km-z]{32,44}\z' -or
                        $argv[$i+10] -cne '--collect' -or $argv[$i+11] -cne '--duration-seconds' -or
                        $argv[$i+12] -cnotmatch '\A[1-9][0-9]{0,4}\z' -or [int]$argv[$i+12] -gt 43200 -or
                        $argv[$i+13] -cne '--drain-seconds' -or $argv[$i+14] -cnotmatch '\A(0|[1-9][0-9]{0,2})\z' -or
                        [int]$argv[$i+14] -gt 180){return}
                    try{
                        $db=[IO.Path]::GetFullPath($argv[$i+3]);$status=[IO.Path]::GetFullPath($argv[$i+5])
                        $prefix=(Join-Path $script:IMCProcessRoot 'RUNTIME\test-runs\')
                        if($argv[$i+3] -ine $db -or $argv[$i+5] -ine $status -or
                            -not $db.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase) -or
                            [IO.Path]::GetFileName($db) -cne 'paper.db' -or
                            [IO.Path]::GetFileName($status) -cne 'paper-status.json' -or
                            [IO.Path]::GetDirectoryName($db) -ine [IO.Path]::GetDirectoryName($status)){return}
                    }catch{return}
                    return @{Role='PaperForward';Supervisor=$false}
                }
                if ($i+4 -ge $argv.Count) { return }
                if ($argv[$i+1] -ceq 'mykonos_meme_scout' -and $argv[$i+2] -ceq '--database' -and
                    $argv[$i+3] -ieq (Join-Path $script:IMCProcessRoot 'CORE\data\imc-main.db') -and
                    $argv[$i+4] -ceq 'run') {
                    return @{Role='Core'; Supervisor=$false}
                }
                return
            }
            if ($arg.StartsWith('-')) { return }
            return Get-IMCScriptRole $arg 'python'
        }
    } elseif ($exe -eq 'node.exe' -and -not $argv[1].StartsWith('-')) {
        return Get-IMCScriptRole $argv[1] 'node'
    }
}

function Test-IMCTransientReadProcess($Process, $AllProcesses) {
    $handles=@()
    try {
        $argv=@([IMCCommandLine]::Split([string]$Process.CommandLine))
        $helper=Join-Path $script:IMCProcessRoot 'RUNTIME\EXECUTOR_GATEWAY\GMGN_BRIDGE\rpc_read_worker.py'
        $layout=Join-Path $script:IMCProcessRoot '.venv\pyvenv.cfg'
        if ((Get-Item -LiteralPath $layout).Length -gt 8192) { return $false }
        $lines=@(Get-Content -LiteralPath $layout -Encoding UTF8 | Where-Object { $_ -match '^executable = ' })
        if ($lines.Count -ne 1) { return $false }
        $base=[IO.Path]::GetFullPath($lines[0].Substring(13))
        if ($argv.Count -ne 2 -or $argv[0] -ine $base -or $argv[1] -ine $helper -or
            $Process.Name -ine 'python.exe' -or $Process.ExecutablePath -ine $base -or -not $Process.CreationDate) { return $false }
        $parents=@($AllProcesses | Where-Object { $_.ProcessId -eq $Process.ParentProcessId })
        if ($parents.Count -ne 1) { return $false }
        $parent=$parents[0]
        if ((Get-IMCProcessRole $parent).Role -cnotin @('PureSignalGateway','JupiterForward') -or
            -not $parent.CreationDate -or $parent.CreationDate -gt $Process.CreationDate) { return $false }
        foreach ($row in @($parent,$Process)) {
            $handle=Get-Process -Id $row.ProcessId -ErrorAction Stop
            $handles += $handle
            $null=$handle.Handle
            if ($handle.HasExited -or $handle.MainModule.FileName -ine $row.ExecutablePath -or
                [math]::Abs(($handle.StartTime.ToUniversalTime()-$row.CreationDate.ToUniversalTime()).Ticks) -ge 10) { return $false }
        }
        return @($handles | Where-Object HasExited).Count -eq 0
    } catch { return $false } finally { foreach ($handle in $handles) { $handle.Dispose() } }
}

function Test-IMCTransientLearningProcess($Process, $AllProcesses) {
    # A module name or a generic Core role is insufficient. The complete
    # canonical runtime ancestry and every native lifetime are required.
    $handles=@()
    try {
        $venv=Join-Path $script:IMCProcessRoot '.venv\Scripts\python.exe'
        $layout=Join-Path $script:IMCProcessRoot '.venv\pyvenv.cfg'
        if ((Get-Item -LiteralPath $layout -ErrorAction Stop).Length -gt 8192) { return $false }
        $lines=@(Get-Content -LiteralPath $layout -Encoding UTF8 | Where-Object { $_ -imatch '^\s*executable\s*=' })
        if ($lines.Count -ne 1 -or $lines[0] -cnotmatch '^executable = ') { return $false }
        $rawBase=$lines[0].Substring(13)
        if (-not [IO.Path]::IsPathRooted($rawBase)) { return $false }
        $base=[IO.Path]::GetFullPath($rawBase)
        if ($rawBase -ine $base -or $base -ieq $venv -or [IO.Path]::GetFileName($base) -ine 'python.exe') { return $false }
        $db=Join-Path $script:IMCProcessRoot 'CORE\data\imc-main.db'
        $mlTail=@('-m','mykonos_meme_scout','--database',$db,'ml-auto-once')
        $coreTail=@('-u','-m','mykonos_meme_scout','--database',$db,'run','--maximum','30','--interval','0.5')
        function Exact-Python($Row, [string]$Exe, $Tail) {
            if (-not $Row -or $Row.Name -ine 'python.exe' -or $Row.ExecutablePath -ine $Exe -or
                -not $Row.CommandLine -or -not $Row.CreationDate) { return $false }
            $a=@([IMCCommandLine]::Split([string]$Row.CommandLine))
            if ($a.Count -ne ($Tail.Count+1) -or $a[0] -ine $Exe) { return $false }
            for ($i=0;$i -lt $Tail.Count;$i++) {
                if ($Tail[$i] -ceq $db) { if ($a[$i+1] -ine $db) { return $false } }
                elseif ($a[$i+1] -cne $Tail[$i]) { return $false }
            }
            return $true
        }
        function Exact-Parent($Row) {
            $found=@($AllProcesses | Where-Object ProcessId -eq $Row.ParentProcessId)
            if ($found.Count -ne 1) { throw 'LEARNING_PARENT_UNKNOWN' }
            return $found[0]
        }
        function Redirector-Children($Row, $ExpectedChild, [bool]$OptionalChild) {
            $children=@($AllProcesses | Where-Object ParentProcessId -eq $Row.ProcessId)
            $consoles=@($children | Where-Object Name -ieq 'conhost.exe')
            $other=@($children | Where-Object Name -ine 'conhost.exe')
            if ($consoles.Count -gt 1 -or $other.Count -gt 1 -or
                (-not $OptionalChild -and $other.Count -ne 1)) { throw 'LEARNING_REDIRECTOR_UNKNOWN' }
            if ($other.Count -and ($null -eq $ExpectedChild -or $other[0].ProcessId -ne $ExpectedChild.ProcessId)) {
                throw 'LEARNING_REDIRECTOR_UNKNOWN'
            }
            if ($consoles.Count) {
                $console=$consoles[0]
                if ($console.ExecutablePath -ine (Join-Path ([Environment]::GetFolderPath('System')) 'conhost.exe') -or
                    -not $console.CreationDate -or $console.CreationDate -lt $Row.CreationDate) {
                    throw 'LEARNING_CONSOLE_UNKNOWN'
                }
                return $console
            }
        }
        $mlBase=$null
        if (Exact-Python $Process $venv $mlTail) { $mlVenv=$Process }
        elseif (Exact-Python $Process $base $mlTail) {
            $mlBase=$Process; $mlVenv=Exact-Parent $Process
            if (-not (Exact-Python $mlVenv $venv $mlTail)) { return $false }
        } else { return $false }
        # Absence of a BASE child is a distinct four-node case. A present but
        # invalid/ambiguous child can never fall back to that case.
        $mlChildren=@($AllProcesses | Where-Object { $_.ParentProcessId -eq $mlVenv.ProcessId -and $_.Name -ine 'conhost.exe' })
        if ($mlChildren.Count -gt 1) { return $false }
        if ($mlChildren.Count) {
            if (-not (Exact-Python $mlChildren[0] $base $mlTail) -or
                ($mlBase -and $mlBase.ProcessId -ne $mlChildren[0].ProcessId)) { return $false }
            $mlBase=$mlChildren[0]
        } elseif ($mlBase) { return $false }
        $mlConsole=Redirector-Children $mlVenv $mlBase $true
        $coreBase=Exact-Parent $mlVenv
        $coreVenv=Exact-Parent $coreBase
        $supervisor=Exact-Parent $coreVenv
        if (-not (Exact-Python $coreBase $base $coreTail) -or
            -not (Exact-Python $coreVenv $venv $coreTail)) { return $false }
        $coreConsole=Redirector-Children $coreVenv $coreBase $false
        $shell=Join-Path ([Environment]::GetFolderPath('System')) 'WindowsPowerShell\v1.0\powershell.exe'
        $runner=Join-Path $script:IMCProcessRoot 'LAUNCHERS\RUN_IMC_SIGNAL_ONLY.ps1'
        $sa=@([IMCCommandLine]::Split([string]$supervisor.CommandLine))
        $expected=@($shell,'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',$runner)
        if($sa.Count -in @(9,10,14) -and $sa[7] -ceq '-ShadowDurationSeconds' -and
            $sa[8] -cmatch '\A[1-9][0-9]{0,4}\z' -and [int]$sa[8] -le 43200){
            $expected += @('-ShadowDurationSeconds',$sa[8])
            if($sa.Count -eq 10 -and $sa[9] -ceq '-ShadowDataOnly'){$expected+='-ShadowDataOnly'}
            if($sa.Count -eq 14 -and $sa[9] -ceq '-ShadowDataOnly' -and
                $sa[10] -ceq '-ShadowMaxStorageGrowthBytes' -and
                $sa[11] -cmatch '\A[1-9][0-9]{0,9}\z' -and [long]$sa[11] -le 6442450944 -and
                $sa[12] -ceq '-ShadowStorageBaselineBytes' -and
                $sa[13] -cmatch '\A(?:0|[1-9][0-9]{0,18})\z'){
                $baselineValue=0L
                if([long]::TryParse($sa[13],[ref]$baselineValue)){
                    $expected+=@('-ShadowDataOnly','-ShadowMaxStorageGrowthBytes',$sa[11],'-ShadowStorageBaselineBytes',$sa[13])
                }
            }
        }
        if ($supervisor.Name -ine 'powershell.exe' -or $supervisor.ExecutablePath -ine $shell -or
            -not $supervisor.CreationDate -or $sa.Count -ne $expected.Count) { return $false }
        for ($i=0;$i -lt $expected.Count;$i++) {
            if ($i -in @(0,6)) { if ($sa[$i] -ine $expected[$i]) { return $false } }
            elseif ($sa[$i] -cne $expected[$i]) { return $false }
        }
        $chain=@($supervisor,$coreVenv,$coreBase,$mlVenv)
        if ($mlBase) { $chain += $mlBase }
        for ($i=1;$i -lt $chain.Count;$i++) {
            if ($chain[$i].ParentProcessId -ne $chain[$i-1].ProcessId -or
                $chain[$i].CreationDate -lt $chain[$i-1].CreationDate) { return $false }
        }
        $participants=@($chain)
        if ($mlConsole) { $participants += $mlConsole }
        if ($coreConsole) { $participants += $coreConsole }
        if (@($participants | Group-Object ProcessId | Where-Object Count -ne 1).Count -or
            @($participants | Where-Object { $_.ProcessId -le 0 }).Count) { return $false }
        foreach ($row in $participants) {
            if (@($AllProcesses | Where-Object ProcessId -eq $row.ProcessId).Count -ne 1) { return $false }
            $handle=Get-Process -Id $row.ProcessId -ErrorAction Stop
            $handles += $handle; $null=$handle.Handle
            if ($handle.HasExited -or $handle.MainModule.FileName -ine $row.ExecutablePath -or
                [math]::Abs(($handle.StartTime.ToUniversalTime()-$row.CreationDate.ToUniversalTime()).Ticks) -ge 10) { return $false }
        }
        return @($handles | Where-Object HasExited).Count -eq 0
    } catch { return $false } finally { foreach ($handle in $handles) { $handle.Dispose() } }
}

function Get-IMCProcessEvidence($Row) {
    $birth=if($Row.CreationDate){$Row.CreationDate.ToUniversalTime().ToFileTimeUtc().ToString()}else{$null}
    $evidence=[ordered]@{pid=$Row.ProcessId;parent_pid=$Row.ParentProcessId;name=$Row.Name;
        executable_path=$Row.ExecutablePath;creation_filetime=$birth;native_birth_filetime=$null;
        native_bound=$false;native_exited=$null;native_exit_code=$null;metadata_gaps=@()}
    foreach($key in @('Name','ExecutablePath','CreationDate')){if(-not $Row.$key){$evidence.metadata_gaps+=('MISSING_'+$key.ToUpperInvariant())}}
    $handle=$null
    try {
        $handle=Get-Process -Id $Row.ProcessId -ErrorAction Stop;$null=$handle.Handle
        $evidence.native_birth_filetime=$handle.StartTime.ToUniversalTime().ToFileTimeUtc().ToString()
        $evidence.native_bound=($null -ne $birth -and [math]::Abs([long]$birth-[long]$evidence.native_birth_filetime) -lt 10)
        $evidence.native_exited=$handle.HasExited
        if($evidence.native_exited){$evidence.native_exit_code=$handle.ExitCode}
    }catch{$evidence.metadata_gaps+='NATIVE_IDENTITY_UNAVAILABLE'}
    finally{if($handle){$handle.Dispose()}}
    return [pscustomobject]$evidence
}
function Get-IMCUnknownEvidence($Row,$All,[string]$Reason,$Arguments=@(),[bool]$ModuleMatch=$false,[bool]$RelativeMatch=$false) {
    try {
    $evidence=Get-IMCProcessEvidence $Row
    $paths=@()
    foreach($arg in $Arguments){foreach($definition in $script:IMCScripts){
        $absolute=Join-Path $script:IMCProcessRoot $definition.Path
        if($arg -ieq $absolute -or $arg -ieq $definition.Path -or $arg -ieq $definition.Path.Replace('\','/')){$paths+=$arg;break}
    }}
    $parents=@($All|Where-Object ProcessId -eq $Row.ParentProcessId)
    $parent=if($parents.Count -eq 1){Get-IMCProcessEvidence $parents[0]}else{$null}
    $evidence | Add-Member -NotePropertyName reason -NotePropertyValue $Reason
    $evidence | Add-Member -NotePropertyName registered_script_arguments -NotePropertyValue @($paths)
    $evidence | Add-Member -NotePropertyName module_match -NotePropertyValue $ModuleMatch
    $evidence | Add-Member -NotePropertyName relative_script_match -NotePropertyValue $RelativeMatch
    $evidence | Add-Member -NotePropertyName parent -NotePropertyValue $parent
    if(-not $parent){$evidence.metadata_gaps+='PARENT_IDENTITY_UNAVAILABLE'}
    return $evidence
    }catch{
        # Diagnostic failure never changes the original unknown-PID decision.
        return [pscustomobject]@{pid=$Row.ProcessId;parent_pid=$Row.ParentProcessId;reason=$Reason;
            metadata_gaps=@('DIAGNOSTIC_METADATA_UNAVAILABLE');registered_script_arguments=@();
            module_match=$ModuleMatch;relative_script_match=$RelativeMatch;parent=$null}
    }
}
function Get-IMCProcessSnapshot {
    $identified = @(); $unknown = @();$unknownEvidence=@();$expiredEvidence=@()
    $allProcesses=@(Get-CimInstance Win32_Process -ErrorAction Stop)
    $snapshotAt=[DateTimeOffset]::UtcNow.ToString('o');$snapshotId=[guid]::NewGuid().ToString('N')
    foreach ($process in $allProcesses) {
        if ($process.Name -notin @('python.exe','pythonw.exe','py.exe','powershell.exe','pwsh.exe','node.exe')) { continue }
        if (-not $process.CommandLine) {
            # CIM may retain a row after its process has ended. Never infer an
            # exemption from a role, parent, age or PID alone.
            $expired=Get-IMCExpiredSnapshotEvidence $process $snapshotId
            if($null -ne $expired){$expiredEvidence+=$expired;continue}
            $unknown += $process.ProcessId
            $unknownEvidence+=Get-IMCUnknownEvidence $process $allProcesses 'NO_COMMANDLINE'
            continue
        }
        $role = Get-IMCProcessRole $process
        if ($role) {
            if ($role.DevelopmentOnly) { continue }
            $identified += [pscustomobject]@{ProcessId=$process.ProcessId; CreationDate=$process.CreationDate;
                Role=$role.Role; Supervisor=[bool]$role.Supervisor; Process=$process}
        } elseif ($process.Name -ieq 'python.exe' -and (Test-IMCTransientReadProcess $process $allProcesses)) {
            $identified += [pscustomobject]@{ProcessId=$process.ProcessId; CreationDate=$process.CreationDate;
                Role='RPCReadHelper'; Supervisor=$false; Process=$process}
        } elseif ($process.Name -ieq 'python.exe' -and (Test-IMCTransientLearningProcess $process $allProcesses)) {
            $identified += [pscustomobject]@{ProcessId=$process.ProcessId; CreationDate=$process.CreationDate;
                Role='MLAutoOnce'; Supervisor=$false; Process=$process}
        } else {
            # Readable but unrecognized repository scripts still conflict.
            # A Codex -C directory argument is not itself a runtime script.
            try {
                $arguments=@([IMCCommandLine]::Split([string]$process.CommandLine))
                $rootPrefix=$script:IMCProcessRoot.TrimEnd('\') + '\'
                $repoScript=@($arguments | Where-Object {
                    $_ -match '^[A-Za-z]:[\\/]' -and [IO.Path]::GetFullPath($_).StartsWith($rootPrefix,[StringComparison]::OrdinalIgnoreCase) -and
                    [IO.Path]::GetExtension($_).ToLowerInvariant() -in @('.py','.ps1','.js','.mjs')
                }).Count -gt 0
                $repoModule=$arguments -ccontains 'mykonos_meme_scout'
                $relativeRepoScript=@($arguments | Where-Object {
                    $_ -match '^[A-Za-z0-9_.-]+(?:[\\/][A-Za-z0-9_.-]+)*\.(?:py|ps1|js|mjs)$' -and
                    (Test-Path -LiteralPath (Join-Path $script:IMCProcessRoot $_) -PathType Leaf)
                }).Count -gt 0
                if ($repoScript -or $repoModule -or $relativeRepoScript) {
                    $unknown += $process.ProcessId
                    $unknownEvidence+=Get-IMCUnknownEvidence $process $allProcesses 'UNRECOGNIZED_REPOSITORY_ARGUMENT' $arguments $repoModule $relativeRepoScript
                }
            } catch {
                $unknown += $process.ProcessId
                $unknownEvidence+=Get-IMCUnknownEvidence $process $allProcesses 'ARGUMENT_INSPECTION_FAILURE'
            }
        }
    }
    $redirectors=@(); $workerView=@($identified)
    $venv=Join-Path $script:IMCProcessRoot '.venv\Scripts\python.exe'
    $layout=Join-Path $script:IMCProcessRoot '.venv\pyvenv.cfg'
    $base=$null
    try {
        if ((Get-Item -LiteralPath $layout -ErrorAction Stop).Length -le 8192) {
            $lines=@(Get-Content -LiteralPath $layout -Encoding UTF8 | Where-Object { $_ -match '^executable = ' })
            if ($lines.Count -eq 1) { $base=[IO.Path]::GetFullPath($lines[0].Substring(13)) }
        }
    } catch { $base=$null }
    if ($base) {
        foreach ($parent in $identified) {
            if ($parent.Supervisor -or $parent.Process.ExecutablePath -ine $venv) { continue }
            $directChildren=@($allProcesses | Where-Object { $_.ParentProcessId -eq $parent.ProcessId })
            $consoles=@($directChildren | Where-Object { $_.Name -ieq 'conhost.exe' })
            $workerChildren=@($directChildren | Where-Object { $_.Name -ine 'conhost.exe' })
            if ($consoles.Count -gt 1 -or $workerChildren.Count -ne 1) { continue }
            $console=$null
            if ($consoles.Count) {
                $console=$consoles[0]
                $systemConsole=Join-Path ([Environment]::GetFolderPath('System')) 'conhost.exe'
                if (-not $console.CreationDate -or $console.ExecutablePath -ine $systemConsole -or
                    $console.CreationDate -lt $parent.CreationDate) { continue }
            }
            $children=@($identified | Where-Object {
                $_.ProcessId -eq $workerChildren[0].ProcessId -and
                $_.Process.ParentProcessId -eq $parent.ProcessId -and $_.Role -ceq $parent.Role -and
                $_.Process.ExecutablePath -ieq $base -and $_.CreationDate -ge $parent.CreationDate
            })
            if ($children.Count -ne 1) { continue }
            $child=$children[0]
            $pa=@([IMCCommandLine]::Split($parent.Process.CommandLine))
            $ca=@([IMCCommandLine]::Split($child.Process.CommandLine))
            if ($pa.Count -ne $ca.Count -or $pa[0] -ine $venv -or $ca[0] -ine $base) { continue }
            $same=$true
            for ($i=1;$i -lt $pa.Count;$i++) { if ($pa[$i] -cne $ca[$i]) { $same=$false; break } }
            if (-not $same) { continue }
            # Bind both native handles to the creation times in this snapshot.
            # Missing/changed evidence retains both records and the alarm.
            $participants=@($parent,$child)
            if ($console) { $participants += [pscustomobject]@{ProcessId=$console.ProcessId; CreationDate=$console.CreationDate; Process=$console} }
            $handles=@()
            try {
                foreach ($row in $participants) {
                    $handle=Get-Process -Id $row.ProcessId -ErrorAction Stop
                    $handles += $handle
                    $null=$handle.Handle
                    if ($handle.HasExited -or $handle.MainModule.FileName -ine $row.Process.ExecutablePath -or
                        $handle.StartTime.ToUniversalTime() -lt $parent.CreationDate.ToUniversalTime() -or
                        [math]::Abs(($handle.StartTime.ToUniversalTime()-$row.CreationDate.ToUniversalTime()).Ticks) -ge 10) { $same=$false }
                }
                # All handles remain owned until the final liveness decision.
                if (@($handles | Where-Object HasExited).Count) { $same=$false }
                if ($same) {
                    $redirectors += [pscustomobject]@{ProcessId=$parent.ProcessId; CreationDate=$parent.CreationDate;
                        Role=$parent.Role; WorkerPid=$child.ProcessId; Evidence='VENV_NATIVE_HANDLE_ARGV_BINDING';
                        ConsolePid=$(if ($console) {$console.ProcessId} else {$null}); ConsoleCreated=$(if ($console) {$console.CreationDate} else {$null})}
                    $workerView=@($workerView | Where-Object { $_.ProcessId -ne $parent.ProcessId })
                }
            } catch { $same=$false } finally {
                foreach ($handle in $handles) { $handle.Dispose() }
            }
        }
    }
    [pscustomobject]@{Processes=@($workerView); InterpreterRedirectors=@($redirectors); UnknownInterpreterPids=@($unknown);
        UnknownInterpreterProcesses=@($unknownEvidence);ExpiredProcessEvidence=@($expiredEvidence);SnapshotAt=$snapshotAt;SnapshotId=$snapshotId}
}

function Get-IMCExpiredSnapshotEvidence($Row,[string]$SnapshotId) {
    if($Row.ProcessId -isnot [int] -and $Row.ProcessId -isnot [uint32] -and $Row.ProcessId -isnot [long]){return}
    if($Row.ProcessId -le 0 -or $Row.ProcessId -gt [int]::MaxValue -or $Row.CreationDate -isnot [datetime] -or
        $Row.CreationDate.ToUniversalTime() -gt [datetime]::UtcNow -or $Row.CreationDate.Year -lt 1601){return}
    try {
        $birth=$Row.CreationDate.ToUniversalTime().ToFileTimeUtc()
        $life=[IMCSnapshotLifetime]::Probe([int]$Row.ProcessId,$birth)
        if($life.State -cnotin @('ABSENT_AT_PROBE','SIGNALED_BIRTH_BOUND')){return}
        return [pscustomobject]@{pid=$Row.ProcessId;parent_pid=$Row.ParentProcessId;name=$Row.Name;
            snapshot_id=$SnapshotId;probe_at=[DateTimeOffset]::UtcNow.ToString('o');decision_reason=$life.State;
            cim_birth_filetime=$birth.ToString();native_birth_filetime=$(if($life.NativeBirth){$life.NativeBirth.ToString()}else{$null});
            native_error=$life.Error;native_wait=$life.Wait;native_close_confirmed=$life.CloseConfirmed}
    }catch{return}
}

function Test-IMCSameProcess($Expected, $Current) {
    return ($null -ne $Current -and $null -ne $Expected.CreationDate -and
        $Expected.ProcessId -eq $Current.ProcessId -and $Expected.CreationDate -eq $Current.CreationDate -and
        $Expected.Role -eq $Current.Role)
}

function Stop-IMCIdentifiedProcess($Expected) {
    $fresh = Get-IMCProcessSnapshot
    $current = @($fresh.Processes | Where-Object { $_.ProcessId -eq $Expected.ProcessId })
    if ($current.Count -eq 0) { return }
    if ($current.Count -ne 1 -or -not (Test-IMCSameProcess $Expected $current[0])) {
        throw "Process identity changed; stop blocked for PID $($Expected.ProcessId)"
    }
    $handle = Get-Process -Id $Expected.ProcessId -ErrorAction Stop
    # Open the handle and compare its start time before terminating that object.
    $null = $handle.Handle
    if ([math]::Abs(($handle.StartTime.ToUniversalTime() - $Expected.CreationDate.ToUniversalTime()).Ticks) -ge 10) {
        throw "Process creation time changed; stop blocked for PID $($Expected.ProcessId)"
    }
    Stop-Process -InputObject $handle -ErrorAction Stop
    $handle.WaitForExit(5000) | Out-Null
    if (-not $handle.HasExited) { throw "PID $($Expected.ProcessId) did not exit" }
}

function Stop-IMCRoles([string[]]$Roles = @()) {
    $snapshot = Get-IMCProcessSnapshot
    if ($snapshot.UnknownInterpreterPids.Count -gt 0) {
        throw "Process command lines unavailable; stop blocked. Unknown PIDs: $($snapshot.UnknownInterpreterPids -join ', ')"
    }
    $targets = @($snapshot.Processes | Where-Object { $Roles.Count -eq 0 -or $_.Role -in $Roles })
    foreach ($process in @($targets | Where-Object Supervisor)) { Stop-IMCIdentifiedProcess $process }
    # Supervisors have exited; now collect their children afresh.
    $snapshot = Get-IMCProcessSnapshot
    foreach ($process in @($snapshot.Processes | Where-Object { $Roles.Count -eq 0 -or $_.Role -in $Roles })) {
        Stop-IMCIdentifiedProcess $process
    }
    $after = Get-IMCProcessSnapshot
    $remaining = @($after.Processes | Where-Object { $Roles.Count -eq 0 -or $_.Role -in $Roles })
    if ($after.UnknownInterpreterPids.Count -gt 0 -or $remaining.Count -gt 0) {
        throw "Stop not confirmed: remaining roles=$($remaining.Role -join ', '); unknown PIDs=$($after.UnknownInterpreterPids -join ', ')"
    }
}

function Publish-IMCStoppedStatus {
    # Only the canonical full STOP calls this. Preserve historical bytes before
    # replacing projections; native absence does not certify wallet/chain state.
    $snapshot=Get-IMCProcessSnapshot
    if ($snapshot.Processes.Count -or $snapshot.UnknownInterpreterPids.Count) {
        throw 'STOP_STATUS_NATIVE_ABSENCE_UNVERIFIED'
    }
    $config=Get-Content -LiteralPath (Join-Path $script:IMCProcessRoot 'config\imc-execution.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if (@(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object LocalPort -eq $config.port).Count) {
        throw 'STOP_STATUS_GATEWAY_STILL_LISTENING'
    }
    $observed=[DateTimeOffset]::UtcNow.ToString('o')
    $runtime=Join-Path $script:IMCProcessRoot 'RUNTIME'
    $archive=Join-Path $script:IMCProcessRoot ('.local-tools\status-history\'+[guid]::NewGuid().ToString('N'))
    $null=[IO.Directory]::CreateDirectory($archive)
    $signal=@{schema_version=1;updated_at=$observed;state='STOPPED';code='NATIVE_STOP_CONFIRMED';
        pid=0;workers=@{};execution_mode='SIGNAL_ONLY';broadcast=$false;live_ready=$false;
        evidence='NATIVE_PROCESS_ABSENCE_AND_GATEWAY_PORT_CLOSED';chain_state='NOT_VERIFIED'}
    $health=@{version='2.0';updated=$observed;verdict='DEGRADED';runtime_state='STOPPED';
        authority_guard='UNKNOWN';live_execution_any=$null;configured_mode='PURE_SIGNAL_ONLY';
        execution_evidence='Native process absence and closed gateway port; chain state not verified.';
        process_snapshot=@{processes=@();unknown_interpreter_pids=@();error=$null;visibility='RECOGNIZED_ABSOLUTE_PATHS'};
        warnings=@('Runtime stopped; no current component health or market evidence.');critical=@();
        info=@('Previous status bytes preserved under .local-tools/status-history.');statuses=@();databases=@();execution_errors=@{}}
    foreach($entry in @(@{name='signal-runtime-status.json';value=$signal},@{name='IMC_SYSTEM_HEALTH.json';value=$health})) {
        $path=Join-Path $runtime $entry.name
        if ([IO.File]::Exists($path)) {
            if ((Get-Item -LiteralPath $path).Length -gt 524288) { throw 'STOP_STATUS_PROJECTION_SIZE' }
            [IO.File]::WriteAllBytes((Join-Path $archive $entry.name),[IO.File]::ReadAllBytes($path))
        }
        $temporary=$path+'.'+[guid]::NewGuid().ToString('N')+'.tmp'
        [IO.File]::WriteAllText($temporary,($entry.value|ConvertTo-Json -Depth 6),[Text.UTF8Encoding]::new($false))
        if ([IO.File]::Exists($path)) { [IO.File]::Replace($temporary,$path,[NullString]::Value) }
        else { [IO.File]::Move($temporary,$path) }
    }
}
