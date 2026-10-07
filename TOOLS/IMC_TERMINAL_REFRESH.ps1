# Display-only helpers. The status child uses the existing CLI; it owns no runner.
function New-IMCUnknownStatus([string]$Reason='Status wird geladen') {
    return [pscustomobject]@{status='UNKNOWN';trades=@();runtime_health='UNKNOWN';view_error=$Reason}
}
function Read-IMCDisplayJson([string]$Path) {
    $file=[IO.FileStream]::new($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete))
    try {
        if($file.Length -gt 65536){throw 'Statusdatei zu gross'}
        $bytes=[byte[]]::new(65537);$count=0
        do {$read=$file.Read($bytes,$count,$bytes.Length-$count);$count+=$read} while($read -gt 0 -and $count -lt $bytes.Length)
        if($count -gt 65536){throw 'Statusdatei zu gross'}
        $value=[Text.UTF8Encoding]::new($false,$true).GetString($bytes,0,$count) | ConvertFrom-Json -ErrorAction Stop
        if($null -eq $value -or $value -isnot [pscustomobject]){throw 'Statusdatei ungueltig'}
        return $value
    } finally {$file.Dispose()}
}
function Get-IMCDisplayUtc($Value) {
    if($Value -isnot [string] -or $Value -cnotmatch '\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,7})?(Z|\+00:00)\z'){throw 'UTC-Zeit ungueltig'}
    return [DateTimeOffset]::Parse($Value,[Globalization.CultureInfo]::InvariantCulture)
}
function Test-IMCDisplayNumber($Value) {
    return ($Value -is [ValueType] -and $Value -isnot [bool] -and -not [double]::IsNaN([double]$Value) -and
        -not [double]::IsInfinity([double]$Value) -and $Value -ge 0)
}
function Get-IMCShadowDisplay([string]$Root) {
    $path=Join-Path $Root 'RUNTIME/signal-runtime-status.json'
    # Once selected, disappearance/corruption must never start another runtime CLI.
    if(-not (Test-Path -LiteralPath $path)){
        if($script:IMCDiagnosticSelected){return New-IMCDiagnosticUnknown 'Diagnose-Statusdatei fehlt'}
        return
    }
    try {
        $s=Read-IMCDisplayJson $path
        if($s.execution_mode -cnotin @('SIGNAL_ONLY','THREE_TRADE_VALIDATION','DATA_ONLY_SHADOW')){throw 'Statusmodus ungueltig'}
        if($s.execution_mode -cne 'DATA_ONLY_SHADOW'){
            if($script:IMCDiagnosticSelected){throw 'Diagnosequelle hat den Modus gewechselt'}
            return
        }
        $script:IMCDiagnosticSelected=$true
        if($s.schema_version -isnot [int] -or $s.schema_version -ne 1 -or $s.state -cnotin @('RUNNING','STARTING','UNKNOWN','STOPPED') -or
            $s.broadcast -isnot [bool] -or $s.broadcast -or $s.live_ready -isnot [bool] -or $s.live_ready){throw 'Diagnose-Status ungueltig'}
        $session=$s.shadow_session
        if($session.data_only -isnot [bool] -or -not $session.data_only -or $session.qualified_trading_ready -isnot [bool] -or
            $session.qualified_trading_ready -or $session.evidence_domain -cne 'DIAGNOSTIC_SHADOW_NOT_PERFORMANCE'){throw 'Diagnose-Session ungueltig'}
        $now=[DateTimeOffset]::UtcNow;$updated=Get-IMCDisplayUtc $s.updated_at
        $start=Get-IMCDisplayUtc $session.started_at;$end=Get-IMCDisplayUtc $session.planned_end
        if($updated -gt $now -or $start -gt $updated -or $end -le $start){throw 'Diagnose-Zeitdaten ungueltig'}
        foreach($key in @('duration_seconds','storage_baseline','max_storage_growth_bytes','failures','scan_cycle_id')){
            if(-not (Test-IMCDisplayNumber $session.$key) -or $session.$key -isnot [int] -and $session.$key -isnot [long]){throw 'Diagnose-Zahlen ungueltig'}
        }
        if($session.duration_seconds -lt 1 -or $session.duration_seconds -gt 43200 -or
            [math]::Abs(($end-$start).TotalSeconds-$session.duration_seconds) -gt .001){throw 'Diagnose-Laufzeit ungueltig'}
        $historical=$s.state -ceq 'STOPPED';$age=($now-$updated).TotalSeconds
        if(-not $historical){
            if($age -gt 30 -or $now -ge $end){throw 'Diagnose-Status veraltet'}
            if($s.pid -isnot [int] -or $s.pid -le 0 -or $s.process_birth_filetime -isnot [string] -or
                $s.process_birth_filetime -cnotmatch '\A[0-9]{17,19}\z'){throw 'Supervisor-Identitaet fehlt'}
            $process=Get-Process -Id $s.pid -ErrorAction Stop
            try {
                $null=$process.Handle
                if($process.HasExited -or $process.StartTime.ToUniversalTime().ToFileTimeUtc().ToString() -cne $s.process_birth_filetime){throw 'Supervisor-Identitaet stimmt nicht'}
                if($process.StartTime.ToUniversalTime() -gt $start.UtcDateTime){throw 'Session vor Supervisor'}
            } finally {$process.Dispose()}
        }
        $observation=$session.latest_observation;$dataAge=$null;$observedUnix=$null;$sourceUnix=$null
        $observationState=if(($now-$start).TotalSeconds -le 90){'LOADING'}else{'UNKNOWN'}
        try {
         if($null -ne $observation){
            if($observation.schema_version -isnot [int] -or $observation.schema_version -ne 1 -or $observation.evidence_domain -cne 'DIAGNOSTIC_SHADOW_NOT_PERFORMANCE' -or
                $observation.qualified_trading_ready -isnot [bool] -or $observation.qualified_trading_ready){throw 'Beobachtung ungueltig'}
            $observed=Get-IMCDisplayUtc $observation.observed_at
            if($observed -lt $start -or $observed -gt $updated -or $observed -gt $now){throw 'Beobachtung gehoert nicht zur Session'}
            foreach($key in @('latest_id','source_age_seconds','storage_bytes','disk_free_bytes','shadow_targets','shadow_observed','sample_size','security_checked','holder_values_present')){
                if(-not (Test-IMCDisplayNumber $observation.$key)){throw 'Beobachtungszahlen ungueltig'}
            }
            foreach($key in @('latest_id','storage_bytes','disk_free_bytes','shadow_targets','shadow_observed','sample_size','security_checked','holder_values_present')){
                if($observation.$key -isnot [int] -and $observation.$key -isnot [long]){throw 'Beobachtungszaehler ungueltig'}
            }
            if($observation.feed_complete -isnot [bool] -or $observation.watch_errors -isnot [array] -or
                $observation.security_checked -gt $observation.sample_size -or $observation.holder_values_present -gt $observation.sample_size){throw 'Beobachtungsabdeckung ungueltig'}
            $dataAge=($now-$observed).TotalSeconds+$observation.source_age_seconds
            $observedUnix=$observed.ToUnixTimeMilliseconds()/1000;$sourceUnix=$observedUnix-$observation.source_age_seconds
            $observationState=if($historical){'HISTORICAL'}else{'FRESH'}
            if(-not $historical -and (($now-$observed).TotalSeconds -gt 30 -or $dataAge -gt 30)){
                $observationState='STALE';$observation=$null;$dataAge=$null;$observedUnix=$null;$sourceUnix=$null
            }
         }
        }catch{$observationState='UNKNOWN';$observation=$null;$dataAge=$null;$observedUnix=$null;$sourceUnix=$null}
        $contract=$session.contract | ConvertFrom-Json -ErrorAction Stop
        foreach($key in @('broadcast','signing','live_trading')){if($contract.$key -isnot [bool] -or $contract.$key){throw 'Gespeicherte Gates ungueltig'}}
        if($contract.owner_gate.value -cne 'OFF' -or $contract.owner_gate.valid -isnot [bool] -or -not $contract.owner_gate.valid){throw 'Owner-Gate ungueltig'}
        $healthVerdict='UNKNOWN';$healthMessages=@();$healthUnix=$null
        if(-not $historical){
            try {
                $health=Read-IMCDisplayJson (Join-Path $Root 'RUNTIME/IMC_SYSTEM_HEALTH.json')
                $healthTime=Get-IMCDisplayUtc $health.updated
                $supervisors=@($health.process_snapshot.processes | Where-Object Role -ceq 'SignalSupervisor')
                if($health.configured_mode -cne 'DATA_ONLY_SHADOW' -or $health.verdict -cnotin @('HEALTHY','DEGRADED','CRITICAL') -or
                    $healthTime -lt $start -or $healthTime -gt $now -or ($now-$healthTime).TotalSeconds -gt 30 -or
                    $supervisors.Count -ne 1 -or $supervisors[0].ProcessId -ne $s.pid -or
                    $supervisors[0].CreationFiletime -isnot [string] -or $supervisors[0].CreationFiletime -cnotmatch '\A[0-9]{17,19}\z' -or
                    [math]::Abs([long]$supervisors[0].CreationFiletime-[long]$s.process_birth_filetime) -ge 10){throw 'Health nicht gebunden'}
                $healthVerdict=$health.verdict
                $healthMessages=@($health.critical)+@($health.warnings)
                $healthUnix=$healthTime.ToUnixTimeMilliseconds()/1000
            }catch{}
        }
        return [pscustomobject]@{diagnostic=$true;status=$s.state;historical=$historical;run_id=($s.pid.ToString()+':'+$session.started_at);
            started_at=$start.ToUnixTimeMilliseconds()/1000;planned_end=$end.ToUnixTimeMilliseconds()/1000;
            elapsed_seconds=[math]::Max(0,($(if($historical){$updated}else{$now})-$start).TotalSeconds);
            remaining_seconds=$(if($historical){0}else{[math]::Max(0,($end-$now).TotalSeconds)});
            ended_at=$(if($historical){$updated.ToUnixTimeMilliseconds()/1000}else{$null});duration=([TimeSpan]::FromSeconds($session.duration_seconds).ToString());
            heartbeat=$updated.ToUnixTimeMilliseconds()/1000;heartbeat_age_seconds=$age;view_received_at=$now.ToUnixTimeMilliseconds()/1000;
            runtime_health=$(if($historical){'HISTORISCH'}elseif($session.failures -gt 0 -or $s.state -ne 'RUNNING'){'DEGRADED'}else{'FRESH'});
            health_verdict=$healthVerdict;health_messages=$healthMessages;
            health_updated_at=$healthUnix;observation_state=$observationState;observation_updated_at=$observedUnix;source_timestamp=$sourceUnix;
            stop_reason=$s.code;observation=$observation;source_age_seconds=$dataAge;scan_cycle_id=$session.scan_cycle_id;
            storage_growth_bytes=$(if($observation){$observation.storage_bytes-$session.storage_baseline}else{$null});
            max_storage_growth_bytes=$session.max_storage_growth_bytes;trades=@()}
    } catch {$script:IMCDiagnosticSelected=$true;return New-IMCDiagnosticUnknown 'Diagnose-Status nicht verifiziert (Quelle, Identitaet oder Aktualitaet)'}
}
function New-IMCDiagnosticUnknown([string]$Reason) {
    $value=New-IMCUnknownStatus $Reason
    $value | Add-Member -NotePropertyName diagnostic -NotePropertyValue $true
    return $value
}
function Update-IMCDiagnosticFreshness($Snapshot,[double]$Now=([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000)) {
    if(-not $Snapshot.diagnostic -or $Snapshot.status -eq 'UNKNOWN' -or $Snapshot.historical){return $Snapshot}
    $age=$Now-$Snapshot.heartbeat
    if($age -lt 0 -or $age -gt 30 -or $Now -ge $Snapshot.planned_end){return New-IMCDiagnosticUnknown 'Laufstatus veraltet oder Laufende erreicht'}
    $Snapshot.heartbeat_age_seconds=$age
    if($null -ne $Snapshot.observation){
        $sourceAge=$Now-$Snapshot.source_timestamp;$observationAge=$Now-$Snapshot.observation_updated_at
        if($sourceAge -lt 0 -or $sourceAge -gt 30 -or $observationAge -lt 0 -or $observationAge -gt 30){
            $Snapshot.observation=$null;$Snapshot.source_age_seconds=$null;$Snapshot.storage_growth_bytes=$null
            $Snapshot.observation_updated_at=$null;$Snapshot.source_timestamp=$null;$Snapshot.observation_state='STALE'
        }else{$Snapshot.source_age_seconds=$sourceAge}
    }
    if($null -ne $Snapshot.health_updated_at -and ($Now-$Snapshot.health_updated_at -lt 0 -or $Now-$Snapshot.health_updated_at -gt 30)){
        $Snapshot.health_verdict='UNKNOWN';$Snapshot.health_messages=@();$Snapshot.health_updated_at=$null
    }
    if($Snapshot.observation_state -eq 'LOADING' -and $Now-$Snapshot.started_at -gt 90){$Snapshot.observation_state='UNKNOWN'}
    return $Snapshot
}
function Start-IMCStatusRead([string]$Python,[string]$Entry,[string]$Root,[switch]$Reality) {
    if($Root){
        $snapshot=Get-IMCShadowDisplay $Root
        if($null -ne $snapshot -and -not ($Reality -and $snapshot.status -ceq 'STOPPED')){return [pscustomobject]@{Native=$true;Snapshot=$snapshot}}
    }
    $info=[Diagnostics.ProcessStartInfo]::new()
    $info.FileName=$Python
    $info.Arguments='"'+$Entry+'" status'
    $info.UseShellExecute=$false;$info.CreateNoWindow=$true
    $info.RedirectStandardOutput=$true;$info.RedirectStandardError=$true
    $info.StandardOutputEncoding=[Text.Encoding]::UTF8
    $info.StandardErrorEncoding=[Text.Encoding]::UTF8
    $process=[Diagnostics.Process]::new();$process.StartInfo=$info
    try {
        $clock=[Diagnostics.Stopwatch]::StartNew()
        if(-not $process.Start()){throw 'REALITY_STATUS_START_FAILED'}
        return [pscustomobject]@{Process=$process;Clock=$clock;Output=$process.StandardOutput.ReadToEndAsync();
            Error=$process.StandardError.ReadToEndAsync();TimedOut=$false}
    } catch {$process.Dispose();throw}
}
function Stop-IMCStatusRead($Read) {
    if($null -eq $Read){return}
    if($Read.Native){return}
    try {
        if(-not $Read.Process.HasExited){$Read.Process.Kill()}
        # Only used on exit/prompt changes, never for a regular display refresh.
        if(-not $Read.Process.WaitForExit(1000)){throw 'REALITY_STATUS_CLEANUP_TIMEOUT'}
    } finally {
        try {$Read.Process.StandardOutput.Dispose();$Read.Process.StandardError.Dispose()}
        finally {$Read.Process.Dispose();$Read.Clock.Stop()}
    }
}
function Receive-IMCStatusRead($Read,[double]$TimeoutSeconds=10) {
    if($Read.Native){return [pscustomobject]@{Ready=$true;TimedOut=$false;Snapshot=$Read.Snapshot}}
    if(-not $Read.TimedOut -and $Read.Clock.Elapsed.TotalSeconds -ge $TimeoutSeconds){
        $Read.TimedOut=$true
        if(-not $Read.Process.HasExited){$Read.Process.Kill()}
    }
    if($Read.TimedOut -and $Read.Process.HasExited){
        return [pscustomobject]@{Ready=$true;TimedOut=$true;Snapshot=(New-IMCUnknownStatus 'Statusabfrage: Timeout')}
    }
    # Never wait for a running child or unfinished pipe drain in the input loop.
    if(-not $Read.Process.HasExited -or -not $Read.Output.IsCompleted -or -not $Read.Error.IsCompleted){
        return [pscustomobject]@{Ready=$false;TimedOut=$Read.TimedOut;Snapshot=$null}
    }
    try {
        if($Read.TimedOut){throw 'Statusabfrage: Timeout'}
        if($Read.Process.ExitCode -ne 0 -or $Read.Output.IsFaulted -or $Read.Error.IsFaulted){throw 'Statusabfrage fehlgeschlagen'}
        $value=$Read.Output.GetAwaiter().GetResult() | ConvertFrom-Json -ErrorAction Stop
        if($null -eq $value -or $value -is [array] -or $value.status -notin @('IDLE','STARTING','RUNNING','FINISHING','BLOCKED','COMPLETED')){
            throw 'Statusabfrage: ungueltige Antwort'
        }
        if($value.status -ne 'IDLE'){
            foreach($key in @('started_at','planned_end','elapsed_seconds','remaining_seconds','heartbeat','heartbeat_age_seconds')){
                $number=$value.$key
                if($number -isnot [ValueType] -or $number -is [bool] -or [double]::IsNaN([double]$number) -or [double]::IsInfinity([double]$number)){
                    throw 'Statusabfrage: unvollstaendige Zeitdaten'
                }
            }
        }
        $value | Add-Member -NotePropertyName view_received_at -NotePropertyValue ([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000) -Force
        return [pscustomobject]@{Ready=$true;TimedOut=$false;Snapshot=$value}
    } catch {
        return [pscustomobject]@{Ready=$true;TimedOut=$Read.TimedOut;Snapshot=(New-IMCUnknownStatus $_.Exception.Message)}
    }
}
function Get-IMCFrameLayout([string[]]$Lines,[string]$Prompt,[int]$Width,[int]$Height,[int]$Offset=0) {
    $width=[Math]::Max(1,$Width-1);$height=[Math]::Max(1,$Height)
    $body=[Collections.Generic.List[string]]::new();$accent=[Collections.Generic.List[bool]]::new()
    # Reserve the command/gate footer and prompt; long trade rows remain reachable.
    $footerCount=[Math]::Min(2,$Lines.Count)
    for($i=0;$i -lt $Lines.Count-$footerCount;$i++){
        $text=[regex]::Replace($Lines[$i],'[\x00-\x1f\x7f]',' ')
        if(-not $text.Length){$body.Add('');$accent.Add($false)}
        for($at=0;$at -lt $text.Length;$at+=$width){
            $body.Add($text.Substring($at,[Math]::Min($width,$text.Length-$at)));$accent.Add($i -lt 6)
        }
    }
    $pageHeight=[Math]::Max(0,$height-4)
    $maxOffset=[Math]::Max(0,$body.Count-$pageHeight)
    $offset=[Math]::Max(0,[Math]::Min($Offset,$maxOffset))
    $rows=[string[]]::new($height);$colors=[bool[]]::new($height)
    for($row=0;$row -lt $pageHeight;$row++){
        if($offset+$row -lt $body.Count){$rows[$row]=$body[$offset+$row];$colors[$row]=$accent[$offset+$row]}
    }
    if($height -ge 4 -and $maxOffset -gt 0){$rows[$height-4]='PgUp/PgDn: weitere Zeilen ('+($offset+1)+'-'+[Math]::Min($body.Count,$offset+$pageHeight)+' / '+$body.Count+')'}
    for($i=0;$i -lt $footerCount;$i++){
        $at=$height-1-$footerCount+$i
        if($at -ge 0){$rows[$at]=$Lines[$Lines.Count-$footerCount+$i]}
    }
    $promptText=[regex]::Replace($Prompt,'[\x00-\x1f\x7f]',' ')
    # Keep the end of a typed command visible in narrow windows.
    if($promptText.Length -gt $width){$promptText=$promptText.Substring($promptText.Length-$width)}
    $rows[$height-1]=$promptText
    for($i=0;$i -lt $rows.Length;$i++){
        if($null -eq $rows[$i]){$rows[$i]=''}
        if($rows[$i].Length -gt $width){$rows[$i]=$rows[$i].Substring(0,$width)}
    }
    return [pscustomobject]@{Rows=$rows;Accent=$colors;Offset=$offset;CursorColumn=$promptText.Length;Width=$Width;Height=$height}
}
function Write-IMCViewFrame($Frame,$Previous,[bool]$VirtualTerminal) {
    $resized=$null -eq $Previous -or $Previous.Width -ne $Frame.Width -or $Previous.Height -ne $Frame.Height
    if($VirtualTerminal){
        $batch=[Text.StringBuilder]::new();$esc=[char]27
        $null=$batch.Append("$esc[?25l")
        for($i=0;$i -lt $Frame.Rows.Length;$i++){
            if($resized -or $Frame.Rows[$i] -cne $Previous.Rows[$i] -or $Frame.Accent[$i] -ne $Previous.Accent[$i]){
                $null=$batch.Append("$esc["+($i+1)+';1H')
                $null=$batch.Append($(if($Frame.Accent[$i]){"$esc[38;2;185;121;60m"}else{"$esc[0m"}))
                $null=$batch.Append($Frame.Rows[$i]).Append("$esc[0m$esc[K")
            }
        }
        $null=$batch.Append("$esc["+$Frame.Height+';'+($Frame.CursorColumn+1)+"H$esc[?25h")
        # One buffered write, without clearing the screen or scrolling each line.
        [Console]::Write($batch.ToString())
    } else {
        [IMCTerminalVT]::WriteFrame($Frame.Rows,$Frame.Accent,$Frame.CursorColumn)
    }
}
