$ErrorActionPreference='Stop'
. (Join-Path (Split-Path -Parent $PSScriptRoot) 'LAUNCHERS\IMC_PROCESS_COMMON.ps1')
$root=Split-Path -Parent $PSScriptRoot
$py=Join-Path $root '.venv\Scripts\python.exe'
$db=Join-Path $root 'RUNTIME\test-runs\identity-fixture\paper.db'
$status=Join-Path $root 'RUNTIME\test-runs\identity-fixture\paper-status.json'
$argsGood=@('-u','-m','mykonos_meme_scout.paper_forward','--database',$db,'--status-path',$status,
    '--virtual-eur','1000','--quote-wallet','So11111111111111111111111111111111111111112','--collect',
    '--duration-seconds','3600','--drain-seconds','180')
function Role($arguments){
    $line='"'+$py+'" '+(($arguments|ForEach-Object{'"'+$_+'"'}) -join ' ')
    return (Get-IMCProcessRole ([pscustomobject]@{Name='python.exe';CommandLine=$line})).Role
}
if((Role $argsGood) -cne 'PaperForward'){throw 'Valid paper identity rejected'}
foreach($bad in @('duration','drain','relative','alias','outside','status','eur','extra','wallet')){
    $argsCopy=@($argsGood)
    switch($bad){
        duration{$argsCopy[13]='43201'}
        drain{$argsCopy[15]='181'}
        relative{$argsCopy[4]='RUNTIME\test-runs\identity-fixture\paper.db'}
        alias{$argsCopy[4]=Join-Path $root 'RUNTIME\test-runs\identity-fixture\..\identity-fixture\paper.db'}
        outside{$argsCopy[4]=Join-Path $env:TEMP 'paper.db'}
        status{$argsCopy[6]=Join-Path $root 'RUNTIME\test-runs\other\paper-status.json'}
        eur{$argsCopy[8]='10000'}
        extra{$argsCopy+=@('--signing','true')}
        wallet{$argsCopy[10]='UNKNOWN'}
    }
    if(Role $argsCopy){throw ('Invalid paper identity accepted: '+$bad)}
}
'PAPER_PROCESS_IDENTITY_PASS:10'
