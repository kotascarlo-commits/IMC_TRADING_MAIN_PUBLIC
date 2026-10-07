param([Parameter(Mandatory=$true)][string]$Workspace)
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot;$tools=Join-Path $root 'TOOLS'
. (Join-Path $tools 'GMGN-ReadProtocol.ps1')
$core=(Get-Command Invoke-GmgnBoundedNode -CommandType Function).ScriptBlock
$exe=Join-Path $Workspace 'observer-fixture.exe'
# Controlled fixture executable, not Node/SDK. It emits a marker, verifies
# provisional persistence, then emits a rate error or stalls until own timeout.
Add-Type -OutputAssembly $exe -OutputType ConsoleApplication -TypeDefinition @'
using System;using System.IO;using System.Threading;using System.Diagnostics;
public static class GmgnObserverFixture {
 public static int Main(string[] args) {
  string scope=Environment.GetEnvironmentVariable("IMC_OBSERVER_FIXTURE_SCOPE");
  string mode=Environment.GetEnvironmentVariable("IMC_OBSERVER_FIXTURE_MODE");
  File.AppendAllText(Path.Combine(scope,"launches"),"1\n");
  Console.Error.WriteLine("GMGN_QUOTE_RATE_OBSERVED");Console.Error.Flush();
  Stopwatch clock=Stopwatch.StartNew();bool observed=false;
  while(clock.Elapsed.TotalSeconds<1.5) {
   string path=Path.Combine(scope,"gmgn-read-cooldown.json");
   if(File.Exists(path)&&File.ReadAllText(path).Contains("\"unknown\":true")){observed=true;break;}
   Thread.Sleep(5);
  }
  if(!observed){Console.Error.WriteLine("FIXTURE_PROVISIONAL_MISSING");return 12;}
  File.WriteAllText(Path.Combine(scope,"provisional-observed"),"UNKNOWN before final outcome");
  if(mode=="timeout"){Thread.Sleep(30000);return 13;}
  Console.Error.WriteLine("HTTP 429 (~59s remaining) code=429 reset_source=header");return 1;
 }
}
'@
$cfg=Get-GmgnReadConfig;$cfg.cli_timeout_seconds=2
$wallet='So11111111111111111111111111111111111111112'
$quote=@('order','quote','--chain','sol','--from',$wallet,'--input-token',$wallet,
    '--output-token','EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v','--amount','1000000','--slippage','1','--raw')
$global:ObserverCoreCalls=0
function Invoke-ObservedFixture([string]$Directory) {
    # Same retained-Core/local-shadow call structure as the repair wrapper.
    function Invoke-GmgnBoundedNode([string]$Node,[string]$Entry,[string[]]$CliArgs,$Config,[string]$ExpiresAt='',
        [string]$SignedModuleDir='',[string]$QuoteMode='SIGNED_QUOTE_PROBE',[scriptblock]$OnRateObserved=$null) {
        $global:ObserverCoreCalls++
        & $core $Node $Entry $CliArgs $Config $ExpiresAt $SignedModuleDir $QuoteMode $OnRateObserved
    }
    Invoke-GmgnCoordinatedRead $exe (Join-Path $tools 'GMGN-SignedQuoteProbe.mjs') $quote $cfg '' $Directory $Workspace 'EXIST_QUOTE'
}
foreach($mode in @('rate','timeout')) {
    $directory=Join-Path $Workspace $mode;$null=New-Item -ItemType Directory -Path $directory
    $env:IMC_OBSERVER_FIXTURE_SCOPE=$directory;$env:IMC_OBSERVER_FIXTURE_MODE=$mode
    $before=$global:ObserverCoreCalls
    try{$null=Invoke-ObservedFixture $directory;throw 'observer_fixture_error_admitted'}
    catch{if($_.Exception.Message -cne 'GMGN_RATE_LIMITED'){throw}}
    if($global:ObserverCoreCalls -ne $before+1 -or -not [IO.File]::Exists((Join-Path $directory 'provisional-observed'))){throw 'observer_fixture_marker_not_durable'}
    $cooldown=Get-Content -LiteralPath (Join-Path $directory 'gmgn-read-cooldown.json') -Raw | ConvertFrom-Json
    if($cooldown.unknown -ne ($mode -ceq 'timeout')){throw 'observer_fixture_wrong_cooldown'}
    if($mode -ceq 'rate'){
        if(-not $cooldown.retry_not_before -or $cooldown.http_status -ne 429){throw 'observer_fixture_final_rate_missing'}
    }elseif($null -ne $cooldown.retry_not_before){throw 'observer_fixture_unknown_deadline_invented'}
    try{$null=Invoke-ObservedFixture $directory;throw 'observer_fixture_followup_admitted'}
    catch{if($_.Exception.Message -cne $(if($mode -ceq 'timeout'){'GMGN_COOLDOWN_UNKNOWN'}else{'GMGN_RATE_LIMITED'})){throw}}
    if($global:ObserverCoreCalls -ne $before+1 -or @([IO.File]::ReadAllLines((Join-Path $directory 'launches'))).Count -ne 1){throw 'observer_fixture_child_restarted'}
    if(@(Get-ChildItem -LiteralPath $directory -Filter '*.tmp' -File).Count){throw 'observer_fixture_temporary_retained'}
    $mutex=[Threading.Mutex]::new($false,('Local\IMC_GMGN_READ_'+$cooldown.repo_id))
    try{if(-not $mutex.WaitOne(0)){throw 'observer_fixture_mutex_retained'};$mutex.ReleaseMutex()}finally{$mutex.Dispose()}
}
'GMGN_SYNCHRONOUS_OBSERVER_SCRIPT_SCOPE_PASS'
