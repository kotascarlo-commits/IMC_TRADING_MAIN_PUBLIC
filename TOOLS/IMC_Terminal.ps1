param([Parameter(Position=0)][string]$Group='t',[Parameter(Position=1)][string]$Command='status',
    [Parameter(Position=2)][string]$Duration,[switch]$Viewer,[switch]$RenderOnly,[switch]$Reality)
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
. (Join-Path $root 'LAUNCHERS\IMC_PYTHON_COMMON.ps1')
$python=Get-IMCPythonPath
$entry=Join-Path $root 'RUNTIME\reality_runtime.py'
. (Join-Path $PSScriptRoot 'IMC_TERMINAL_REFRESH.ps1')
function Get-RealityStatus {
    $snapshot=Get-IMCShadowDisplay $root
    if($null -ne $snapshot -and -not ($Reality -and $snapshot.status -ceq 'STOPPED')){return $snapshot}
    $value=@(& $python $entry status)
    if($LASTEXITCODE -ne 0 -or $value.Count -ne 1){throw 'REALITY_STATUS_UNKNOWN'}
    return ($value[0] | ConvertFrom-Json)
}
function Format-IMCMoney($Value) {
    if($null -eq $Value){return 'UNKNOWN'}
    return ([decimal]$Value).ToString('N2',[Globalization.CultureInfo]::GetCultureInfo('de-DE'))+' '+[char]0x20ac
}
function Format-IMCNumber($Value) {
    if($null -eq $Value){return 'N/A'}
    return ([decimal]$Value).ToString('N3',[Globalization.CultureInfo]::GetCultureInfo('de-DE'))
}
function Format-IMCTime($Value) {
    if($null -eq $Value){return 'N/A'}
    return [DateTimeOffset]::FromUnixTimeMilliseconds([long]([double]$Value*1000)).ToLocalTime().ToString('dd.MM.yyyy HH:mm:ss')
}
function Initialize-IMCConsole {
    # RGB #b9793c is the only accent. Enable VT once, outside the refresh loop.
    if(-not ('IMCTerminalVT' -as [type])){Add-Type @'
using System; using System.Runtime.InteropServices; using System.ComponentModel;
public static class IMCTerminalVT {
 [DllImport("kernel32.dll")] static extern IntPtr GetStdHandle(int n);
 [DllImport("kernel32.dll")] static extern bool GetConsoleMode(IntPtr h,out uint m);
 [DllImport("kernel32.dll")] static extern bool SetConsoleMode(IntPtr h,uint m);
 public static bool Enable(){uint m;var h=GetStdHandle(-11);return GetConsoleMode(h,out m)&&SetConsoleMode(h,m|4);}
 [StructLayout(LayoutKind.Sequential)] struct Coord {public short X,Y;public Coord(int x,int y){X=(short)x;Y=(short)y;}}
 [StructLayout(LayoutKind.Sequential)] struct Rect {public short Left,Top,Right,Bottom;}
 [StructLayout(LayoutKind.Sequential)] struct BufferInfo {public Coord Size,Cursor;public ushort Attributes;public Rect Window;public Coord Maximum;}
 [StructLayout(LayoutKind.Explicit,CharSet=CharSet.Unicode)] struct Cell {
  [FieldOffset(0)] public char Character;[FieldOffset(2)] public ushort Attributes;
 }
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool GetConsoleScreenBufferInfo(IntPtr h,out BufferInfo b);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool WriteConsoleOutputW(IntPtr h,Cell[] b,Coord size,Coord at,ref Rect r);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool SetConsoleCursorPosition(IntPtr h,Coord at);
 public static void WriteFrame(string[] lines,bool[] accent,int cursor){
  var h=GetStdHandle(-11);BufferInfo b;if(!GetConsoleScreenBufferInfo(h,out b))throw new Win32Exception();
  int width=b.Window.Right-b.Window.Left+1,height=Math.Min(lines.Length,b.Window.Bottom-b.Window.Top+1);
  var cells=new Cell[width*height];
  for(int y=0;y<height;y++)for(int x=0;x<width;x++){
   var line=lines[y]??"";cells[y*width+x]=new Cell{Character=x<line.Length?line[x]:' ',Attributes=(ushort)(accent[y]?6:7)};
  }
  var rect=new Rect{Left=b.Window.Left,Top=b.Window.Top,Right=b.Window.Right,Bottom=(short)(b.Window.Top+height-1)};
  if(!WriteConsoleOutputW(h,cells,new Coord(width,height),new Coord(0,0),ref rect))throw new Win32Exception();
  if(!SetConsoleCursorPosition(h,new Coord(b.Window.Left+Math.Min(cursor,width-1),b.Window.Top+height-1)))throw new Win32Exception();
 }
}
'@}
    return [IMCTerminalVT]::Enable()
}
function Get-IMCHeader {
    return (@'
██╗███╗   ███╗ ██████╗
██║████╗ ████║██╔════╝
██║██╔████╔██║██║
██║██║╚██╔╝██║██║
██║██║ ╚═╝ ██║╚██████╗
╚═╝╚═╝     ╚═╝ ╚═════╝
'@ -split '\r?\n')
}
function Get-RealityLines($s) {
    $s=Update-IMCDiagnosticFreshness $s
    Get-IMCHeader
    Write-Output ('STATUS: '+$s.status)
    if($s.view_error){Write-Output ('Statusabfrage: '+$s.view_error)}
    if($s.view_received_at){Write-Output ('Datenstand:       '+(Format-IMCTime $s.view_received_at))}
    if($s.runtime_health){Write-Output ('Runtime Health:   '+$s.runtime_health+' | Heartbeat Age: '+(Format-IMCNumber $s.heartbeat_age_seconds)+' s')}
    if($s.diagnostic){
        Write-Output 'STATISTIK-NACHTLAUF | DATA_ONLY_SHADOW | READ ONLY'
        if($s.status -ne 'UNKNOWN'){
            Write-Output ('Run ID:           '+$s.run_id)
            Write-Output ('Startzeit:        '+(Format-IMCTime $s.started_at))
            Write-Output ('Geplantes Ende:   '+(Format-IMCTime $s.planned_end))
            Write-Output ('Bereits gelaufen: '+[TimeSpan]::FromSeconds($s.elapsed_seconds))
            Write-Output ('Restzeit:         '+[TimeSpan]::FromSeconds($s.remaining_seconds))
            if($s.historical){Write-Output 'GESPEICHERTER LAUF: Beobachtungen/Gates historisch; kein Nachweis aktueller Prozessbereinigung'}
            else{
                Write-Output ('System Health:    '+$s.health_verdict)
                foreach($message in @($s.health_messages)){Write-Output ('Health-Meldung:    '+[regex]::Replace([string]$message,'[\x00-\x1f\x7f]',' '))}
            }
            Write-Output ('Scan-Zyklen:      '+$s.scan_cycle_id)
            $o=$s.observation
            Write-Output ('Statistik-Status: '+$s.observation_state)
            if($o){
                Write-Output ('Letzte Daten-ID:  '+$o.latest_id+' | Datenalter: '+(Format-IMCNumber $s.source_age_seconds)+' s')
                Write-Output ('Shadow-Ziele:     '+$o.shadow_targets+' | Beobachtet: '+$o.shadow_observed)
                Write-Output ('Security geprueft: '+$o.security_checked+' / '+$o.sample_size+' | Holderdaten: '+$o.holder_values_present+' / '+$o.sample_size)
                Write-Output ('Speicherwachstum: '+(Format-IMCNumber ($s.storage_growth_bytes/1GB))+' / '+(Format-IMCNumber ($s.max_storage_growth_bytes/1GB))+' GiB')
                Write-Output ('Freier Speicher: '+(Format-IMCNumber ($o.disk_free_bytes/1GB))+' GiB')
                Write-Output ('Watcher-Fehler:   '+@($o.watch_errors).Count+' | Feed komplett: '+$o.feed_complete)
            }else{Write-Output 'Beobachtungen: keine aktuellen verifizierten Statistikwerte'}
            Write-Output ('Laufmeldung:      '+$s.stop_reason)
        }
        Write-Output 'PnL / Trefferquote / Profit Factor: N/A (keine Trade-Performance in diesem Lauf)'
        Write-Output 'status | refresh | exit (Diagnoseansicht; start/stop gesperrt)'
        if($s.status -eq 'UNKNOWN'){Write-Output 'Gespeicherte Gates: UNKNOWN (Status nicht verifiziert)'}
        else{Write-Output 'Gespeicherter Vertrag: OWNER OFF | BROADCAST OFF | SIGNING OFF | LIVE OFF'}
        return
    }
    Write-Output 'FORWARD / SHADOW / NO BROADCAST | EUR | modeled observations, no chain fills'
    Write-Output ('Run ID:           '+$s.run_id)
    Write-Output ('Startzeit:        '+(Format-IMCTime $s.started_at))
    Write-Output ('Gewaehlte Laufzeit: '+$s.duration)
    Write-Output ('Geplantes Ende:   '+(Format-IMCTime $s.planned_end))
    Write-Output ('Bereits gelaufen: '+[TimeSpan]::FromSeconds([double]$s.elapsed_seconds))
    Write-Output ('Restzeit:         '+[TimeSpan]::FromSeconds([double]$s.remaining_seconds))
    Write-Output ('Startguthaben:    '+(Format-IMCMoney $s.start_equity_eur))
    Write-Output ('Aktuelle Equity:  '+(Format-IMCMoney $s.equity_eur))
    Write-Output ('Freies Kapital:   '+(Format-IMCMoney $s.cash_eur))
    $m=$s.metrics
    foreach($pair in @(@('Gross PnL','gross_pnl_eur'),@('Net PnL','net_pnl_eur'),@('Gesamtkosten','costs_eur'),
        @('Execution Costs','execution_costs_eur'),@('Netzwerkgebuehren','network_fees_eur'),@('Slippage (enthalten)','slippage_eur'),
        @('Price Impact (enthalten)','price_impact_eur'),@('Sonstige Kosten','other_verified_costs_eur'),
        @('Max Drawdown','max_drawdown_eur'),@('Top-3 Gewinnbeitrag','top3_contribution_eur'))){
        Write-Output ($pair[0].PadRight(26)+': '+(Format-IMCMoney $m.($pair[1])))
    }
    foreach($pair in @(@('Net PnL %','net_pnl_pct'),@('Max Drawdown %','max_drawdown_pct'),@('Gross Profit Factor','gross_pf'),
        @('Net Profit Factor','net_pf'),@('PF ohne Top-3','net_pf_without_top3'))){
        Write-Output ($pair[0].PadRight(26)+': '+(Format-IMCNumber $m.($pair[1])))
    }
    Write-Output ('Trefferquote:      '+$(if($null -eq $m.win_rate){'N/A'}else{(Format-IMCNumber ([decimal]$m.win_rate*100))+' %'}))
    Write-Output ('Top-3 Anteil Gewinn: '+$(if($null -eq $m.top3_profit_share){'N/A'}else{(Format-IMCNumber ([decimal]$m.top3_profit_share*100))+' %'}))
    Write-Output ('Trades CLOSED:    '+$m.closed+' | Wins: '+$m.wins+' | Losses: '+$m.losses)
    Write-Output ('UNKNOWN / Rejected: '+$s.unknown+' / '+$s.rejected)
    Write-Output ('Recovery Events:  '+$s.recovery_events+' | Runtime Errors: '+$s.runtime_errors)
    Write-Output ('Stop Reason:      '+$s.stop_reason)
    if($s.entry_stop_reason){Write-Output ('Entries stopped:  '+$s.entry_stop_reason+' | run continues until selected end')}
    if($s.ended_at){
        Write-Output ('Ende:             '+(Format-IMCTime $s.ended_at))
        Write-Output ('End Equity:       '+(Format-IMCMoney $s.equity_eur))
        Write-Output ('Best / Worst:     '+$m.best_trade+' / '+$m.worst_trade)
    }
    $p=$s.open_position
    if($p){
        Write-Output '';Write-Output 'OPEN POSITION'
        Write-Output ('Coin: '+$p.symbol+' | Mint: '+$p.mint)
        Write-Output ('Entry Time: '+(Format-IMCTime $p.entry_at)+' | Entry Price: '+(Format-IMCNumber $p.entry_price))
        Write-Output ('Einsatz: '+(Format-IMCMoney $p.stake_eur)+' | Current Value: '+(Format-IMCMoney $p.last_mark.value_eur))
        Write-Output ('Gross PnL: '+(Format-IMCMoney $p.last_mark.gross_eur)+' | Estimated Exit Costs: '+
            (Format-IMCMoney $p.last_mark.estimated_exit_costs_eur)+' | Net PnL: '+(Format-IMCMoney $p.last_mark.net_eur))
        Write-Output ('Hold Time: '+[TimeSpan]::FromSeconds([Math]::Max(0,([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000)-$p.entry_at))+' | Exit State: '+$p.execution_state)
    }
    Write-Output '';Write-Output '# | COIN | MINT | ENTRY | EXIT | EINSATZ | ENTRY PRICE | EXIT PRICE | GROSS % | KOSTEN | NET % | NET EUR | EXIT'
    $i=0
    foreach($t in @($s.trades)){$i++;Write-Output ((@($i,$t.symbol,$t.mint,(Format-IMCTime $t.entry_at),(Format-IMCTime $t.exit_at),
        (Format-IMCMoney $t.stake_eur),(Format-IMCNumber $t.entry_price),(Format-IMCNumber $t.exit_price),
        (Format-IMCNumber $t.gross_pct),(Format-IMCMoney $t.costs_eur),(Format-IMCNumber $t.net_pct),
        (Format-IMCMoney $t.net_eur),$t.exit_reason)) -join ' | ')}
    Write-Output '';Write-Output 'start <dauer> | start | stop | status | refresh | exit'
    Write-Output 'OWNER OFF | BROADCAST OFF | SIGNING OFF | LIVE OFF'
}
function Show-Reality($s) {
    $lines=@(Get-RealityLines $s)
    $vt=Initialize-IMCConsole
    $text=$lines -join [Environment]::NewLine
    if($vt){$text=([char]27)+'[38;2;185;121;60m'+(($lines[0..5]) -join [Environment]::NewLine)+([char]27)+'[0m'+[Environment]::NewLine+(($lines[6..($lines.Count-1)]) -join [Environment]::NewLine)}
    Write-Host $text
}
function Start-Reality([string]$Chosen) {
    Assert-IMCRealityControl
    if($Chosen -cnotmatch '\A[1-9][0-9]{0,5}[mh]\z'){throw 'Laufzeit: positive Minuten oder Stunden, z.B. 30m oder 8h'}
    $n=[long]$Chosen.Substring(0,$Chosen.Length-1)
    $seconds=$n*$(if($Chosen.EndsWith('h')){3600}else{60})
    if($seconds -gt 2678400){throw 'Maximale Laufzeit: 31 Tage'}
    $request=[guid]::NewGuid().ToString('N')
    $launcher=Join-Path $root 'LAUNCHERS\RUN_IMC_REALITY.ps1'
    $result=Join-Path $root ('RUNTIME\reality-start-'+$request+'.json')
    $args=@('-NoProfile','-ExecutionPolicy','Bypass','-File',('"'+$launcher+'"'),'-Duration',$Chosen,'-RequestId',$request)
    $process=Start-Process powershell.exe -ArgumentList $args -WindowStyle Hidden -PassThru
    try {
        $deadline=[DateTime]::UtcNow.AddSeconds(25)
        while([DateTime]::UtcNow -lt $deadline -and -not(Test-Path -LiteralPath $result) -and -not $process.HasExited){Start-Sleep -Milliseconds 150}
        if(-not(Test-Path -LiteralPath $result)){throw 'Start nicht bestaetigt; imc t status pruefen. Kein zweiter Start.'}
        $answer=Get-Content -LiteralPath $result -Raw | ConvertFrom-Json
        if($answer.status -ne 'STARTED'){throw $answer.reason}
        if($answer.recovery_only){Write-Host ('RECOVERY EXIT STARTED: '+$answer.run_id+' | no new run | NO BROADCAST')}
        else{Write-Host ('RUN STARTED: '+$answer.run_id+' | '+$Chosen+' | 1.000,00 EUR virtual | NO BROADCAST')}
    } finally {$process.Dispose()}
}
function Assert-IMCRealityControl {
    $snapshot=Get-IMCShadowDisplay $root
    if($null -ne $snapshot -and -not ($Reality -and $snapshot.status -ceq 'STOPPED')){throw 'Diagnoseansicht ist read-only. Nachtlauf endet automatisch. Nach STOPPED: Reality-Modus explizit mit -Reality waehlen.'}
}
function Stop-Reality {
    Assert-IMCRealityControl
    & $python $entry stop | Out-Null
    if($LASTEXITCODE -ne 0){throw 'REALITY_STOP_FAILED'}
}
function Read-IMCViewLine([string]$Prompt='imc t') {
    if([Console]::IsInputRedirected){return Read-Host $Prompt}
    $line=[Text.StringBuilder]::new()
    $clock=[Diagnostics.Stopwatch]::StartNew();$nextRead=0.0;$nextFrame=0.0
    $snapshot=New-IMCUnknownStatus;$read=$null;$previous=$null;$offset=0
    $vt=Initialize-IMCConsole;$lines=@();$dirty=$true
    try {
        while($true) {
            $now=$clock.Elapsed.TotalSeconds
            if($null -eq $read -and $now -ge $nextRead){
                try {$read=Start-IMCStatusRead -Python $python -Entry $entry -Root $(if($script:IMCDisplayRoot){$script:IMCDisplayRoot}else{''}) -Reality:$Reality}
                catch {$snapshot=New-IMCUnknownStatus 'Statusabfrage konnte nicht gestartet werden';$nextFrame=0}
                $nextRead=$now+5
            }
            if($null -ne $read){
                $result=Receive-IMCStatusRead $read
                if($result.TimedOut -and $snapshot.status -ne 'UNKNOWN'){$snapshot=New-IMCUnknownStatus 'Statusabfrage: Timeout';$nextFrame=0}
                if($result.Ready){
                    $snapshot=$result.Snapshot;Stop-IMCStatusRead $read;$read=$null;$nextFrame=0
                    $nextRead=[Math]::Max($nextRead,$now+0.25)
                }
            }
            if($now -ge $nextFrame){
                if($snapshot.started_at -and -not $snapshot.ended_at){
                    $unix=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000
                    $snapshot.elapsed_seconds=[Math]::Max(0,$unix-$snapshot.started_at)
                    $snapshot.remaining_seconds=if($snapshot.status -in @('STARTING','RUNNING')){[Math]::Max(0,$snapshot.planned_end-$unix)}else{0}
                    if($snapshot.heartbeat){
                        $snapshot.heartbeat_age_seconds=[Math]::Max(0,$unix-$snapshot.heartbeat)
                        $snapshot.runtime_health=if($snapshot.heartbeat_age_seconds -gt 30){'UNKNOWN'}else{$snapshot.runtime_health}
                    }
                }
                $lines=@(Get-RealityLines $snapshot);$nextFrame=$now+1;$dirty=$true
            }
            while([Console]::KeyAvailable){
                $key=[Console]::ReadKey($true)
                if($key.Key -eq [ConsoleKey]::Enter){return $line.ToString()}
                if($key.Key -eq [ConsoleKey]::Backspace){if($line.Length){$line.Length--}}
                elseif($key.Key -eq [ConsoleKey]::PageUp){$offset=[Math]::Max(0,$offset-[Math]::Max(1,[Console]::WindowHeight-4))}
                elseif($key.Key -eq [ConsoleKey]::PageDown){$offset+=[Math]::Max(1,[Console]::WindowHeight-4)}
                elseif(-not [char]::IsControl($key.KeyChar) -and $line.Length -lt 200){$null=$line.Append($key.KeyChar)}
                $dirty=$true
            }
            $width=[Console]::WindowWidth;$height=[Console]::WindowHeight
            if($dirty -or $null -eq $previous -or $previous.Width -ne $width -or $previous.Height -ne $height){
                $frame=Get-IMCFrameLayout -Lines $lines -Prompt ($Prompt+'> '+$line.ToString()) -Width $width -Height $height -Offset $offset
                try {Write-IMCViewFrame $frame $previous $vt;$previous=$frame;$offset=$frame.Offset;$dirty=$false}
                catch [ArgumentOutOfRangeException] {$previous=$null} # Retry after a resize race.
            }
            Start-Sleep -Milliseconds 20
        }
    } finally {Stop-IMCStatusRead $read;$clock.Stop()}
}
$script:IMCDisplayRoot=$root
if($Group -cne 't'){throw 'Usage: imc t view|start <dauer>|stop|status'}
if($Viewer -or $RenderOnly){
    if($Viewer){$Host.UI.RawUI.WindowTitle='IMC Terminal Control Center';[Console]::Title='IMC Terminal Control Center'}
    if($Viewer -and -not [Console]::IsOutputRedirected){
        try {
            $width=[Math]::Min(148,[Console]::LargestWindowWidth)
            $height=[Math]::Min(60,[Console]::LargestWindowHeight)
            [Console]::SetBufferSize([Math]::Max($width,[Console]::BufferWidth),[Math]::Max($height,[Console]::BufferHeight))
            [Console]::SetWindowSize($width,$height)
        } catch {} # A terminal host may own its window dimensions.
    }
    do {
        if($RenderOnly){Show-Reality (Get-RealityStatus);break}
        $line=Read-IMCViewLine 'imc t'
        $parts=$line.Trim() -split '\s+'
        try {
            switch -CaseSensitive ($parts[0]) {
                'start' {$chosen=if($parts.Count -eq 2){$parts[1]}elseif($parts.Count -eq 1){Read-IMCViewLine 'Laufzeit eingeben'}else{throw 'start <dauer>'};Start-Reality $chosen}
                'stop' {Stop-Reality}
                'status' {}
                'refresh' {}
                'exit' {return}
                default {Write-Host 'start <dauer> | stop | status | refresh | exit'}
            }
        } catch {Write-Host $_.Exception.Message}
    } while($true)
    return
}
switch -CaseSensitive ($Command) {
    'view' {
        . (Join-Path $PSScriptRoot 'IMC_TERMINAL_WINDOW.ps1')
        [IMCTerminalWindow]::Open((Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'),$PSCommandPath,$root,[bool]$Reality) | Out-Null
    }
    'start' {if(-not $Duration){throw 'Usage: imc t start <dauer>'};Start-Reality $Duration}
    'stop' {Stop-Reality;Show-Reality (Get-RealityStatus)}
    'status' {Show-Reality (Get-RealityStatus)}
    default {throw 'Usage: imc t view|start <dauer>|stop|status'}
}
