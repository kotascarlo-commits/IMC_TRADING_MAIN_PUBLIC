param([string]$Mode,[string]$WorkDir)
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$report=Join-Path $WorkDir ($Mode+'.json')
$checks=[Collections.Generic.List[string]]::new()
function Check($Ok,[string]$Name){if(-not $Ok){throw ('FAIL: '+$Name)};$checks.Add($Name)}
. (Join-Path $root 'TOOLS/IMC_TERMINAL_REFRESH.ps1')
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $root 'TOOLS/IMC_Terminal.ps1'),[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Terminal parse error'}
foreach($statement in $ast.EndBlock.Statements){
    if($statement -is [Management.Automation.Language.FunctionDefinitionAst]){. ([scriptblock]::Create($statement.Extent.Text))}
}
$python=Join-Path $root '.venv/Scripts/python.exe'
function Child([string]$Name,[string]$Code){
    $path=Join-Path $WorkDir ($Name+'.py');[IO.File]::WriteAllText($path,$Code,[Text.UTF8Encoding]::new($false));return $path
}
function Wait-Read($Read,[double]$Timeout=10){
    $watch=[Diagnostics.Stopwatch]::StartNew()
    do {$result=Receive-IMCStatusRead $Read -TimeoutSeconds $Timeout;if($result.Ready){return $result};Start-Sleep -Milliseconds 10}while($watch.Elapsed.TotalSeconds -lt 5)
    throw 'fixture read deadline'
}
try {
    switch($Mode){
        'async' {
            $entry=Child 'slow' "import time,json,sys`ntime.sleep(1)`nsys.stderr.write('x'*300000)`nprint(json.dumps({'status':'IDLE','trades':[]}))"
            $read=Start-IMCStatusRead $python $entry
            try {
                $watch=[Diagnostics.Stopwatch]::StartNew();$pending=Receive-IMCStatusRead $read;$watch.Stop()
                Check (-not $pending.Ready -and $watch.Elapsed.TotalMilliseconds -lt 100) 'slow status polling never waits'
                $result=Wait-Read $read
                Check ($result.Ready -and $result.Snapshot.status -eq 'IDLE') 'stdout and large stderr drain together'
            }finally{Stop-IMCStatusRead $read}
            foreach($case in @(@('invalid',"print('bad json')"),@('array',"print('[]')"),@('exit',"import sys; sys.exit(1)"),@('scalar',"print('123')"),@('partial',"import json; print(json.dumps({'status':'RUNNING'}))"))){
                $entry=Child $case[0] $case[1];$read=Start-IMCStatusRead $python $entry
                try {$result=Wait-Read $read;Check ($result.Snapshot.status -eq 'UNKNOWN' -and $null -eq $result.Snapshot.equity_eur) ($case[0]+' clears statistics')}
                finally {Stop-IMCStatusRead $read}
            }
            $entry=Child 'timeout' "import time`ntime.sleep(30)";$read=Start-IMCStatusRead $python $entry;$childPid=$read.Process.Id
            try {
                $watch=[Diagnostics.Stopwatch]::StartNew();$result=Wait-Read $read .15
                Check ($result.TimedOut -and $result.Snapshot.status -eq 'UNKNOWN' -and $watch.Elapsed.TotalSeconds -lt 1) 'monotone timeout ends slow child'
            }finally{Stop-IMCStatusRead $read}
            Check ($null -eq (Get-Process -Id $childPid -ErrorAction SilentlyContinue)) 'timed out child reaped'
            $entry=Child 'drain-timeout' "import subprocess,sys`nsubprocess.Popen([sys.executable,'-c','import time; time.sleep(2)'],close_fds=False)"
            $read=Start-IMCStatusRead $python $entry
            try {
                $watch=[Diagnostics.Stopwatch]::StartNew();$result=Wait-Read $read .2
                Stop-IMCStatusRead $read;$read=$null
                Check ($result.TimedOut -and $watch.Elapsed.TotalSeconds -lt 1) 'deadline includes unfinished pipe drain and cleanup'
            }finally{Stop-IMCStatusRead $read}
            $entry=Child 'exit-slow' "import time`ntime.sleep(30)"
            $read=Start-IMCStatusRead $python $entry;$childPid=$read.Process.Id
            Stop-IMCStatusRead $read
            Check ($null -eq (Get-Process -Id $childPid -ErrorAction SilentlyContinue)) 'prompt exit reaps own child'
        }
        'layout' {
            $lines=@('header 1','header 2','header 3','header 4','header 5','header 6',('TOKEN-'+('a'*180)),'short','commands','OWNER OFF')
            $wide=Get-IMCFrameLayout $lines 'imc t> ref' 120 20
            $narrow=Get-IMCFrameLayout $lines 'imc t> refresh' 40 8 4
            Check ($wide.Rows[-1] -eq 'imc t> ref' -and $narrow.Rows[-1] -eq 'imc t> refresh') 'typed prompt survives resize'
            Check ($narrow.Rows[-2] -eq 'OWNER OFF' -and $narrow.Rows[-3] -eq 'commands') 'command and gate footer remain visible'
            Check ($narrow.Offset -gt 0 -and $narrow.Rows[-4] -like 'PgUp/PgDn*') 'wrapped long rows remain pageable'
            Check (@($narrow.Rows | Where-Object Length -gt 39).Count -eq 0) 'rows never trigger terminal autowrap'
            $tiny=Get-IMCFrameLayout $lines ('imc t> '+('a'*200)) 8 1
            Check ($tiny.Rows.Count -eq 1 -and $tiny.CursorColumn -eq 7) 'smallest window keeps input tail and cursor'
            $null=Initialize-IMCConsole
            $capture=[IO.StringWriter]::new();$saved=[Console]::Out
            try {
                [Console]::SetOut($capture);Write-IMCViewFrame $wide $null $true
                $first=$capture.ToString();$null=$capture.GetStringBuilder().Clear()
                Write-IMCViewFrame $wide $wide $true;$unchanged=$capture.ToString();$null=$capture.GetStringBuilder().Clear()
                $shrunk=Get-IMCFrameLayout @('x','commands','OWNER OFF') 'imc t> ' 120 20
                Write-IMCViewFrame $shrunk $wide $true;$last=$capture.ToString()
            }finally{[Console]::SetOut($saved);$capture.Dispose()}
            Check ($first.Contains('[38;2;185;121;60m')) 'RGB accent retained'
            Check (-not $first.Contains('[2J') -and -not $last.Contains('[2J')) 'refresh never clears whole screen'
            Check ($unchanged.Length -lt 40) 'unchanged rows not repainted'
            Check ($last.Contains('[K')) 'shrinking rows erase stale suffixes'
            $unknown=@(Get-RealityLines (New-IMCUnknownStatus 'fixture failure'))
            Check (($unknown -join "`n").Contains('Aktuelle Equity:  UNKNOWN')) 'unknown renderer never invents equity'
        }
        'native' {
            $vt=Initialize-IMCConsole
            Add-Type @'
using System;using System.Text;using System.Threading;using System.Runtime.InteropServices;
public static class IMCRefreshProbe {
 [StructLayout(LayoutKind.Sequential)] struct Coord {public short X,Y;}
 [StructLayout(LayoutKind.Sequential)] struct Rect {public short Left,Top,Right,Bottom;}
 [StructLayout(LayoutKind.Sequential)] struct Info {public Coord Size,Cursor;public ushort Attributes;public Rect Window;public Coord Maximum;}
 [StructLayout(LayoutKind.Explicit,Size=20)] struct Input {
  [FieldOffset(0)] public ushort Type;[FieldOffset(4)] public int Down;[FieldOffset(8)] public ushort Repeat;
  [FieldOffset(10)] public ushort Virtual;[FieldOffset(14)] public ushort Character;
 }
 [DllImport("kernel32.dll")] static extern IntPtr GetStdHandle(int n);
 [DllImport("kernel32.dll")] static extern bool GetConsoleScreenBufferInfo(IntPtr h,out Info b);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode)] static extern bool ReadConsoleOutputCharacter(IntPtr h,StringBuilder s,uint n,Coord at,out uint read);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode)] static extern bool WriteConsoleInput(IntPtr h,Input[] e,uint n,out uint count);
 public static string Row(int row){
  var h=GetStdHandle(-11);Info b;if(!GetConsoleScreenBufferInfo(h,out b))throw new Exception("no fixture console");
  int width=b.Window.Right-b.Window.Left+1;var s=new StringBuilder(width+1);uint n;
  if(!ReadConsoleOutputCharacter(h,s,(uint)width,new Coord{X=b.Window.Left,Y=(short)(b.Window.Top+row)},out n))throw new Exception("fixture screen read failed");return s.ToString(0,(int)n);
 }
 public static Thread TypeLater(string text,int delay){
  var t=new Thread(()=>{Thread.Sleep(delay);var e=new Input[text.Length];for(int i=0;i<text.Length;i++)e[i]=new Input{Type=1,Down=1,Repeat=1,Virtual=(ushort)(text[i]=='\r'?13:0),Character=text[i]};uint n;if(!WriteConsoleInput(GetStdHandle(-10),e,(uint)e.Length,out n))throw new Exception("fixture input failed");});
  t.IsBackground=true;t.Start();return t;
 }
 public static Thread TypeAcrossRefresh(){
  var t=new Thread(()=>{TypeLater("ref",0).Join();Thread.Sleep(1400);TypeLater("resh\r",0).Join();});
  t.IsBackground=true;t.Start();return t;
 }
}
'@
            $width=[Console]::WindowWidth;$height=[Console]::WindowHeight
            $lines=@('NATIVE HEADER','long row suffix','commands','OWNER OFF')
            $previous=$null;$watch=[Diagnostics.Stopwatch]::StartNew()
            for($i=0;$i -lt 100;$i++){
                $frame=Get-IMCFrameLayout $lines ('imc t> test'+$i) $width $height
                Write-IMCViewFrame $frame $previous $vt;$previous=$frame
                Check ([IMCRefreshProbe]::Row(0).StartsWith('NATIVE HEADER')) 'native header remains present'
            }
            $refreshMs=$watch.Elapsed.TotalMilliseconds/100
            Check ($refreshMs -lt 50) 'native buffered refresh below 50ms'
            $shrunk=Get-IMCFrameLayout @('X','commands','OWNER OFF') 'imc t> typed' $width $height
            Write-IMCViewFrame $shrunk $previous $vt
            $nativeRow=[IMCRefreshProbe]::Row(0)
            Check ($nativeRow.Trim([char]0,[char]32) -eq 'X') ('native shrinking frame removes stale content: '+$nativeRow)
            Check ([IMCRefreshProbe]::Row($height-1).StartsWith('imc t> typed')) 'native prompt stays visible'
            Write-IMCViewFrame $shrunk $null $false
            Check ([IMCRefreshProbe]::Row(0).Trim([char]0,[char]32) -eq 'X') 'native fallback frame works'
            try {
                [Console]::SetWindowSize([Math]::Min(80,$width),[Math]::Min(20,$height))
                $resized=Get-IMCFrameLayout $lines 'imc t> resize' ([Console]::WindowWidth) ([Console]::WindowHeight)
                Write-IMCViewFrame $resized $shrunk $vt
                Check ([IMCRefreshProbe]::Row([Console]::WindowHeight-1).StartsWith('imc t> resize')) 'native resize preserves prompt'
            }finally{[Console]::SetWindowSize($width,$height)}
            $entry=Child 'viewer-slow' "import time`ntime.sleep(30)"
            $sender=[IMCRefreshProbe]::TypeLater("exit`r",400);$watch=[Diagnostics.Stopwatch]::StartNew()
            $answer=Read-IMCViewLine 'imc t';$sender.Join()
            Check ($answer -eq 'exit' -and $watch.Elapsed.TotalSeconds -lt 1.5) 'viewer accepts and exits while status sleeps 30s'
            $entry=Child 'viewer-current' "import json,time`nn=time.time()`nprint(json.dumps({'status':'RUNNING','started_at':n,'planned_end':n+60,'elapsed_seconds':0,'remaining_seconds':60,'trades':[],'heartbeat':n,'heartbeat_age_seconds':0,'runtime_health':'CURRENT'}))"
            $sender=[IMCRefreshProbe]::TypeAcrossRefresh();$answer=Read-IMCViewLine 'imc t';$sender.Join()
            Check ($answer -eq 'refresh') 'typed input survives periodic status and countdown refresh'
            Check ([IMCRefreshProbe]::Row(6).Contains('STATUS: RUNNING')) 'viewer renders completed async snapshot'
            # Exercise the actual input/refresh loop with an isolated diagnostic source.
            $fixtureAst=[Management.Automation.Language.Parser]::ParseFile((Join-Path $root 'tests/terminal_shadow_fixture.ps1'),[ref]$tokens,[ref]$errors)
            $freshFunction=$fixtureAst.EndBlock.Statements | Where-Object {$_ -is [Management.Automation.Language.FunctionDefinitionAst] -and $_.Name -eq 'Fresh'}
            . ([scriptblock]::Create($freshFunction.Extent.Text))
            $null=New-Item -ItemType Directory -Path (Join-Path $WorkDir RUNTIME) -Force
            $statusPath=Join-Path $WorkDir 'RUNTIME/signal-runtime-status.json'
            [IO.File]::WriteAllText($statusPath,((Fresh)|ConvertTo-Json -Depth 8),[Text.UTF8Encoding]::new($false))
            $script:IMCDisplayRoot=$WorkDir;$python='DOES-NOT-EXIST.exe'
            $writer=[powershell]::Create()
            try {
                $null=$writer.AddScript('param($Path) Start-Sleep -Milliseconds 1000; [IO.File]::WriteAllText($Path,''{broken'')').AddArgument($statusPath)
                $pending=$writer.BeginInvoke()
                $sender=[IMCRefreshProbe]::TypeLater("exit`r",6400)
                $answer=Read-IMCViewLine 'imc t';$sender.Join();$null=$writer.EndInvoke($pending)
                Check ($answer -eq 'exit' -and [IMCRefreshProbe]::Row(6).Contains('STATUS: UNKNOWN')) 'source corruption during native refresh clears live status'
                Check ([IMCRefreshProbe]::Row(9).Contains('STATISTIK-NACHTLAUF')) 'source errors never fall back to Reality'
            }finally{$writer.Dispose()}
        }
        default {throw 'Unknown fixture mode'}
    }
    [IO.File]::WriteAllText($report,(@{ok=$true;checks=@($checks);refresh_ms=$refreshMs} | ConvertTo-Json -Depth 4))
}catch{
    [IO.File]::WriteAllText($report,(@{ok=$false;error=[string]$_;trace=$_.ScriptStackTrace} | ConvertTo-Json));exit 1
}
