param([string]$Library,[string]$Nonce,[int]$ParentId,[long]$ParentBirth,[string]$Case)
$ErrorActionPreference='Stop'
. $Library
$client=[IMCStorageChannel]::ConnectClient($Nonce,'terminal_fixture',$ParentId,$ParentBirth,10)
if([Text.Encoding]::UTF8.GetString($client.Read()) -cne 'ready'){throw 'terminal_fixture_boot'}
$client.Write([Text.Encoding]::UTF8.GetBytes('ready ack'))
if([Text.Encoding]::UTF8.GetString($client.Read()) -cne 'finish'){throw 'terminal_fixture_command'}
$client.Close()
if(-not $client.Closed -or $client.Faulted){throw 'terminal_fixture_child_cleanup'}
if($Case -ceq 'NonzeroExit'){exit 7}
exit 0
