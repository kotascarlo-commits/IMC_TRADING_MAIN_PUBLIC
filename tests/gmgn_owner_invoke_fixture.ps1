# Invoke contract integration with OWN methods; no native retirement authority.
$ErrorActionPreference='Stop';$repo=Split-Path -Parent $PSScriptRoot
. (Join-Path $repo 'TOOLS\GMGN-ReadSupervisorCommands.ps1')
$scope=Join-Path (Join-Path $repo '.local-tools') ('legacy-capture-native-'+[guid]::NewGuid().ToString('N'));$recovery=Join-Path (Join-Path $repo '.local-tools') ('gmgn-read-recovery-'+[guid]::NewGuid().ToString('N'))
$null=[IO.Directory]::CreateDirectory($scope);$null=[IO.Directory]::CreateDirectory($recovery)
$bindings=@(@('14552','3904','2026-10-02T15:14:56.6594833Z','OWN_EXE'),@('3904','4176','2026-10-02T15:14:56.3325126Z','OWN_EXE'))
$self=@('1','2','OWN_BIRTH','OWN_EXE');$observed=[Diagnostics.Stopwatch]::GetTimestamp();$frequency=[Diagnostics.Stopwatch]::Frequency
$owner=[pscustomobject]@{schema_version=1;kind='ORIGINAL_OWNER_RETIREMENT_EVIDENCE';owner_pid=3904;owner_birth=$bindings[1][2];broker_pid=14552;broker_birth=$bindings[0][2];broker_directory=(Join-Path $scope 'legacy');loaded_source_sha256=('a'*64);loaded_cleanup_sha256=('b'*64);original_script=(Join-Path $scope 'parent.ps1');original_line=1;original_broker_handle_verified=$true;variables_changed=$false;provider_calls=0;observed_qpc=$observed.ToString();qpc_frequency=$frequency.ToString()}
$proofPath=Join-Path (Join-Path $repo '.local-tools') ('gmgn-owner-proof-'+[guid]::NewGuid().ToString('N')+'.json')
$utf8=[Text.UTF8Encoding]::new($false);$env:IMC_GMGN_IPC_TOKEN='f'*64
function WriteOwnPacket([string]$Path,$Body){$text=$Body|ConvertTo-Json -Compress -Depth 6;$h=[Security.Cryptography.HMACSHA256]::new([Text.Encoding]::ASCII.GetBytes('f'*64));try{$mac=([BitConverter]::ToString($h.ComputeHash($utf8.GetBytes($text)))).Replace('-','').ToLowerInvariant()}finally{$h.Dispose()};[IO.File]::WriteAllText($Path,(@{body=$text;mac=$mac}|ConvertTo-Json -Compress),$utf8)}
WriteOwnPacket $proofPath $owner;$proofHash=(Get-FileHash $proofPath).Hash.ToLowerInvariant()
$retire=[pscustomobject]@{GenerationFingerprint=('c'*64);Phase='ADMITTED';Observed=0L;Frequency=0L;Calls=0}
$retire|Add-Member ScriptMethod Retire {param($o,$f)$this.Observed=$o;$this.Frequency=$f;$this.Calls++;$this.Phase='RETIRED'}
$healthy=[pscustomobject]@{};$healthy|Add-Member ScriptMethod Check {};$healthy|Add-Member ScriptMethod CheckCommitted {}
$directory=[pscustomobject]@{Identity='OWN_DIRECTORY'};$directory|Add-Member ScriptMethod Check {}
$lifetime=[pscustomobject]@{};$lifetime|Add-Member ScriptMethod ObserveOriginalExits {return @($true,$false)}
$stage=[pscustomobject]@{WarmMode=$false;Streams=[Collections.Generic.List[object]]::new();Proofs=@();Sources=$healthy;Directory=$directory;Capture=$healthy;Retirement=$retire;Lifetime=$lifetime;LegacyBindings=$bindings;NativeSelf=$self;InstanceId=('d'*32);CommandId='';Phase='WAIT_CODE'}
$command=[pscustomobject]@{schema_version=1;command='RETIRE_LEGACY';command_id=('e'*32);generation_sha256=('a'*64);source_fingerprint=('c'*64);supervisor=$self;directory=$recovery;directory_identity='OWN_DIRECTORY';legacy_instance_id=('d'*32);legacy_bindings=$bindings;owner_proof_path=$proofPath;owner_proof_sha256=$proofHash}
WriteOwnPacket (Join-Path $recovery 'retire.request.json') $command
$script:proofNames=[Collections.Generic.List[string]]::new()
function Write-StageProof([string]$Name,$Body){$script:proofNames.Add($Name)}
try{Invoke-GmgnStageRetireCommand $stage $recovery ('a'*64) $scope}finally{foreach($stream in $stage.Streams){$stream.Dispose()}}
if($retire.Calls -ne 1 -or $retire.Observed -ne $observed -or $retire.Frequency -ne $frequency -or $stage.Phase -cne 'RETIRED_WAIT_PRIMARY' -or
 ($script:proofNames -join '|') -cne 'retire-intent.json|retire-completed.json'){throw 'OWN_OWNER_INVOKE_CLOCK_NOT_PRESERVED'}
[pscustomobject]@{pass=$true;retire_stub_calls=$retire.Calls;process_actions=0;provider_calls=0;production_verified=$false}|ConvertTo-Json -Compress
