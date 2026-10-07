param()
$ErrorActionPreference='Stop'
$loader=Join-Path $PSScriptRoot 'IMC_T_Command.ps1'
$line=". '"+$loader.Replace("'","''")+"' # IMC terminal current-user integration"
foreach($path in @($PROFILE.CurrentUserAllHosts,$PROFILE.CurrentUserCurrentHost) | Select-Object -Unique){
    $directory=Split-Path -Parent $path
    if(-not(Test-Path -LiteralPath $directory)){New-Item -ItemType Directory -Path $directory -Force | Out-Null}
    $text=if(Test-Path -LiteralPath $path){[IO.File]::ReadAllText($path)}else{''}
    if(-not $text.Contains($line)){
        [IO.File]::WriteAllText($path,$text+"`r`n"+$line+"`r`n",[Text.UTF8Encoding]::new($true))
    }
}
. $loader
Write-Output 'IMC_TERMINAL_CURRENT_USER_INSTALLED'
