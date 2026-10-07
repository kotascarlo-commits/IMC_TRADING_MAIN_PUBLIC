param([string]$Workspace,[string]$Node,[string]$Entry,[string]$ExpectedCode='GMGN_RATE_BUSINESS_BLOCKED')
$ErrorActionPreference='Stop'
$fixtureProtocol=Join-Path $Workspace 'TOOLS\GMGN-ReadProtocol.ps1'
$protocol=if ([IO.File]::Exists($fixtureProtocol)) { $fixtureProtocol } else { Join-Path (Split-Path -Parent $PSScriptRoot) 'TOOLS\GMGN-ReadProtocol.ps1' }
. $protocol
$cfg=Get-GmgnReadConfig
try {
    Invoke-GmgnCoordinatedRead $Node $Entry @('portfolio','info','--raw') $cfg '' $Workspace | Out-Null
    throw 'unexpected_read'
} catch {
    if ($_.Exception.Message -cne $ExpectedCode) { throw }
    Write-Output 'persistent cooldown retained'
}
