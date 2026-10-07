param([ValidateSet('Normal','BirthMismatch','SidMismatch','DirectoryMismatch','CallerMutation')][string]$Case='Normal')
$ErrorActionPreference='Stop';$repo=Split-Path -Parent $PSScriptRoot;$tools=Join-Path $repo 'TOOLS'
Add-Type -Path @((Join-Path $tools 'GmgnScheduledActionIdentity.cs'),(Join-Path $tools 'GmgnReadDirectoryIdentity.cs'),(Join-Path $tools 'GmgnRepairNamespace.cs'),(Join-Path $tools 'GmgnRepairProcessBinding.cs'))
$scope=Join-Path (Join-Path $repo '.local-tools') ('expected-peer-native-'+[guid]::NewGuid().ToString('N'));$null=[IO.Directory]::CreateDirectory($scope)
$directory=[GmgnReadDirectoryIdentity]::new($scope);$id=$directory.Identity
$process=[Diagnostics.Process]::GetCurrentProcess();$native=[GmgnRepairProcessBinding]::Read($process)
$wanted=$native.Clone();$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
switch($Case){'BirthMismatch'{$wanted[2]='2000-01-01T00:00:00.0000000Z'};'SidMismatch'{$sid='S-1-5-18'};'DirectoryMismatch'{$id=('0'*16)+':'+('0'*32)}}
$peer=$null;$code=''
try{$peer=[GmgnScheduledActionIdentity]::BindExpectedPeer([string[]]$wanted,$sid,$directory,$id)}catch{$e=$_.Exception;while($e.InnerException){$e=$e.InnerException};$code=$e.Message}
if($Case -cin @('Normal','CallerMutation')){
 if(-not $peer -or $code){throw ('OWN_EXPECTED_PEER_NOT_BOUND_'+$code)}
 if($Case -ceq 'CallerMutation'){$wanted[0]='0';$copy=$peer.ReadNativeBinding();$copy[2]='OWN_MUTATION'}
 $peer.CheckPeerPid($PID);$checked=$peer.ReadNativeBinding()
 foreach($i in 0..3){if($checked[$i] -cne $native[$i]){throw 'OWN_EXPECTED_PEER_CALLER_ALIAS'}}
}else{
 $expected=switch($Case){'BirthMismatch'{'GMGN_ACTION_ORIGINAL_CHANGED'};'SidMismatch'{'GMGN_ACTION_OWNER_MISMATCH'};'DirectoryMismatch'{'GMGN_ACTION_PEER_DIRECTORY'}}
 if($peer -or $code -cne $expected){throw ('OWN_EXPECTED_PEER_BAD_REJECTION_'+$code)}
}
$process.Dispose()
[pscustomobject]@{kind='EXPECTED_ORIGINAL_PEER';case=$Case;pass=$true;code=$code;provider_calls=0;production_verified=$false;source_sha256=(Get-FileHash -LiteralPath (Join-Path $tools 'GmgnScheduledActionIdentity.cs')).Hash.ToLowerInvariant()}|ConvertTo-Json -Compress
