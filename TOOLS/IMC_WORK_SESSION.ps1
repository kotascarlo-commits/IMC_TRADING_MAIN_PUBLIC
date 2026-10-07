# Pure argument builder. Never starts a model, broker or trading process.
function Get-IMCWorkSessionArguments {
    param(
        [Parameter(Mandatory)]$Config,
        [ValidateSet('routine','standard','complex')][string]$Level = 'standard'
    )
    if ($Config.work.sandbox_mode -cne 'danger-full-access' -or
        $Config.work.approval_policy -cne 'never' -or
        $Config.work.approvals_reviewer -cne 'user') {
        throw 'IMC development work requires danger-full-access/never; live broadcast remains a separate owner-only gate.'
    }
    if ($Config.work.model -cne 'gpt-6.1-sol' -or
        $Config.work.$Level -cnotin @('low','medium','high')) {
        throw 'Invalid IMC work model/reasoning configuration.'
    }
    return @('-m', $Config.work.model,
        '-c', ('model_reasoning_effort="{0}"' -f $Config.work.$Level),
        '-c', ('approvals_reviewer="{0}"' -f $Config.work.approvals_reviewer),
        '-s', $Config.work.sandbox_mode, '-a', $Config.work.approval_policy, '--search')
}

# Windows PowerShell uses the legacy native argument transport.
function ConvertTo-IMCNativeArguments {
    param([Parameter(Mandatory)][string[]]$Arguments)
    $nativeArgumentMode = Get-Variable PSNativeCommandArgumentPassing -ErrorAction SilentlyContinue
    if (-not $nativeArgumentMode -or $nativeArgumentMode.Value -eq 'Legacy') {
        return @($Arguments | ForEach-Object { $_.Replace('"', '\"') })
    }
    return $Arguments
}
