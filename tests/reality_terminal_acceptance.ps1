$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$python=Join-Path $root '.venv\Scripts\python.exe'
$runtime=Join-Path $root 'RUNTIME\reality_runtime.py'
. (Join-Path $root 'TOOLS\IMC_T_Command.ps1')
. (Join-Path $root 'LAUNCHERS\IMC_PROCESS_COMMON.ps1')
$checks=[Collections.Generic.List[string]]::new()
function Check($condition,[string]$name){if(-not $condition){throw ('FAIL: '+$name)};$checks.Add($name)}
function Status {return (& $python $runtime status | ConvertFrom-Json)}
function WaitCompleted {
    $deadline=[DateTime]::UtcNow.AddSeconds(30)
    do {$s=Status;if($s.status -eq 'COMPLETED' -and $s.cleanup_confirmed){return $s};Start-Sleep -Milliseconds 150}while([DateTime]::UtcNow -lt $deadline)
    throw 'Controlled completion/cleanup timeout'
}
Add-Type @'
using System;using System.Runtime.InteropServices;
public static class IMCViewProbe {
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] public static extern IntPtr FindWindow(IntPtr c,string title);
 [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h,uint m,IntPtr w,IntPtr l);
 [StructLayout(LayoutKind.Explicit,Size=20)] public struct Input {
  [FieldOffset(0)] public ushort Type;[FieldOffset(4)] public int Down;[FieldOffset(8)] public ushort Repeat;
  [FieldOffset(10)] public ushort Virtual;[FieldOffset(12)] public ushort Scan;[FieldOffset(14)] public ushort Character;
  [FieldOffset(16)] public uint Control;
 }
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool AttachConsole(uint id);
 [DllImport("kernel32.dll")] static extern bool FreeConsole();
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern IntPtr CreateFile(string name,uint a,uint s,IntPtr p,uint d,uint f,IntPtr t);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool WriteConsoleInput(IntPtr h,Input[] events,uint n,out uint written);
 [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
 [StructLayout(LayoutKind.Sequential)] struct Coord {public short X,Y;}
 [StructLayout(LayoutKind.Sequential)] struct BufferInfo {public Coord Size,Cursor;public ushort Attributes;public short Left,Top,Right,Bottom;public Coord Maximum;}
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool GetConsoleScreenBufferInfo(IntPtr h,out BufferInfo b);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool ReadConsoleOutputCharacter(IntPtr h,System.Text.StringBuilder s,uint n,Coord at,out uint read);
 public static string Read(uint id){
  FreeConsole();if(!AttachConsole(id))throw new System.ComponentModel.Win32Exception();
  IntPtr output=IntPtr.Zero;
  try{
   output=CreateFile("CONOUT$",0x80000000,3,IntPtr.Zero,3,0,IntPtr.Zero);BufferInfo b;
   if(output==new IntPtr(-1)||!GetConsoleScreenBufferInfo(output,out b))throw new System.ComponentModel.Win32Exception();
   var text=new System.Text.StringBuilder(8192);uint n;
   if(!ReadConsoleOutputCharacter(output,text,8191,new Coord(),out n))throw new System.ComponentModel.Win32Exception();
   return text.ToString();
  }finally{if(output!=IntPtr.Zero&&output!=new IntPtr(-1))CloseHandle(output);FreeConsole();AttachConsole(UInt32.MaxValue);}
 }
 public static void Type(uint id,string text){
  FreeConsole();if(!AttachConsole(id))throw new System.ComponentModel.Win32Exception();
  IntPtr input=IntPtr.Zero;
  try{
   input=CreateFile("CONIN$",0x40000000,3,IntPtr.Zero,3,0,IntPtr.Zero);
   if(input==new IntPtr(-1))throw new System.ComponentModel.Win32Exception();
   var records=new Input[text.Length];
   for(int i=0;i<text.Length;i++)records[i]=new Input{Type=1,Down=1,Repeat=1,Character=text[i],Virtual=(ushort)(text[i]=='\r'?13:Char.ToUpperInvariant(text[i]))};
   uint count;if(!WriteConsoleInput(input,records,(uint)records.Length,out count)||count!=records.Length)throw new System.ComponentModel.Win32Exception();
  }finally{if(input!=IntPtr.Zero&&input!=new IntPtr(-1))CloseHandle(input);FreeConsole();AttachConsole(UInt32.MaxValue);}
 }
}
'@
$view=$null
try {
    foreach($duration in @('15m','30m','1h','8h')) {
        imc t start $duration
        $s=Status
        Check ($s.duration -ceq $duration) ('native start '+$duration)
        Check ([decimal]$s.cash_eur -eq 1000) ('1000 EUR new run '+$duration)
        Check (-not $s.broadcast -and -not $s.signing -and -not $s.live -and $s.owner_gate -ceq 'OFF') 'all gates OFF'
        if($duration -eq '30m') {
            $first=$s.run_id
            $blocked=$false
            try {imc t start 1h}catch{$blocked=$true}
            Check ($blocked -and (Status).run_id -ceq $first) 'second run blocked'
            imc t view
            $deadline=[DateTime]::UtcNow.AddSeconds(10)
            do {
                $window=[IMCViewProbe]::FindWindow([IntPtr]::Zero,'IMC Terminal Control Center')
                $rows=@(Get-CimInstance Win32_Process | Where-Object {
                    (Get-IMCProcessRole $_).Role -ceq 'RealityTerminal' -and
                    @([IMCCommandLine]::Split($_.CommandLine)) -ccontains '-Viewer'})
                if($window -ne [IntPtr]::Zero -and $rows.Count -eq 1){break}
                Start-Sleep -Milliseconds 100
            }while([DateTime]::UtcNow -lt $deadline)
            Check ($window -ne [IntPtr]::Zero -and $rows.Count -eq 1) 'new PowerShell view window'
            $view=Get-Process -Id $rows[0].ProcessId
            $null=$view.Handle
            $ready=[DateTime]::UtcNow.AddSeconds(8)
            do {
                $frame1=[IMCViewProbe]::Read([uint32]$view.Id)
                if($frame1.Contains('STATUS: RUNNING') -and $frame1.Contains([char]0x2588) -and $frame1.Contains('imc t>')){break}
                Start-Sleep -Milliseconds 100
            }while([DateTime]::UtcNow -lt $ready)
            Start-Sleep -Milliseconds 2200
            $frame2=[IMCViewProbe]::Read([uint32]$view.Id)
            Check ($frame1.Contains('STATUS: RUNNING') -and $frame1.Contains([char]0x2588)) 'native screen status and ASCII header'
            $rest1=[regex]::Match($frame1,'Restzeit:\s*([0-9:.]+)').Groups[1].Value
            $rest2=[regex]::Match($frame2,'Restzeit:\s*([0-9:.]+)').Groups[1].Value
            Check ($rest1 -and $rest2 -and $rest1 -ne $rest2) 'automatic remaining-time refresh without typing'
            [IMCViewProbe]::Type([uint32]$view.Id,"status`rrefresh`rexit`r")
            Check ($view.WaitForExit(5000)) 'exit closes view'
            $view.Dispose();$view=$null
            Check ((Status).run_id -ceq $first -and (Status).status -eq 'RUNNING') 'runtime independent of view exit'
            imc t view
            $deadline=[DateTime]::UtcNow.AddSeconds(10)
            do {$window=[IMCViewProbe]::FindWindow([IntPtr]::Zero,'IMC Terminal Control Center');if($window -ne [IntPtr]::Zero){break};Start-Sleep -Milliseconds 100}while([DateTime]::UtcNow -lt $deadline)
            Check ($window -ne [IntPtr]::Zero -and (Status).run_id -ceq $first) 'reopen same persisted run'
            $null=[IMCViewProbe]::PostMessage($window,0x0010,[IntPtr]::Zero,[IntPtr]::Zero)
        }
        imc t stop
        $s=WaitCompleted
        Check ($s.status -eq 'COMPLETED' -and -not $s.open_position) ('stop drained '+$duration)
        Check $s.cleanup_confirmed ('native cleanup '+$duration)
    }
    $s=Status
    Check ($s.status -eq 'COMPLETED' -and $s.duration -eq '8h') 'last run persists after completion'
    imc t view
    $deadline=[DateTime]::UtcNow.AddSeconds(10)
    do {
        $rows=@(Get-CimInstance Win32_Process | Where-Object {
            (Get-IMCProcessRole $_).Role -ceq 'RealityTerminal' -and
            @([IMCCommandLine]::Split($_.CommandLine)) -ccontains '-Viewer'})
        if($rows.Count -eq 1){break};Start-Sleep -Milliseconds 100
    }while([DateTime]::UtcNow -lt $deadline)
    Check ($rows.Count -eq 1) 'interactive view owned'
    $view=Get-Process -Id $rows[0].ProcessId;$null=$view.Handle
    [IMCViewProbe]::Type([uint32]$view.Id,"start`r15m`rstatus`rstop`rexit`r")
    Check ($view.WaitForExit(15000)) 'interactive start duration prompt stop exit'
    $view.Dispose();$view=$null
    $s=WaitCompleted
    Check ($s.duration -eq '15m' -and $s.run_id -ne 'ce57a5a45205469d9a2782f31d657b4e') 'interactive selected duration persisted'
    $document=@{status='PASS';checks=@($checks);passed=$checks.Count;failed=0;skipped=0;run_id=$s.run_id;
        observed_at=[DateTimeOffset]::UtcNow.ToString('o');evidence='NATIVE_CONTROLS_NO_PERFORMANCE_PROOF';broadcast=$false;signing=$false}
    [IO.File]::WriteAllText((Join-Path $root 'RUNTIME\reality-native-controls.json'),($document|ConvertTo-Json -Depth 5),[Text.UTF8Encoding]::new($false))
    $document|ConvertTo-Json -Depth 5
} catch {
    [IO.File]::WriteAllText((Join-Path $root 'RUNTIME\reality-native-controls-failure.json'),
        (@{status='FAIL';reason=$_.Exception.ToString();checks=@($checks);frame1=$frame1;frame2=$frame2}|ConvertTo-Json -Depth 4),[Text.UTF8Encoding]::new($false))
    throw
} finally {
    if($view){
        if(-not $view.HasExited){[IMCViewProbe]::Type([uint32]$view.Id,"exit`r");$null=$view.WaitForExit(5000)}
        $view.Dispose()
    }
    $s=Status
    if($s.status -in @('STARTING','RUNNING','FINISHING')){& $python $runtime stop | Out-Null;WaitCompleted | Out-Null}
}
