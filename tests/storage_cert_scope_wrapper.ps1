param([Parameter(Mandatory)][hashtable]$Arguments,[Parameter(Mandatory)][string]$CodeRoot)
# Real private & scope, with a nested & inspector; no production invocation.
. (Join-Path $CodeRoot 'TOOLS\IMC_COPY_CERTIFICATION.ps1')
$beforeJob=$job;$beforeStreams=$streams;$beforeRecordReference=$record
$beforeRecord=$record|ConvertTo-Json -Depth 10 -Compress
$script:IMCProcessRoot=$CodeRoot
$processAst=[Management.Automation.Language.Parser]::ParseFile((Join-Path $CodeRoot 'LAUNCHERS\IMC_PROCESS_COMMON.ps1'),[ref]$null,[ref]$null)
$table=@($processAst.EndBlock.Statements|Where-Object {
    $_ -is [Management.Automation.Language.AssignmentStatementAst] -and
    $_.Left -is [Management.Automation.Language.VariableExpressionAst] -and
    $_.Left.VariablePath.UserPath -ceq 'script:IMCScripts'})
if($table.Count -ne 1){throw 'fixture_scope_table_missing'}
. ([scriptblock]::Create($table[0].Extent.Text))
$role=Get-IMCScriptRole (Join-Path $CodeRoot 'TOOLS\Invoke-GMGN-ReadBroker.ps1') 'shell'
if($role.Role -cne 'GMGNReadClient' -or -not $role.DevelopmentOnly){throw 'fixture_process_scope_unbound'}
function Get-CimInstance {
    param([Parameter(Mandatory)][string]$ClassName,[string]$Filter)
    if($ClassName -cne 'Win32_Process'){throw 'fixture_cim_class_invalid'}
    return @(
        [pscustomobject]@{ProcessId=7117;Name='powershell.exe';ExecutablePath='C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe';
            CommandLine=('powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+(Join-Path $CodeRoot 'TOOLS\Invoke-GMGN-ReadBroker.ps1')+'"');
            ParentProcessId=0;CreationDate=[datetime]'2026-10-03T00:00:00Z'},
        [pscustomobject]@{ProcessId=7118;Name='python.exe';ExecutablePath=(Join-Path $CodeRoot '.venv\Scripts\python.exe');
            CommandLine=('python.exe -I "'+(Join-Path $CodeRoot 'scripts\maintenance_state.py')+'"');
            ParentProcessId=0;CreationDate=[datetime]'2026-10-03T00:00:00Z'}
    )
}
$snapshot=Get-IMCProcessSnapshot
if($snapshot.Processes.Count -or $snapshot.UnknownInterpreterPids.Count -ne 1 -or
    $snapshot.UnknownInterpreterPids[0] -ne 7118){throw 'fixture_inherited_snapshot_scope_invalid'}
try {
    $completed=Invoke-IMCHeldCopyCertification @Arguments
    if(-not $completed.Complete){throw 'fixture_certificate_incomplete'}
} finally {
    if(-not [object]::ReferenceEquals($record,$beforeRecordReference) -or
        -not [object]::ReferenceEquals($job,$beforeJob) -or -not [object]::ReferenceEquals($streams,$beforeStreams) -or
        ($record|ConvertTo-Json -Depth 10 -Compress) -cne $beforeRecord){throw 'fixture_original_reference_changed'}
    & (Join-Path $PSScriptRoot 'storage_cert_retention_inspector.ps1') -State $global:IMCHeldCertifications[-1]
}
