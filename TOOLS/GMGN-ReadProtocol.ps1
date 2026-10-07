# Shared, side-effect-free protocol definitions. No credentials or services.
function Get-GmgnReadConfig {
    $path = Join-Path (Split-Path -Parent $PSScriptRoot) 'config\imc-gmgn-read-broker.json'
    $cfg = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
    $bounds = @{schema_version=@(1,1); max_request_bytes=@(1024,65536);
        max_response_bytes=@(1024,8388608); max_cli_output_chars=@(1024,2097152);
        max_args=@(3,64); max_arg_chars=@(44,4096); cli_timeout_seconds=@(1,30);
        response_timeout_seconds=@(2,60); poll_milliseconds=@(50,1000);
        identity_check_milliseconds=@(1000,10000);
        max_requests_per_poll=@(1,32)}
    if (@($cfg.PSObject.Properties).Count -ne $bounds.Count) { throw 'GMGN_CONFIG_INVALID' }
    foreach ($key in $bounds.Keys) {
        $v = $cfg.$key
        if ($v -isnot [int] -or $v -lt $bounds[$key][0] -or $v -gt $bounds[$key][1]) { throw 'GMGN_CONFIG_INVALID' }
    }
    if ($cfg.response_timeout_seconds -le $cfg.cli_timeout_seconds) { throw 'GMGN_CONFIG_INVALID' }
    $execution = Get-Content -LiteralPath (Join-Path (Split-Path -Parent $PSScriptRoot) 'config\imc-execution.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($execution.http_timeout_seconds -isnot [ValueType] -or $execution.http_timeout_seconds -is [bool] -or
        [double]::IsNaN($execution.http_timeout_seconds) -or [double]::IsInfinity($execution.http_timeout_seconds) -or
        $execution.http_timeout_seconds -le $cfg.response_timeout_seconds + 1) { throw 'GMGN_TIMEOUT_CONTRACT_INVALID' }
    return $cfg
}

function Assert-GmgnLocalPath([string]$Path) {
    $absolute = [IO.Path]::GetFullPath($Path)
    if ($absolute.StartsWith('\\')) { throw 'GMGN_IPC_PATH_INVALID' }
    $item = Get-Item -LiteralPath $absolute -Force -ErrorAction Stop
    while ($item) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'GMGN_IPC_REPARSE_POINT' }
        $item = if ($item -is [IO.DirectoryInfo]) { $item.Parent } else { $item.Directory }
    }
    return $absolute
}

function Read-GmgnJson([string]$Path, [int]$MaxBytes) {
    $absolute = Assert-GmgnLocalPath $Path
    # Bound the actual read as well as the file metadata (concurrent growth).
    $stream = [IO.File]::OpenRead($absolute)
    try {
        if ($stream.Length -gt $MaxBytes) { throw 'GMGN_IPC_TOO_LARGE' }
        $buffer = New-Object byte[] ($MaxBytes + 1)
        $count = 0
        while ($count -lt $buffer.Length) {
            $n = $stream.Read($buffer, $count, $buffer.Length - $count)
            if ($n -eq 0) { break }
            $count += $n
        }
        if ($count -gt $MaxBytes) { throw 'GMGN_IPC_TOO_LARGE' }
        $envelope = ([Text.UTF8Encoding]::new($false,$true).GetString($buffer,0,$count).TrimStart([char]0xfeff) | ConvertFrom-Json)
        if (@($envelope.PSObject.Properties).Count -ne 2 -or $envelope.body -isnot [string] -or
            $envelope.mac -isnot [string] -or $envelope.mac -cnotmatch '\A[0-9a-f]{64}\z' -or
            $envelope.mac -cne (Get-GmgnMac $envelope.body)) { throw 'GMGN_IPC_AUTH_FAILED' }
        return ($envelope.body | ConvertFrom-Json)
    } finally { $stream.Dispose() }
}

function Test-GmgnReadOnlyArgs($ArgsIn, $Config) {
    if ($ArgsIn -isnot [array] -or $ArgsIn.Count -lt 3 -or $ArgsIn.Count -gt $Config.max_args) { return $false }
    foreach ($arg in $ArgsIn) {
        if ($arg -isnot [string] -or -not $arg -or $arg.Length -gt $Config.max_arg_chars -or
            $arg -cnotmatch '\A[A-Za-z0-9_./+=:-]+\z') { return $false }
    }
    # Deliberately only the current IMC reconciliation contract. No arbitrary
    # top-level commands, config output, trade commands or future subcommands.
    $command = $ArgsIn[0] + ' ' + $ArgsIn[1]
    switch -CaseSensitive ($command) {
        'portfolio info' { return $ArgsIn.Count -eq 3 -and $ArgsIn[2] -ceq '--raw' }
        'portfolio holdings' {
            $allowed = @('--chain','--wallet','--limit','--cursor','--hide-airdrop','--hide-abnormal','--hide-closed')
            $required = @('--chain','--wallet')
        }
        'order get' { $allowed = @('--chain','--order-id'); $required = $allowed }
        'order quote' {
            $allowed = @('--chain','--from','--input-token','--output-token','--amount','--slippage')
            $required = $allowed
        }
        default { return $false }
    }
    $options = @{}
    for ($i=2; $i -lt $ArgsIn.Count; $i++) {
        $key = $ArgsIn[$i]
        if ($options.ContainsKey($key)) { return $false }
        if ($key -ceq '--raw') { $options[$key] = $true; continue }
        if ($key -cnotin $allowed -or ++$i -ge $ArgsIn.Count) { return $false }
        $value = $ArgsIn[$i]
        switch -CaseSensitive ($key) {
            '--chain' { if ($value -cne 'sol') { return $false } }
            '--wallet' { if ($value -cnotmatch '\A[1-9A-HJ-NP-Za-km-z]{32,44}\z') { return $false } }
            '--from' { if ($value -cnotmatch '\A[1-9A-HJ-NP-Za-km-z]{32,44}\z') { return $false } }
            '--input-token' { if ($value -cnotmatch '\A[1-9A-HJ-NP-Za-km-z]{32,44}\z') { return $false } }
            '--output-token' { if ($value -cnotmatch '\A[1-9A-HJ-NP-Za-km-z]{32,44}\z') { return $false } }
            '--amount' { if ($value -cnotmatch '\A[1-9][0-9]{0,19}\z' -or
                [Numerics.BigInteger]::Parse($value) -gt [Numerics.BigInteger]::Parse('18446744073709551615')) { return $false } }
            '--slippage' { if ($value -cne '1') { return $false } }
            '--order-id' { if ($value -cnotmatch '\A[A-Za-z0-9_-]{1,128}\z') { return $false } }
            '--limit' { if ($value -cnotmatch '\A(?:[1-9]|[1-4][0-9]|50)\z') { return $false } }
            '--cursor' { if ($value -cnotmatch '\A[A-Za-z0-9_+/=-]{1,2048}\z') { return $false } }
            default { if ($value -cnotin @('true','false')) { return $false } }
        }
        $options[$key] = $value
    }
    foreach ($key in (@($required) + '--raw')) { if (-not $options.ContainsKey($key)) { return $false } }
    if ($command -ceq 'order quote' -and $options['--input-token'] -ceq $options['--output-token']) { return $false }
    return $true
}

function Get-GmgnProcessBinding([int]$ProcessId, [string]$ScriptPath) {
    $p = Get-CimInstance Win32_Process -Filter ('ProcessId={0}' -f $ProcessId) -OperationTimeoutSec 1 -ErrorAction Stop
    if (-not $p -or $p.Name -ine 'powershell.exe' -or -not $p.ExecutablePath -or -not $p.CreationDate -or
        -not $p.CommandLine) {
        throw 'GMGN_PROCESS_BINDING_UNAVAILABLE'
    }
    if (-not ('IMCCommandLine' -as [type])) {
        . (Join-Path (Split-Path -Parent $PSScriptRoot) 'LAUNCHERS\IMC_PROCESS_COMMON.ps1')
    }
    $argv = @([IMCCommandLine]::Split($p.CommandLine))
    $bound = $false
    for ($i=1;$i -lt $argv.Count;$i++) {
        if ($argv[$i] -ieq '-file' -and $i+1 -lt $argv.Count) {
            $bound = [IO.Path]::IsPathRooted($argv[$i+1]) -and [IO.Path]::GetFullPath($argv[$i+1]) -ieq $ScriptPath
            break
        }
        if ($argv[$i] -iin @('-noprofile','-noninteractive','-nologo')) { continue }
        if ($argv[$i] -iin @('-executionpolicy','-windowstyle') -and $i+1 -lt $argv.Count) { $i++; continue }
        break
    }
    if (-not $bound -or $argv[0] -ine $p.ExecutablePath) { throw 'GMGN_PROCESS_BINDING_UNAVAILABLE' }
    return @{pid=$ProcessId; parent_pid=[int]$p.ParentProcessId; created_at=([datetime]$p.CreationDate).ToUniversalTime().ToString('o');
        executable=[IO.Path]::GetFullPath($p.ExecutablePath); script=[IO.Path]::GetFullPath($ScriptPath)}
}

function Assert-GmgnReadyEnvelope($Ready, [string]$Directory) {
    $fields=@('schema_version','instance_id','directory','broker','parent','transport')
    if (@($Ready.PSObject.Properties).Count -ne $fields.Count -or
        @($Ready.PSObject.Properties.Name | Where-Object { $_ -cnotin $fields }).Count -or
        $Ready.schema_version -isnot [int] -or $Ready.schema_version -ne 3 -or $Ready.instance_id -isnot [string] -or
        $Ready.instance_id -cnotmatch '\A[0-9a-f]{32}\z' -or $Ready.directory -cne $Directory) { throw 'GMGN_READY_INVALID' }
    Assert-GmgnTransportEnvelope $Ready.transport
    foreach ($role in @('broker','parent')) {
        $binding=[pscustomobject]$Ready.$role
        if (@($binding.PSObject.Properties).Count -ne 5 -or $binding.pid -isnot [int] -or $binding.pid -le 0 -or
            $binding.parent_pid -isnot [int] -or $binding.parent_pid -lt 0 -or $binding.created_at -isnot [string] -or
            $binding.executable -isnot [string] -or $binding.script -isnot [string] -or
            -not [IO.Path]::IsPathRooted($binding.executable) -or -not [IO.Path]::IsPathRooted($binding.script)) { throw 'GMGN_READY_INVALID' }
        $date=[DateTimeOffset]::MinValue
        if (-not [DateTimeOffset]::TryParseExact($binding.created_at,'o',[Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::None,[ref]$date)) { throw 'GMGN_READY_INVALID' }
    }
    if ($Ready.broker.script -cne (Join-Path $PSScriptRoot 'GMGN-ReadBroker.ps1') -or
        $Ready.parent.script -cnotin @((Join-Path $PSScriptRoot 'Start-IMC-Work.ps1'),
        (Join-Path (Split-Path -Parent $PSScriptRoot) 'LAUNCHERS\RUN_IMC_SIGNAL_ONLY.ps1')) -or
        $Ready.broker.parent_pid -ne $Ready.parent.pid -or $Ready.broker.pid -eq $Ready.parent.pid) { throw 'GMGN_READY_INVALID' }
}

function Assert-GmgnTransportEnvelope($Transport) {
    $fields=@('quote_mode','cli_entry','sdk_version','cli_sha256','quote_sha256','dependency_sha256','client_sha256','config_sha256','signer_sha256')
    if (@($Transport.PSObject.Properties).Count -ne $fields.Count -or
        @($Transport.PSObject.Properties.Name | Where-Object { $_ -cnotin $fields }).Count -or
        $Transport.quote_mode -cnotin @('EXIST_QUOTE','SIGNED_QUOTE_PROBE') -or
        $Transport.sdk_version -cne '1.6.6' -or $Transport.cli_entry -isnot [string] -or
        -not [IO.Path]::IsPathRooted($Transport.cli_entry)) { throw 'GMGN_TRANSPORT_INVALID' }
    foreach ($key in @('cli_sha256','quote_sha256','dependency_sha256','client_sha256','config_sha256','signer_sha256')) {
        if ($Transport.$key -isnot [string] -or $Transport.$key -cnotmatch '\A[0-9a-f]{64}\z') { throw 'GMGN_TRANSPORT_INVALID' }
    }
}

function Get-GmgnTransportBinding([string]$Entry,[string]$Mode) {
    $Entry=Assert-GmgnLocalPath $Entry
    $dist=Split-Path $Entry -Parent
    $package=Get-Content -LiteralPath (Join-Path (Split-Path $dist -Parent) 'package.json') -Raw | ConvertFrom-Json
    $binding=@{quote_mode=$Mode;cli_entry=$Entry;sdk_version=$package.version}
    $sources=@{cli_sha256=$Entry;quote_sha256=(Join-Path $PSScriptRoot 'GMGN-SignedQuoteProbe.mjs');
        dependency_sha256=(Join-Path $PSScriptRoot 'GMGN-SingleSwapTransport.mjs');
        client_sha256=(Join-Path $dist 'client\OpenApiClient.js');config_sha256=(Join-Path $dist 'config.js');
        signer_sha256=(Join-Path $dist 'client\signer.js')}
    foreach ($key in $sources.Keys) { $binding[$key]=(Get-FileHash -LiteralPath (Assert-GmgnLocalPath $sources[$key]) -Algorithm SHA256).Hash.ToLowerInvariant() }
    $result=[pscustomobject]$binding
    Assert-GmgnTransportEnvelope $result
    return $result
}

function Assert-GmgnNativeBinding($Handle,$Binding) {
    if ($Handle.HasExited -or $Handle.Id -ne $Binding.pid -or
        [math]::Abs(($Handle.StartTime.ToUniversalTime()-[DateTime]::Parse($Binding.created_at).ToUniversalTime()).Ticks) -ge 10 -or
        $Handle.MainModule.FileName -ine $Binding.executable) { throw 'GMGN_BROKER_IDENTITY_CHANGED' }
}

function Assert-GmgnTransportUnchanged($Expected) {
    Assert-GmgnTransportEnvelope $Expected
    $actual=Get-GmgnTransportBinding $Expected.cli_entry $Expected.quote_mode
    foreach ($key in $Expected.PSObject.Properties.Name) {
        if ($actual.$key -cne $Expected.$key) { throw 'GMGN_TRANSPORT_CHANGED' }
    }
}

function Assert-GmgnReady($Ready, [string]$Directory, $Handles=$null) {
    Assert-GmgnReadyEnvelope $Ready $Directory
    foreach ($role in @('broker','parent')) {
        $expected = $Ready.$role
        $scriptPath = if ($role -eq 'broker') { Join-Path $PSScriptRoot 'GMGN-ReadBroker.ps1' }
                      else { $Ready.parent.script }
        if ($role -eq 'parent' -and $scriptPath -cnotin @(
            (Join-Path $PSScriptRoot 'Start-IMC-Work.ps1'),
            (Join-Path (Split-Path -Parent $PSScriptRoot) 'LAUNCHERS\RUN_IMC_SIGNAL_ONLY.ps1'))) {
            throw 'GMGN_READY_INVALID'
        }
        if ($expected.pid -isnot [int] -or $expected.pid -le 0) { throw 'GMGN_READY_INVALID' }
        if ($Handles) { Assert-GmgnNativeBinding $Handles[$role] $expected; continue }
        $actual = Get-GmgnProcessBinding $expected.pid $scriptPath
        foreach ($field in @('pid','parent_pid','created_at','executable','script')) {
            if ($actual[$field] -cne $expected.$field) { throw 'GMGN_BROKER_IDENTITY_CHANGED' }
        }
    }
    if ($Ready.broker.parent_pid -ne $Ready.parent.pid) { throw 'GMGN_BROKER_IDENTITY_CHANGED' }
}

function Get-GmgnMac([string]$Body) {
    $token = $env:IMC_GMGN_IPC_TOKEN
    if (-not $token -or $token -cnotmatch '\A[0-9a-f]{64}\z') { throw 'GMGN_IPC_AUTH_UNAVAILABLE' }
    $hmac = [Security.Cryptography.HMACSHA256]::new([Text.Encoding]::ASCII.GetBytes($token))
    try { return ([BitConverter]::ToString($hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes($Body)))).Replace('-','').ToLowerInvariant() }
    finally { $hmac.Dispose() }
}

function New-GmgnIpcToken {
    $bytes = [byte[]]::new(32)
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return ([BitConverter]::ToString($bytes)).Replace('-','').ToLowerInvariant()
}

function Write-GmgnAtomicJson([string]$Directory,[string]$Name,$Payload,[int]$MaxBytes) {
    $absolute = Assert-GmgnLocalPath $Directory
    if ($Name -cnotmatch '\A(?:broker.ready|[0-9a-f]{32}\.(?:request|response)\.json)\z') { throw 'GMGN_IPC_NAME_INVALID' }
    $body = $Payload | ConvertTo-Json -Depth 12 -Compress
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes((@{body=$body;mac=(Get-GmgnMac $body)} | ConvertTo-Json -Compress))
    if ($bytes.Length -gt $MaxBytes) { throw 'GMGN_IPC_TOO_LARGE' }
    $temporary = Join-Path $absolute ([guid]::NewGuid().ToString('N') + '.tmp')
    try {
        $stream = [IO.File]::Open($temporary,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
        try { $stream.Write($bytes,0,$bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
        # Move is atomic on the same volume and fails if destination exists.
        [IO.File]::Move($temporary,(Join-Path $absolute $Name))
    } finally {
        if ([IO.File]::Exists($temporary)) { [IO.File]::Delete($temporary) }
    }
}

function Read-GmgnRequest([string]$Path,$Ready,$Config) {
    $name = [IO.Path]::GetFileName($Path)
    if ($name -cnotmatch '\A([0-9a-f]{32})\.request\.json\z') { throw 'GMGN_REQUEST_INVALID' }
    $id = $Matches[1]
    $request = Read-GmgnJson $Path $Config.max_request_bytes
    if (@($request.PSObject.Properties).Count -ne 5 -or $request.schema_version -isnot [int] -or $request.schema_version -ne 3 -or
        $request.id -isnot [string] -or $request.instance_id -isnot [string] -or
        $request.id -cne $id -or $request.instance_id -cne $Ready.instance_id -or
        -not (Test-GmgnReadOnlyArgs $request.args $Config)) { throw 'GMGN_REQUEST_INVALID' }
    if ($request.expires_at -isnot [string]) { throw 'GMGN_REQUEST_INVALID' }
    $expiry=[DateTimeOffset]::MinValue
    if (-not [DateTimeOffset]::TryParseExact($request.expires_at,'o',[Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::None,[ref]$expiry)) { throw 'GMGN_REQUEST_INVALID' }
    $remaining=($expiry-[DateTimeOffset]::UtcNow).TotalSeconds
    if ($remaining -le 0 -or $remaining -gt $Config.response_timeout_seconds) { throw 'GMGN_REQUEST_EXPIRED' }
    return $request
}

function ConvertTo-GmgnSafeOutput([string]$Text,[string[]]$CliArgs) {
    $data = $Text | ConvertFrom-Json
    $safe = @{}
    switch -CaseSensitive ($CliArgs[0] + ' ' + $CliArgs[1]) {
        'portfolio info' {
            if ($data.wallets -isnot [array]) { throw 'GMGN_SCHEMA_UNVERIFIED' }
            $safe.wallets = @($data.wallets | ForEach-Object {
                if ($_.chain -isnot [string] -or $_.address -isnot [string] -or
                    $_.chain -cnotmatch '\A[a-z]{2,16}\z' -or $_.address -cnotmatch '\A[A-Za-z0-9]{20,128}\z') { throw 'GMGN_SCHEMA_UNVERIFIED' }
                $wallet=@{chain=$_.chain; address=$_.address}
                if ($null -ne $_.balances) {
                    if ($_.balances -isnot [array]) { throw 'GMGN_SCHEMA_UNVERIFIED' }
                    $wallet.balances=@($_.balances | ForEach-Object {
                        if ($_.symbol -isnot [string] -or $_.symbol -cnotmatch '\A[A-Z0-9]{1,16}\z' -or
                            $_.balance -isnot [string] -or $_.balance -cnotmatch '\A[0-9]+(?:\.[0-9]+)?\z') { throw 'GMGN_SCHEMA_UNVERIFIED' }
                        @{symbol=$_.symbol; balance=$_.balance}
                    })
                }
                $wallet
            })
        }
        'portfolio holdings' {
            if ($data.list -isnot [array] -or ($null -ne $data.next -and $data.next -isnot [string])) { throw 'GMGN_SCHEMA_UNVERIFIED' }
            $safe.list = @($data.list | ForEach-Object {
                if ($_.token.token_address -isnot [string] -or $_.token.token_address -cnotmatch '\A[1-9A-HJ-NP-Za-km-z]{32,44}\z' -or
                    $_.balance -isnot [string] -or $_.balance -cnotmatch '\A[0-9]+(?:\.[0-9]+)?\z') { throw 'GMGN_SCHEMA_UNVERIFIED' }
                @{token=@{token_address=$_.token.token_address}; balance=$_.balance}
            })
            if ($data.next -and $data.next -cnotmatch '\A[A-Za-z0-9_+/=-]{1,2048}\z') { throw 'GMGN_SCHEMA_UNVERIFIED' }
            $safe.next = $data.next
        }
        'order get' {
            foreach ($field in @('order_id','status')) {
                if ($data.$field -isnot [string] -or $data.$field -cnotmatch '\A[A-Za-z0-9_-]{1,128}\z') { throw 'GMGN_SCHEMA_UNVERIFIED' }
                $safe[$field] = $data.$field
            }
            if ($data.status -cnotin @('pending','processed','failed','expired','confirmed','successful')) { throw 'GMGN_SCHEMA_UNVERIFIED' }
            if ($null -ne $data.hash) {
                if ($data.hash -isnot [string] -or $data.hash -cnotmatch '\A[1-9A-HJ-NP-Za-km-z]{64,100}\z') { throw 'GMGN_SCHEMA_UNVERIFIED' }
                $safe.hash=$data.hash
            }
            if ($null -ne $data.state) {
                if ($data.state -isnot [int]) { throw 'GMGN_SCHEMA_UNVERIFIED' }
                $safe.state = $data.state
            }
            if ($data.state -eq 30 -and $data.status -cnotin @('confirmed','successful')) { throw 'GMGN_SCHEMA_UNVERIFIED' }
            if ($null -ne $data.report) {
                $safe.report = @{}
                foreach ($field in @('input_token','output_token','input_amount','output_amount')) {
                    $pattern = if ($field.EndsWith('_amount')) { '\A[0-9]{1,40}\z' } else { '\A[1-9A-HJ-NP-Za-km-z]{32,44}\z' }
                    if ($data.report.$field -isnot [string] -or $data.report.$field -cnotmatch $pattern) { throw 'GMGN_SCHEMA_UNVERIFIED' }
                    $safe.report[$field] = $data.report.$field
                }
            }
            if ($data.status -cin @('confirmed','successful') -and
                (-not $safe.hash -or -not $safe.report -or ($data.status -ceq 'successful' -and $data.state -ne 30))) { throw 'GMGN_SCHEMA_UNVERIFIED' }
        }
        'order quote' {
            if (-not (Test-GmgnReadOnlyArgs $CliArgs (Get-GmgnReadConfig))) { throw 'GMGN_SCHEMA_UNVERIFIED' }
            $options=@{}
            for ($i=2;$i -lt $CliArgs.Count;$i++) {
                if ($CliArgs[$i] -ceq '--raw') { continue }
                $key=$CliArgs[$i];$i++;$options[$key]=$CliArgs[$i]
            }
            foreach ($field in @('input_token','output_token','input_amount','output_amount','min_output_amount')) {
                $pattern = if ($field.EndsWith('_amount')) { '\A[0-9]{1,40}\z' } else { '\A[1-9A-HJ-NP-Za-km-z]{32,44}\z' }
                if ($data.$field -isnot [string] -or $data.$field -cnotmatch $pattern) { throw 'GMGN_SCHEMA_UNVERIFIED' }
                $safe[$field] = $data.$field
            }
            if ($data.slippage -isnot [ValueType] -or $data.slippage -is [bool] -or
                [double]::IsNaN($data.slippage) -or [double]::IsInfinity($data.slippage) -or
                $data.slippage -lt 0 -or $data.slippage -gt 1) { throw 'GMGN_SCHEMA_UNVERIFIED' }
            $safe.slippage = $data.slippage
            if ($safe.input_token -cne $options['--input-token'] -or $safe.output_token -cne $options['--output-token'] -or
                $safe.input_amount -cne $options['--amount'] -or [decimal]$safe.slippage -ne [decimal]$options['--slippage']) { throw 'GMGN_QUOTE_BINDING_INVALID' }
            foreach ($field in @('input_amount','output_amount','min_output_amount')) {
                $amount=[Numerics.BigInteger]::Parse($safe[$field])
                if ($amount -le 0 -or $amount -gt [Numerics.BigInteger]::Parse('18446744073709551615')) { throw 'GMGN_SCHEMA_UNVERIFIED' }
            }
            if ([Numerics.BigInteger]::Parse($safe.min_output_amount) -gt [Numerics.BigInteger]::Parse($safe.output_amount)) { throw 'GMGN_SCHEMA_UNVERIFIED' }
        }
        default { throw 'GMGN_SCHEMA_UNVERIFIED' }
    }
    # Project only known data fields. Never forward unknown metadata or errors,
    # and never round/serialize a floating point quantity as an exact balance.
    return ($safe | ConvertTo-Json -Depth 8 -Compress)
}

function Get-GmgnCliFailureCode([string]$Text) {
    # Classification is deliberately lossy. Never return any substring of a
    # credential-bearing CLI error, URL, header, API response or stack trace.
    if ($Text -match 'ERROR_RATE_LIMIT_BLOCKED') { return 'GMGN_RATE_BUSINESS_BLOCKED' }
    if ($Text -match 'RATE_LIMIT_BANNED') { return 'GMGN_RATE_BANNED' }
    if ($Text -match 'RATE_LIMIT_EXCEEDED|HTTP 429|(?:^|\s)code=429(?:\s|$)') { return 'GMGN_RATE_LIMITED' }
    if ($Text -match 'GMGN_PRIVATE_KEY is required') { return 'GMGN_SIGNED_READ_AUTH_UNAVAILABLE' }
    if ($Text -match 'GMGN_API_KEY is required|API key.*(?:missing|invalid)|INVALID_API_KEY') { return 'GMGN_AUTH_UNAVAILABLE' }
    if ($Text -match 'HTTP 403|PERMISSION_DENIED|NO_PERMISSION|INSUFFICIENT_PERMISSION|ACCESS_DENIED') { return 'GMGN_API_PERMISSION_DENIED' }
    if ($Text -match 'HTTP 401|UNAUTHORIZED') { return 'GMGN_AUTH_REJECTED' }
    if ($Text -match 'HTTP 5[0-9][0-9]') { return 'GMGN_UPSTREAM_UNAVAILABLE' }
    if ($Text -match 'HTTP 400|INVALID_PARAM|INVALID_ARGUMENT') { return 'GMGN_REQUEST_REJECTED' }
    return 'GMGN_CLI_FAILED'
}

function Get-GmgnRetrySeconds([string]$Text) {
    if ($Text -match '\(~([0-9]{1,9})s remaining\)') {
        $remaining=[long]$Matches[1]+1
        if ($remaining -gt 86400) { return -1 } # UNKNOWN; never shorten the provider deadline.
        return [int][math]::Max(1,$remaining)
    }
    return -1
}

function Get-GmgnQuotaHint([string]$Text, [ValidateSet('quota_limit','quota_remaining')][string]$Name) {
    $matchesForHint=[regex]::Matches($Text,('(?:^|\s)'+$Name+'=([^\s]*)'))
    if ($matchesForHint.Count -ne 1) { return $null }
    $value=$matchesForHint[0].Groups[1].Value
    if ($value -cnotmatch '\A(?:0|[1-9][0-9]{0,9})\z') { return $null }
    $number=[long]::Parse($value,[Globalization.CultureInfo]::InvariantCulture)
    if ($number -gt 1000000000) { return $null }
    return [int]$number
}

function Drain-GmgnCompletedStreams($Tasks,$Readers,$Buffers,$Builders,$Finished,$Config,[scriptblock]$ObserveRate) {
    for ($i=0;$i -lt 2;$i++) {
        # A following ReadAsync can complete synchronously from buffered data.
        # Drain it too before the caller evaluates its unchanged deadline.
        while (-not $Finished[$i] -and $Tasks[$i].IsCompleted) {
            $n=$Tasks[$i].GetAwaiter().GetResult()
            if (-not $n) { $Finished[$i]=$true;break }
            if ($Builders[0].Length+$Builders[1].Length+$n -gt $Config.max_cli_output_chars) { throw 'GMGN_CLI_OUTPUT_LIMIT' }
            $null=$Builders[$i].Append($Buffers[$i],0,$n)
            if ($ObserveRate) { & $ObserveRate | Out-Null }
            $Tasks[$i]=$Readers[$i].ReadAsync($Buffers[$i],0,$Buffers[$i].Length)
        }
    }
}

function Invoke-GmgnBoundedNode([string]$Node,[string]$Entry,[string[]]$CliArgs,$Config,[string]$ExpiresAt='',[string]$SignedModuleDir='',
                               [ValidateSet('EXIST_QUOTE','SIGNED_QUOTE_PROBE')][string]$QuoteMode='SIGNED_QUOTE_PROBE',
                               [scriptblock]$OnRateObserved=$null) {
    if (-not (Test-GmgnReadOnlyArgs $CliArgs $Config)) { throw 'GMGN_COMMAND_BLOCKED' }
    if ($Entry.Contains('"')) { throw 'GMGN_CLI_PATH_INVALID' }
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $Node
    $info.Arguments = '"' + $Entry + '" ' + ($CliArgs -join ' ')
    $info.UseShellExecute = $false; $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true; $info.RedirectStandardError = $true; $info.RedirectStandardInput = $true
    # The installed CLI otherwise retries a READ internally. Its minimum retry
    # delay is 1000ms; max-wait=0 therefore allows exactly one HTTP attempt.
    $info.EnvironmentVariables['GMGN_RATE_LIMIT_AUTO_RETRY_MAX_WAIT_MS']='0'
    $info.EnvironmentVariables.Remove('GMGN_DEBUG')
    $info.EnvironmentVariables.Remove('IMC_GMGN_SIGNED_PROBE_DIST')
    if ($SignedModuleDir) {
        if (($CliArgs[0] + ' ' + $CliArgs[1]) -cne 'order quote' -or
            $Entry -ine (Join-Path $PSScriptRoot 'GMGN-SignedQuoteProbe.mjs')) { throw 'GMGN_COMMAND_BLOCKED' }
        $info.EnvironmentVariables['IMC_GMGN_SIGNED_PROBE_DIST']=$SignedModuleDir
        $info.EnvironmentVariables['IMC_GMGN_QUOTE_MODE']=$QuoteMode
    }
    $p = [Diagnostics.Process]::new(); $p.StartInfo = $info
    $buffers = @([char[]]::new(4096),[char[]]::new(4096))
    $builders = @([Text.StringBuilder]::new(),[Text.StringBuilder]::new())
    $rateState=[pscustomobject]@{observed=$false}
    try {
        $deadline = [DateTime]::UtcNow.AddSeconds($Config.cli_timeout_seconds)
        if ($ExpiresAt) {
            $expiry=[DateTimeOffset]::ParseExact($ExpiresAt,'o',[Globalization.CultureInfo]::InvariantCulture).UtcDateTime
            if ($expiry -lt $deadline) { $deadline=$expiry }
        }
        if ([DateTime]::UtcNow -ge $deadline) { throw 'GMGN_REQUEST_EXPIRED' }
        $repairVariable=Get-Variable -Name IMCLiveReaderRepair -Scope Global -ErrorAction SilentlyContinue
        if ($null -ne $repairVariable) {
            # A present-null or malformed repair context must never fall back.
            $repair=$repairVariable.Value
            $lifetimeProperty=if($null -ne $repair){$repair.PSObject.Properties['Lifetime']}else{$null}
            $lifetimeType='GmgnRepairLifetime' -as [type]
            if(-not $lifetimeType -or $lifetimeProperty -isnot [Management.Automation.PSNoteProperty] -or
                $lifetimeProperty.Value -isnot $lifetimeType) { throw 'GMGN_READ_REPAIR_LOCKED' }
            $started=$lifetimeProperty.Value.StartReadProcess($p)
        } else {$started=$p.Start()}
        if (-not $started) { throw 'GMGN_CLI_FAILED' }
        $p.StandardInput.Close()
        $readers = @($p.StandardOutput,$p.StandardError)
        $tasks = @($readers[0].ReadAsync($buffers[0],0,$buffers[0].Length),$readers[1].ReadAsync($buffers[1],0,$buffers[1].Length))
        $finished = @($false,$false)
        # Drain invokes this observer synchronously within this active call.
        # A DynamicModule closure would hide the broker's ScriptScope helpers
        # from the nested rate-persistence callback.
        $observeMarker={
            if (-not $rateState.observed -and $SignedModuleDir -and
                $builders[1].ToString() -match '(?m)^GMGN_QUOTE_RATE_OBSERVED\r?$') {
                $rateState.observed=$true
                if ($OnRateObserved) { & $OnRateObserved | Out-Null }
            }
        }
        while (-not ($finished[0] -and $finished[1] -and $p.HasExited)) {
            Drain-GmgnCompletedStreams $tasks $readers $buffers $builders $finished $Config $observeMarker
            if ([DateTime]::UtcNow -ge $deadline) { throw 'GMGN_CLI_TIMEOUT' }
            [Threading.Thread]::Sleep(10)
        }
        if ($p.ExitCode -ne 0) {
            $failureText=$builders[0].ToString() + $builders[1].ToString()
            $failure=[Exception]::new((Get-GmgnCliFailureCode $failureText))
            $failure.Data['origin']='transport_error'
            if ($failure.Message -clike 'GMGN_RATE_*') { $failure.Data['retry_after_seconds']=Get-GmgnRetrySeconds $failureText }
            if ($failureText -match 'HTTP ([1-5][0-9]{2})') { $failure.Data['http_status']=[int]$Matches[1] }
            if ($failureText -match '(?:^|\s)code=([0-9]{1,9})(?:\s|$)') { $failure.Data['api_code']=[int]$Matches[1] }
            if ($null -ne $failure.Data['http_status'] -or $null -ne $failure.Data['api_code']) { $failure.Data['origin']='provider_response' }
            if ($SignedModuleDir -and [IO.Path]::GetFullPath($Entry) -ieq (Join-Path $PSScriptRoot 'GMGN-SignedQuoteProbe.mjs') -and
                ($CliArgs[0]+' '+$CliArgs[1]) -ceq 'order quote') {
                foreach ($name in @('quota_limit','quota_remaining')) {
                    $failure.Data[$name]=Get-GmgnQuotaHint $failureText $name
                }
                if ($failureText -match '(?:^|\s)reset_source=(header|body|header_and_body|unknown)(?:\s|$)') { $failure.Data['reset_source']=$Matches[1] }
                # Only the pinned quote guard emits this closed enum. Never
                # forward a provider message or infer the account's plan.
                if ($failureText -cmatch '(?:^|\s)(RATE_LIMIT_EXCEEDED|RATE_LIMIT_BANNED|ERROR_RATE_LIMIT_BLOCKED)(?:\s|$)') { $failure.Data['api_error']=$Matches[1] }
            }
            throw $failure
        }
        return ConvertTo-GmgnSafeOutput $builders[0].ToString() $CliArgs
    } catch {
        if ($rateState.observed -and $_.Exception.Message -cnotlike 'GMGN_RATE_*') {
            $failure=[Exception]::new('GMGN_RATE_LIMITED')
            $failure.Data['origin']='provider_response';$failure.Data['http_status']=429
            $failure.Data['retry_after_seconds']=-1;$failure.Data['reset_source']='unknown'
            throw $failure
        }
        throw
    } finally {
        # Only the child created above; never stop the broker, parent or IMC.
        try { if (-not $p.HasExited) { $p.Kill(); $null=$p.WaitForExit(1000) } } catch {}
        $p.Dispose()
    }
}

function Invoke-GmgnCoordinatedRead([string]$Node,[string]$Entry,[string[]]$CliArgs,$Config,
                                  [string]$ExpiresAt='', [string]$Directory='', [string]$SignedModuleDir='',
                                  [ValidateSet('EXIST_QUOTE','SIGNED_QUOTE_PROBE')][string]$QuoteMode='SIGNED_QUOTE_PROBE') {
    if (-not $Directory) { $Directory=Join-Path (Split-Path -Parent $PSScriptRoot) 'RUNTIME' }
    $Directory=Assert-GmgnLocalPath $Directory
    $hash=[Security.Cryptography.SHA256]::Create()
    try { $repoId=([BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($Directory.TrimEnd('\').ToUpperInvariant())))).Replace('-','').ToLowerInvariant() }
    finally { $hash.Dispose() }
    $mutex=[Threading.Mutex]::new($false,('Local\IMC_GMGN_READ_'+$repoId))
    $owned=$false; $temporary=$null
    $path=Join-Path $Directory 'gmgn-read-cooldown.json'
    try {
        $waitMilliseconds=[math]::Max(1,($Config.response_timeout_seconds-$Config.cli_timeout_seconds-1)*1000)
        if ($ExpiresAt) {
            $requestExpiry=[DateTimeOffset]::MinValue
            if (-not [DateTimeOffset]::TryParseExact($ExpiresAt,'o',[Globalization.CultureInfo]::InvariantCulture,
                [Globalization.DateTimeStyles]::None,[ref]$requestExpiry)) { throw 'GMGN_REQUEST_EXPIRED' }
            $remainingMilliseconds=($requestExpiry-[DateTimeOffset]::UtcNow).TotalMilliseconds
            if ($remainingMilliseconds -le 0) { throw 'GMGN_REQUEST_EXPIRED' }
            $waitMilliseconds=[math]::Min($waitMilliseconds,[math]::Max(0,[math]::Floor($remainingMilliseconds)))
        }
        try { $owned=$mutex.WaitOne([int]$waitMilliseconds) }
        catch [Threading.AbandonedMutexException] { $owned=$true }
        if (-not $owned) { throw 'GMGN_READ_COORDINATION_BUSY' }
        if ([IO.File]::Exists($path)) {
            $null=Assert-GmgnLocalPath $path
            $stream=[IO.File]::OpenRead($path)
            try {
                if ($stream.Length -gt 2048) { throw 'GMGN_COOLDOWN_INVALID' }
                $buffer=[byte[]]::new(2049); $count=0
                while ($count -lt $buffer.Length) {
                    $n=$stream.Read($buffer,$count,$buffer.Length-$count)
                    if (-not $n) { break }; $count += $n
                }
                if ($count -gt 2048) { throw 'GMGN_COOLDOWN_INVALID' }
                $cooldown=([Text.UTF8Encoding]::new($false,$true).GetString($buffer,0,$count) | ConvertFrom-Json)
            } catch { throw 'GMGN_COOLDOWN_INVALID' } finally { $stream.Dispose() }
            if ($cooldown.schema_version -isnot [int] -or $cooldown.schema_version -ne 1 -or $cooldown.repo_id -cne $repoId -or
                $cooldown.unknown -isnot [bool] -or $cooldown.error_class -cnotin @('GMGN_RATE_LIMITED','GMGN_RATE_BANNED','GMGN_RATE_BUSINESS_BLOCKED')) { throw 'GMGN_COOLDOWN_INVALID' }
            if ($cooldown.unknown) {
                $unknown=[Exception]::new('GMGN_COOLDOWN_UNKNOWN');$unknown.Data['origin']='local_cooldown';$unknown.Data['retry_after_seconds']=-1
                throw $unknown
            }
            $deadline=[DateTimeOffset]::MinValue
            if (-not [DateTimeOffset]::TryParseExact($cooldown.retry_not_before,'o',[Globalization.CultureInfo]::InvariantCulture,
                [Globalization.DateTimeStyles]::None,[ref]$deadline)) { throw 'GMGN_COOLDOWN_INVALID' }
            if ($deadline -gt [DateTimeOffset]::UtcNow) {
                $limited=[Exception]::new($cooldown.error_class)
                $limited.Data['origin']='local_cooldown'
                $limited.Data['retry_after_seconds']=[int][math]::Ceiling(($deadline-[DateTimeOffset]::UtcNow).TotalSeconds)
                # Rejection is local. Do not move the deadline or call Node.
                throw $limited
            }
        }
        # Core invokes the hook synchronously; path/repoId remain in this
        # active call chain. Never store or dispatch either observer async.
        $observeRate={
            $provisional=@{schema_version=1;repo_id=$repoId;observed_at=[DateTimeOffset]::UtcNow.ToString('o');
                error_class='GMGN_RATE_LIMITED';unknown=$true;retry_not_before=$null;http_status=429;api_code=$null}
            Save-GmgnCooldown $path $provisional
        }
        try { return Invoke-GmgnBoundedNode $Node $Entry $CliArgs $Config $ExpiresAt $SignedModuleDir $QuoteMode $observeRate }
        catch {
            $failure=$_.Exception
            if ($failure.Message -clike 'GMGN_RATE_*') {
                $seconds=$failure.Data['retry_after_seconds']
                if ($seconds -isnot [int] -or ($seconds -lt 1 -and $seconds -ne -1) -or $seconds -gt 86400) { $seconds=-1 }
                $now=[DateTimeOffset]::UtcNow
                $cooldown=@{schema_version=1; repo_id=$repoId; observed_at=$now.ToString('o'); error_class=$failure.Message;
                    unknown=($seconds -eq -1); retry_not_before=$(if ($seconds -ge 1) { $now.AddSeconds($seconds).ToString('o') } else { $null });
                    http_status=$failure.Data['http_status']; api_code=$failure.Data['api_code']}
                Save-GmgnCooldown $path $cooldown
            }
            throw $failure
        }
    } finally {
        if ($temporary -and [IO.File]::Exists($temporary)) { [IO.File]::Delete($temporary) }
        if ($owned) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
}

function Save-GmgnCooldown([string]$Path,$Payload) {
    $directory=Assert-GmgnLocalPath (Split-Path $Path -Parent)
    $temporary=Join-Path $directory ('gmgn-read-cooldown.'+[guid]::NewGuid().ToString('N')+'.tmp')
    try {
        $bytes=[Text.UTF8Encoding]::new($false).GetBytes(($Payload | ConvertTo-Json -Compress))
        $stream=[IO.File]::Open($temporary,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
        try { $stream.Write($bytes,0,$bytes.Length);$stream.Flush($true) } finally { $stream.Dispose() }
        if ([IO.File]::Exists($Path)) { $null=Assert-GmgnLocalPath $Path;[IO.File]::Replace($temporary,$Path,[NullString]::Value) }
        else { [IO.File]::Move($temporary,$Path) }
    } finally { if ([IO.File]::Exists($temporary)) { [IO.File]::Delete($temporary) } }
}

function Read-GmgnResponse([string]$Path,[string]$Id,[string]$Instance,$Config) {
    $response = Read-GmgnJson $Path $Config.max_response_bytes
    if (@($response.PSObject.Properties).Count -ne 6 -or
        @($response.PSObject.Properties.Name | Where-Object { $_ -cnotin @('schema_version','instance_id','id','exit_code','output','evidence') }).Count -or
        $response.schema_version -isnot [int] -or $response.schema_version -ne 3 -or
        $response.id -isnot [string] -or $response.instance_id -isnot [string] -or
        $response.id -cne $Id -or $response.instance_id -cne $Instance -or
        $response.exit_code -isnot [int] -or $response.exit_code -notin @(0,124,125,126) -or
        $response.output -isnot [string]) { throw 'GMGN_RESPONSE_INVALID' }
    if ($response.exit_code -ne 0 -and $response.output -notin @('GMGN_READ_ONLY_BROKER_BLOCKED','GMGN_READ_ONLY_BROKER_ERROR')) {
        throw 'GMGN_RESPONSE_INVALID'
    }
    if ($response.exit_code -eq 0) {
        if ($null -ne $response.evidence) { throw 'GMGN_RESPONSE_INVALID' }
    } else { Assert-GmgnFailureEvidence $response.evidence }
    return $response
}

function New-GmgnFailureEvidence($ErrorObject) {
    $code=[string]$ErrorObject.Message
    if ($code -cnotmatch '\AGMGN_[A-Z0-9_]+\z') { $code='GMGN_INTERNAL_ERROR' }
    $evidence=[pscustomobject]@{origin='local_error';code=$code;http_status=$null;api_code=$null;
        retry_after_seconds=$null;quota_limit=$null;quota_remaining=$null;reset_source='unknown';api_error=$null}
    if ($ErrorObject.Data['origin'] -cin @('local_cooldown','provider_response','transport_error')) { $evidence.origin=$ErrorObject.Data['origin'] }
    if ($evidence.origin -ceq 'provider_response') {
        foreach ($key in @('http_status','api_code','quota_limit','quota_remaining')) { $evidence.$key=$ErrorObject.Data[$key] }
        if ($ErrorObject.Data['reset_source'] -cin @('header','body','header_and_body')) { $evidence.reset_source=$ErrorObject.Data['reset_source'] }
        if ($ErrorObject.Data['api_error'] -cin @('RATE_LIMIT_EXCEEDED','RATE_LIMIT_BANNED','ERROR_RATE_LIMIT_BLOCKED')) { $evidence.api_error=$ErrorObject.Data['api_error'] }
    }
    if ($code -clike 'GMGN_RATE_*' -or $code -ceq 'GMGN_COOLDOWN_UNKNOWN') {
        $seconds=$ErrorObject.Data['retry_after_seconds']
        $evidence.retry_after_seconds=if ($seconds -is [int] -and ($seconds -eq -1 -or $seconds -ge 1 -and $seconds -le 86400)) { $seconds } else { -1 }
    }
    Assert-GmgnFailureEvidence $evidence
    return $evidence
}

function Assert-GmgnFailureEvidence($Evidence) {
    $fields=@('origin','code','http_status','api_code','retry_after_seconds','quota_limit','quota_remaining','reset_source','api_error')
    if (@($Evidence.PSObject.Properties).Count -ne $fields.Count -or
        @($Evidence.PSObject.Properties.Name | Where-Object { $_ -cnotin $fields }).Count -or
        $Evidence.origin -cnotin @('local_error','local_cooldown','provider_response','transport_error') -or
        $Evidence.code -isnot [string] -or $Evidence.code -cnotmatch '\AGMGN_[A-Z0-9_]+\z' -or
        $Evidence.reset_source -cnotin @('unknown','header','body','header_and_body') -or
        ($null -ne $Evidence.api_error -and ($Evidence.api_error -isnot [string] -or $Evidence.api_error -cnotin @('RATE_LIMIT_EXCEEDED','RATE_LIMIT_BANNED','ERROR_RATE_LIMIT_BLOCKED')))) { throw 'GMGN_RESPONSE_INVALID' }
    $bounds=@{http_status=@(100,599);api_code=@(0,999999999);quota_limit=@(0,1000000000);quota_remaining=@(0,1000000000);retry_after_seconds=@(-1,86400)}
    foreach ($key in $bounds.Keys) {
        $value=$Evidence.$key
        if ($null -ne $value -and ($value -isnot [int] -or $value -lt $bounds[$key][0] -or $value -gt $bounds[$key][1])) { throw 'GMGN_RESPONSE_INVALID' }
    }
    $rate=$Evidence.code -clike 'GMGN_RATE_*' -or $Evidence.code -ceq 'GMGN_COOLDOWN_UNKNOWN'
    if (($rate -and ($null -eq $Evidence.retry_after_seconds -or $Evidence.retry_after_seconds -eq 0)) -or
        (-not $rate -and $null -ne $Evidence.retry_after_seconds) -or
        ($Evidence.origin -cne 'provider_response' -and (@('http_status','api_code','quota_limit','quota_remaining','api_error') | Where-Object { $null -ne $Evidence.$_ }).Count) -or
        ($null -ne $Evidence.api_error -and -not $rate) -or
        ($Evidence.origin -cne 'provider_response' -and $Evidence.reset_source -cne 'unknown') -or
        ($Evidence.origin -ceq 'local_cooldown' -and -not $rate) -or
        ($Evidence.origin -ceq 'provider_response' -and $null -eq $Evidence.http_status -and $null -eq $Evidence.api_code)) { throw 'GMGN_RESPONSE_INVALID' }
}
