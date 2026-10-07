# This entry is itself pinned and hash-checked by the invoking client.
param([Parameter(Mandatory=$true)][string]$SourceDirectory)
$ErrorActionPreference='Stop'
$root='%USERPROFILE%\Desktop\IMC_TRADING_MAIN'
if($SourceDirectory -cne (Join-Path $root 'TOOLS') -and $SourceDirectory -cnotmatch '\AC:\\Users\\<local-user>\\Desktop\\IMC_TRADING_MAIN\\[.]local-tools\\legacy-capture-native-[0-9a-f]{32}\\primary\z'){throw 'GMGN_CLIENT_SOURCE_PATH'}
$hashes=@{
 GmgnRepairNamespace='07ae7401e3496e37955e8b97cbae55c936fb205144c702c2a39551707497c62c'
 GmgnReadDirectoryIdentity='ac4d12ff807b6b14ecd4c1aeb627f2f9762ffc10111cce6a171386fddc82152f'
 GmgnRepairProcessBinding='f965b581616836846cc3a6fcf1ebcb5bd424485023987909fae0bf7b76c0b2b9'
 GmgnReadClientIdentity='f73dfb7d39aae80982ae6c6af06cedfa45e82c1d242406cd476931884fc21684'
}
$retained=[Collections.Generic.List[object]]::new();$code=[Collections.Generic.List[string]]::new()
foreach($name in @('GmgnRepairNamespace','GmgnReadDirectoryIdentity','GmgnRepairProcessBinding','GmgnReadClientIdentity')){
 if($name -as [type]){throw 'GMGN_CLIENT_TYPE_PRESENT'}
 $path=Join-Path $SourceDirectory ($name+'.cs')
 $stream=[IO.FileStream]::new($path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read);$retained.Add($stream)
 if($stream.Length -le 0 -or $stream.Length -gt 2097152){throw 'GMGN_CLIENT_SOURCE_SIZE'}
 $sha=[Security.Cryptography.SHA256]::Create()
 try{$actual=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
 if($actual -cne $hashes[$name]){throw 'GMGN_CLIENT_SOURCE_CHANGED'}
 $stream.Position=0;$reader=[IO.StreamReader]::new($stream,[Text.UTF8Encoding]::new($false,$true),$true,4096,$true)
 try{$code.Add($reader.ReadToEnd())}finally{$reader.Dispose()}
}
$compiler=[Microsoft.CSharp.CSharpCodeProvider]::new();$parameters=[CodeDom.Compiler.CompilerParameters]::new();$parameters.GenerateInMemory=$true
foreach($reference in @('System.dll','System.Core.dll')){$null=$parameters.ReferencedAssemblies.Add($reference)}
try{$compiled=$compiler.CompileAssemblyFromSource($parameters,$code.ToArray())}finally{$compiler.Dispose()}
if($compiled.Errors.HasErrors){throw 'GMGN_CLIENT_NATIVE_COMPILE'}
foreach($name in $hashes.Keys){if(-not [object]::ReferenceEquals(($name -as [type]).Assembly,$compiled.CompiledAssembly)){throw 'GMGN_CLIENT_NATIVE_PROVENANCE'}}
# Namespace pins close the path check and survive until this client exits.
$namespace=[GmgnRepairNamespace]::new($SourceDirectory);$namespace.Check()
$configNamespace=[GmgnRepairNamespace]::new((Join-Path (Split-Path -Parent $SourceDirectory) 'config'));$configNamespace.Check()
[pscustomobject]@{Streams=$retained;Namespace=$namespace;ConfigNamespace=$configNamespace;Assembly=$compiled.CompiledAssembly}
