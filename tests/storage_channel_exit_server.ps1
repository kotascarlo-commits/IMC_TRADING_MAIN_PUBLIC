param([string]$Nonce,[int]$PeerId,[long]$PeerBirth,[string]$Workspace)
$ErrorActionPreference='Stop'
. (Join-Path (Split-Path -Parent $PSScriptRoot) 'TOOLS\IMC_STORAGE_CHANNEL.ps1')
$server=[IMCStorageChannel]::CreateServer($Nonce,'peer_exit',15)
$server.BindPeer($PeerId,$PeerBirth)
[IO.File]::WriteAllText((Join-Path $Workspace 'server-ready'),'native server created')
$server.Connect()
if([Text.Encoding]::UTF8.GetString($server.Read()) -cne 'request'){throw 'fixture_request_mismatch'}
$server.Write([Text.Encoding]::UTF8.GetBytes('reply'))
if([Text.Encoding]::UTF8.GetString($server.Read()) -cne 'close then exit'){throw 'fixture_exit_mismatch'}
$wait=[Diagnostics.Stopwatch]::StartNew()
while(-not [IO.File]::Exists((Join-Path $Workspace 'client-write-completed')) -and $wait.Elapsed.TotalSeconds -lt 5){Start-Sleep -Milliseconds 5}
if(-not [IO.File]::Exists((Join-Path $Workspace 'client-write-completed'))){throw 'fixture_client_write_not_completed'}
$server.Close()
if($server.Faulted -or -not $server.Closed -or [IMCStorageChannel]::RetainedCount -ne 0){throw 'fixture_server_cleanup_unconfirmed'}
exit 0
