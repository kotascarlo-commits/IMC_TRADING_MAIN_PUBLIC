# Own IPC directories only; retain files rather than recursively deleting them.
. (Join-Path $PSScriptRoot 'GMGN-ReadProtocol.ps1')
. (Join-Path $PSScriptRoot 'IMC_RUNTIME_JOB.ps1')
$script:IMCOwnedBrokerDirectories=@{}
if (-not ('IMCExclusiveDirectory' -as [type])) {
Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
public static class IMCExclusiveDirectory {
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]
    static extern bool CreateDirectory(string path,IntPtr attributes);
    public static void Create(string path) {
        if (!CreateDirectory(path,IntPtr.Zero)) throw new Win32Exception();
    }
}
'@
}
function New-IMCOwnedBrokerDirectory([string]$Parent,[string]$Name) {
    $parentPath=Assert-GmgnLocalPath $Parent
    if ($Name -cnotmatch '\Aimc-(?:gmgn|astra)-broker-[0-9a-f]{32}\z') { throw 'IMC_IPC_NAME_INVALID' }
    $path=[IO.Path]::GetFullPath((Join-Path $parentPath $Name))
    if ([IO.Path]::GetDirectoryName($path) -cne $parentPath.TrimEnd('\')) { throw 'IMC_IPC_PARENT_INVALID' }
    [IMCExclusiveDirectory]::Create($path)
    $null=Assert-GmgnLocalPath $path
    $owned=[pscustomobject]@{Path=$path; Parent=$parentPath; Stop=(Join-Path $path 'broker.stop')}
    $script:IMCOwnedBrokerDirectories[$path]=$owned
    return $owned
}
function Stop-IMCOwnedWorkBrokers($Job,[object[]]$Directories) {
    foreach($directory in $Directories) {
        if (-not $directory) { continue }
        try {
            if (-not [object]::ReferenceEquals($script:IMCOwnedBrokerDirectories[$directory.Path],$directory)) { continue }
            if ((Assert-GmgnLocalPath $directory.Path) -cne $directory.Path -or
                [IO.Path]::GetDirectoryName($directory.Path) -cne $directory.Parent.TrimEnd('\') -or
                $directory.Stop -cne (Join-Path $directory.Path 'broker.stop')) { continue }
            # Never overwrite an existing or linked marker.
            $stream=[IO.File]::Open($directory.Stop,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
            try { $bytes=[Text.Encoding]::ASCII.GetBytes('stop'); $stream.Write($bytes,0,$bytes.Length) }
            finally { $stream.Dispose() }
        } catch {}
    }
    # The job owns broker descendants, including an in-flight read-only Advisor.
    if ($Job) { $Job.Dispose() }
}
