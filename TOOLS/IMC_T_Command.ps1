# Current-user profile loader. Existing `imc` developer launcher remains available.
$existing=Get-Command imc -CommandType Function -ErrorAction SilentlyContinue
if($existing -and -not $existing.Definition.Contains('$imcTerminal')){
    $script:IMCTLegacyCommand=$existing.ScriptBlock
}
$imcTerminal=Join-Path $PSScriptRoot 'IMC_Terminal.ps1'
$imcLegacy=$script:IMCTLegacyCommand
$function:global:imc={
    if($args.Count -gt 0 -and $args[0] -ceq 't') {
        & $imcTerminal @args
    } elseif($imcLegacy) {& $imcLegacy @args}
    else {throw 'Usage: imc t view|start <dauer>|stop|status'}
}.GetNewClosure()
