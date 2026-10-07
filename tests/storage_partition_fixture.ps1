param([Parameter(Mandatory)][string]$Workspace,[ValidateSet('Normal','LeafCollision','IdentityWidth')][string]$Case='Normal')
$ErrorActionPreference='Stop'
. (Join-Path (Split-Path -Parent $PSScriptRoot) 'TOOLS\IMC_STORAGE_GUARDS.ps1')
Add-Type -ReferencedAssemblies @('System.dll','System.Numerics.dll') -TypeDefinition @'
using System;using System.IO;using System.Numerics;using System.Runtime.InteropServices;using System.Globalization;
public static class OwnPartitionFileID {
 [StructLayout(LayoutKind.Sequential)]struct ID{public ulong Volume;[MarshalAs(UnmanagedType.ByValArray,SizeConst=16)]public byte[] Bytes;}
 [StructLayout(LayoutKind.Sequential)]struct Standard{public long Allocation,Bytes;public uint Links;public byte DeletePending,Directory;}
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool GetFileInformationByHandleEx(IntPtr h,int kind,ref ID value,uint size);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool GetFileInformationByHandleEx(IntPtr h,int kind,ref Standard value,uint size);
 public static string Read(FileStream stream){ID id=new ID();Standard s=new Standard();
  if(stream.SafeFileHandle.IsInvalid||stream.SafeFileHandle.IsClosed||!GetFileInformationByHandleEx(stream.SafeFileHandle.DangerousGetHandle(),18,ref id,24)||
    !GetFileInformationByHandleEx(stream.SafeFileHandle.DangerousGetHandle(),1,ref s,24)||s.Links!=1||s.DeletePending!=0||s.Directory!=0)throw new Exception("OWN_PARTITION_NATIVE_FD");
  return Format(id.Volume,id.Bytes,s.Bytes);}
 public static string Format(ulong volume,byte[] id,long bytes){byte[] unsigned=new byte[17];Array.Copy(id,unsigned,16);
  return "{\"volume\":"+volume.ToString(CultureInfo.InvariantCulture)+",\"file_id\":"+new BigInteger(unsigned).ToString(CultureInfo.InvariantCulture)+",\"bytes\":"+bytes.ToString(CultureInfo.InvariantCulture)+"}";}
}
'@
if($Case -ceq 'IdentityWidth'){
 $id=[byte[]]::new(16);$id[15]=128
 $first=[OwnPartitionFileID]::Format([uint64]::MaxValue,$id,7)
 if($first -cne [IMCStorageGuards]::IdentityJSON([uint64]::MaxValue,$id,7) -or
    $first -cne '{"volume":18446744073709551615,"file_id":170141183460469231731687303715884105728,"bytes":7}'){throw 'OWN_PARTITION_WIDTH_FIRST'}
 $id[0]=1;$second=[OwnPartitionFileID]::Format([uint64]::MaxValue,$id,7)
 if($second -cne [IMCStorageGuards]::IdentityJSON([uint64]::MaxValue,$id,7) -or $first -ceq $second -or
    $second -cne '{"volume":18446744073709551615,"file_id":170141183460469231731687303715884105729,"bytes":7}'){throw 'OWN_PARTITION_WIDTH_SECOND'}
 [pscustomobject]@{pass=$true;case=$Case;identity_width_verified=$true;original_fds=0;publication_allowed=$false;provider_calls=0}|ConvertTo-Json -Compress;exit 0
}
$nonce=[guid]::NewGuid().ToString('N')
foreach($leaf in @('source','work','archive','raw','evidence','RUNTIME','journal')){$null=[IO.Directory]::CreateDirectory((Join-Path $Workspace $leaf))}
$movingPaths=[string[]]@((Join-Path $Workspace 'source\imc-main.db'),(Join-Path $Workspace 'source\imc-main.db-wal'),(Join-Path $Workspace 'work\imc-main.db'))
$evidencePaths=[string[]]@((Join-Path $Workspace 'raw\imc-main.db'),(Join-Path $Workspace 'raw\imc-main.db-wal'))
foreach($name in @('controller','intent','done','expected','sqlite','empty')){$evidencePaths+=[string](Join-Path $Workspace ('evidence\'+$name+'.dat'))}
$evidencePaths+=[string](Join-Path $Workspace 'RUNTIME\core-storage-copy.json')
$paths=[string[]](@($movingPaths)+@($evidencePaths));$original=[Collections.Generic.List[IO.FileStream]]::new();$snapshots=@();$digests=@()
for($i=0;$i -lt 12;$i++){
 [IO.File]::WriteAllText($paths[$i],('OWN_PARTITION_'+$i),[Text.UTF8Encoding]::new($false))
 $stream=[IO.File]::Open($paths[$i],[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read);$original.Add($stream)
 $snapshots+=[OwnPartitionFileID]::Read($stream)
 $digests+=(Get-FileHash -LiteralPath $paths[$i]).Hash.ToLowerInvariant()
}
$frequency=[Diagnostics.Stopwatch]::Frequency;$end=[Diagnostics.Stopwatch]::GetTimestamp()+30*$frequency
$moving=[IMCStorageGuards]::new($Workspace,$nonce,$end,$frequency)
$evidence=[IMCStorageGuards]::new($Workspace,$nonce,$end,$frequency)
$movingKeys=[string[]]@('source_main','source_wal','work_main');$evidenceKeys=[string[]]@(0..8|ForEach-Object {'evidence_'+$_})
$moving.Acquire($movingKeys,$movingPaths,[string[]]@(Join-Path $Workspace 'archive'))
if($Case -ceq 'LeafCollision'){
 $code='';try{$evidence.AcquireScopes([string[]]@(Join-Path $Workspace 'source'))}catch{$code=$_.Exception.GetBaseException().Message}
 if($code -cne 'STORAGE_NATIVE_ERROR_80' -or -not $evidence.Faulted -or $evidence.Closed -or [IMCStorageGuards]::RetainedCount -ne 2){throw ('OWN_PARTITION_COLLISION_'+$code)}
 $moving.AssertAll();foreach($stream in $original){$null=[OwnPartitionFileID]::Read($stream)}
 [pscustomobject]@{pass=$true;case=$Case;original_fds=12;publication_allowed=$false;provider_calls=0}|ConvertTo-Json -Compress;exit 0
}
$evidence.Acquire($evidenceKeys,$evidencePaths,[string[]]@(Join-Path $Workspace 'journal'))
$evidence.TakeStrong($evidenceKeys,$evidencePaths)
for($i=0;$i -lt 12;$i++){
 $guard=if($i -lt 3){$moving}else{$evidence};$key=if($i -lt 3){$movingKeys[$i]}else{$evidenceKeys[$i-3]}
 $actual=$guard.MemberJSON($key)
 if($actual -cne $snapshots[$i]){throw 'OWN_PARTITION_ID_MISMATCH'}
 if($guard.Hash($key) -cne $digests[$i]){throw 'OWN_PARTITION_HASH_MISMATCH'}
 $null=[OwnPartitionFileID]::Read($original[$i])
}
# Only the fixture's original file owners close here; no process or mutex
# handover is represented by this step.
foreach($stream in $original){$handle=$stream.SafeFileHandle;$stream.Dispose();if(-not $handle.IsClosed){throw 'OWN_PARTITION_ORIGINAL_CLOSE'}}
foreach($path in $evidencePaths){try{$test=[IO.File]::Open($path,[IO.FileMode]::Open,[IO.FileAccess]::Write,[IO.FileShare]::ReadWrite);$test.Dispose();throw 'OWN_PARTITION_STRONG_LOST'}catch [IO.IOException]{}}
$marker=Join-Path $Workspace 'RUNTIME\maintenance-state.json';[IO.File]::WriteAllText($marker,'OWN_MARKER_AFTER_ORIGINAL_CLOSE')
$evidence.AddStrongRead('marker',$marker)
$journal=Join-Path $Workspace 'journal\00-INTENT.json';[IO.File]::WriteAllText($journal,'OWN_JOURNAL_RECORD')
$evidence.AddStrongRead('journal_0',$journal)
if($evidence.SentinelPaths.Count -ne 4){throw 'OWN_PARTITION_LEAF_INVENTORY'}
$moving.Close();$evidence.AssertAll()
if([IMCStorageGuards]::RetainedCount -ne 1){throw 'OWN_PARTITION_INDEPENDENCE'}
$evidence.Close()
if([IMCStorageGuards]::RetainedCount -ne 0){throw 'OWN_PARTITION_CLEANUP'}
[pscustomobject]@{pass=$true;case=$Case;original_fds=12;moving_count=3;evidence_count=9;overlap_hashes=12;strong_before_original_close=$true;appended=2;publication_allowed=$false;provider_calls=0}|ConvertTo-Json -Compress
