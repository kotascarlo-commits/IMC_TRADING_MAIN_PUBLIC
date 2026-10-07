param([string]$Workspace,[ValidateSet('Normal','Weak','Sentinel','DuplicateKey','DuplicatePath','Hardlink','Unpinned','BrokenChain','Inspection','PriorFault')][string]$Case)
$ErrorActionPreference='Stop'
. (Join-Path (Split-Path -Parent $PSScriptRoot) 'TOOLS\IMC_STORAGE_GUARDS.ps1')
$nonce='a'*32;$leaf=Join-Path $Workspace 'journal';$outside=Join-Path $Workspace 'unpinned'
$null=[IO.Directory]::CreateDirectory($leaf);$null=[IO.Directory]::CreateDirectory($outside)
$first=Join-Path $leaf 'first.json';[IO.File]::WriteAllText($first,'first')
$group=[IMCStorageGuards]::new($Workspace,$nonce,30)
if($Case -ceq 'Weak'){$group.Acquire([string[]]@('first'),[string[]]@($first),[string[]]@())}
else{$group.AcquireScopes([string[]]@($leaf))}
$flags=[Reflection.BindingFlags]'Instance,NonPublic'
$all=[IMCStorageGuards].GetField('all',$flags).GetValue($group)
$files=[IMCStorageGuards].GetField('files',$flags).GetValue($group)
if($Case -ceq 'Normal') {
    foreach($i in 0..12){
        $path=Join-Path $leaf ($i.ToString('00')+'.json');[IO.File]::WriteAllText($path,('record-'+$i))
        $group.AddStrongRead([string]$i,$path);$group.AssertAll()
        if($group.Hash([string]$i).Length -ne 64){throw 'fixture_append_hash'}
        try{[IO.File]::Open($path,[IO.FileMode]::Open,[IO.FileAccess]::Write,[IO.FileShare]::Read).Dispose();throw 'fixture_append_write_admitted'}catch [IO.IOException]{}
        try{[IO.File]::Move($path,($path+'.moved'));throw 'fixture_append_rename_admitted'}catch [IO.IOException]{}
    }
    if($files.Count -ne 13){throw 'fixture_append_inventory'}
    $group.Close();if(-not $group.Closed -or [IMCStorageGuards]::RetainedCount -ne 0){throw 'fixture_append_close'}
    Write-Output 'STRONG_APPEND_NATIVE_PASS';exit 0
}
$key='next';$path=Join-Path $leaf 'next.json';[IO.File]::WriteAllText($path,'next')
$expected=@{
    Weak='STORAGE_STRONG_APPEND_INVENTORY';Sentinel='STORAGE_STRONG_APPEND_SENTINEL';DuplicateKey='STORAGE_STRONG_APPEND_INVENTORY';
    DuplicatePath='STORAGE_STRONG_APPEND_ALIAS';Hardlink='STORAGE_MEMBER_SHAPE';Unpinned='STORAGE_STRONG_APPEND_UNPINNED';
    BrokenChain='STORAGE_STRONG_APPEND_CHAIN';Inspection='STORAGE_NATIVE_ERROR_5';PriorFault='STORAGE_GROUP_UNCONFIRMED'
}[$Case]
if($Case -cin @('DuplicateKey','DuplicatePath','PriorFault')){$group.AddStrongRead('first',$first)}
switch -CaseSensitive ($Case) {
    'Sentinel' {$path=$group.SentinelPaths[0]}
    'DuplicateKey' {$key='first'}
    'DuplicatePath' {$path=$first}
    'Hardlink' {
        $path=Join-Path $leaf 'alias.json'
        $null=New-Item -ItemType HardLink -Path $path -Target $first
    }
    'Unpinned' {$path=Join-Path $outside 'next.json';[IO.File]::WriteAllText($path,'next')}
    'BrokenChain' {
        $dirs=[IMCStorageGuards].GetField('directories',$flags).GetValue($group)
        if(-not $dirs.Remove([IO.Path]::GetFullPath($Workspace))){throw 'fixture_chain_not_present'}
    }
    'Inspection' {$path=Join-Path $leaf 'directory.json';$null=[IO.Directory]::CreateDirectory($path)}
    'PriorFault' {
        try{$group.AddStrongRead('first',$path);throw 'fixture_prior_fault_admitted'}
        catch{if($_.Exception.GetBaseException().Message -cne 'STORAGE_STRONG_APPEND_INVENTORY'){throw}}
    }
}
$before=$all.Count
try{$group.AddStrongRead($key,$path);throw 'fixture_invalid_append_admitted'}
catch{if($_.Exception.GetBaseException().Message -cne $expected){throw}}
if(-not $group.Faulted -or $group.Closed -or [IMCStorageGuards]::RetainedCount -ne 1){throw 'fixture_append_fault_not_retained'}
if($Case -ceq 'Hardlink' -and $all.Count -ne ($before+1)){throw 'fixture_partial_acquisition_not_owned'}
if($Case -cne 'Hardlink' -and $all.Count -ne $before){throw 'fixture_invalid_append_opened_member'}
$retained=$all.Count
try{$group.AddStrongRead('retry',$first);throw 'fixture_fault_retry_admitted'}
catch{if($_.Exception.GetBaseException().Message -cne 'STORAGE_GROUP_UNCONFIRMED'){throw}}
try{$group.Close();throw 'fixture_fault_close_admitted'}
catch{if($_.Exception.GetBaseException().Message -cne 'STORAGE_CLOSE_UNCONFIRMED'){throw}}
if($all.Count -ne $retained -or $group.Closed){throw 'fixture_fault_released_member'}
Write-Output 'STRONG_APPEND_FAULT_RETAINED'
