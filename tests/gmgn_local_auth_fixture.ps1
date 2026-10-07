param([ValidateSet('Normal','WrongHash','WrongFileId','WrongDirectoryId','Missing','InvalidKey','CreateCollision','BodyLimit','MacMismatch','Abort','BodyBoundary','MultibyteLimit','UnpairedSurrogate','EmptyBody','EntropyVector','NullBody')][string]$Case='Normal')
$ErrorActionPreference='Stop';$repo=Split-Path -Parent $PSScriptRoot;$tools=Join-Path $repo 'TOOLS'
Add-Type -Path @((Join-Path $tools 'GmgnRepairNamespace.cs'),(Join-Path $tools 'GmgnReadDirectoryIdentity.cs'),(Join-Path $tools 'GmgnReadLocalAuth.cs'))
$directory=Join-Path (Join-Path $repo '.local-tools') ('gmgn-read-auth-'+[guid]::NewGuid().ToString('N'))
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
# Fake local IPC bytes, never provider credentials.
$key=[Text.Encoding]::ASCII.GetBytes('f'*64);$body='{"fixture":"local-ipc-only"}';$code='';$auth=$null
function Cause($record){$e=$record.Exception;while($e.InnerException){$e=$e.InnerException};return $e.Message}
try{
 if($Case -ceq 'Missing'){$null=[GmgnReadLocalAuth]::OpenExisting($directory,$sid,('0'*16+':'+('0'*32)),('0'*16+':'+('0'*32)),('0'*64))}
 elseif($Case -ceq 'InvalidKey'){$null=[GmgnReadLocalAuth]::CreateNew($directory,$sid,[byte[]]@(1,2,3))}
 else{
  $auth=[GmgnReadLocalAuth]::CreateNew($directory,$sid,$key);$binding=$auth.ReadPublicBinding()
  $hmac=[Security.Cryptography.HMACSHA256]::new($key)
  try{$expected=[BitConverter]::ToString($hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes($body))).Replace('-','').ToLowerInvariant()}finally{$hmac.Dispose()}
  if($auth.Sign($body) -cne $expected -or -not $auth.Verify($body,$expected)){throw 'OWN_LOCAL_AUTH_HMAC_COMPATIBILITY'}
  switch($Case){
   'CreateCollision'{$null=[GmgnReadLocalAuth]::CreateNew($directory,$sid,$key)}
   'BodyLimit'{$null=$auth.Sign('x'*32769)}
   'MultibyteLimit'{$null=$auth.Sign(([string][char]0x20ac)*10923)}
   'UnpairedSurrogate'{$null=$auth.Sign([string][char]0xd800)}
   'EmptyBody'{$null=$auth.Sign('')}
   'NullBody'{$null=$auth.Sign($null)}
   'BodyBoundary'{if($auth.Sign('x'*32768).Length -ne 64){throw 'OWN_LOCAL_AUTH_EXACT_BOUNDARY'}}
   'EntropyVector'{
    Add-Type -AssemblyName System.Security
    $sha=[Security.Cryptography.SHA256]::Create()
    try{$entropy=$sha.ComputeHash([Text.Encoding]::ASCII.GetBytes('IMC_GMGN_LOCAL_IPC_AUTH_V1'+[char]0+$sid))}finally{$sha.Dispose()}
    # Independent managed DPAPI implementation checks the native wire contract;
    # fake local IPC bytes are compared only in memory and never printed.
    $decoded=[Security.Cryptography.ProtectedData]::Unprotect([IO.File]::ReadAllBytes((Join-Path $directory 'store.dpapi')),$entropy,[Security.Cryptography.DataProtectionScope]::CurrentUser)
    try{
     if($decoded.Length -ne 64){throw 'OWN_LOCAL_AUTH_ENTROPY_VECTOR_SIZE'}
     foreach($byte in $decoded){if($byte -ne [byte][char]'f'){throw 'OWN_LOCAL_AUTH_ENTROPY_VECTOR_MISMATCH'}}
    }finally{[Array]::Clear($decoded,0,$decoded.Length)}
   }
   'MacMismatch'{if($auth.Verify($body,('0'*64))){throw 'OWN_LOCAL_AUTH_BAD_MAC_ACCEPTED'}}
   'Abort'{$auth.Abort();$null=$auth.Sign($body)}
   default{
    $dirId=if($Case -ceq 'WrongDirectoryId'){'0'*16+':'+('0'*32)}else{$binding[3]}
    $fileId=if($Case -ceq 'WrongFileId'){'0'*16+':'+('0'*32)}else{$binding[4]}
    $hash=if($Case -ceq 'WrongHash'){'0'*64}else{$binding[5]}
    $opened=[GmgnReadLocalAuth]::OpenExisting($directory,$sid,$dirId,$fileId,$hash)
    if($opened.Sign($body) -cne $expected){throw 'OWN_LOCAL_AUTH_REOPEN_MISMATCH'}
   }
  }
 }
}catch{$code=Cause $_}
$expectedCode=switch($Case){'WrongHash'{'GMGN_AUTH_CIPHERTEXT_HASH'};'WrongFileId'{'GMGN_AUTH_FILE_BINDING'};'WrongDirectoryId'{'GMGN_AUTH_DIRECTORY_BINDING'};'Missing'{'GMGN_REPAIR_NAMESPACE_OPEN_FAILED'};'InvalidKey'{'GMGN_AUTH_LOCAL_KEY_SHAPE'};'CreateCollision'{'GMGN_AUTH_DIRECTORY_CREATE_NEW'};'BodyLimit'{'GMGN_AUTH_BODY_SIZE'};'MultibyteLimit'{'GMGN_AUTH_BODY_SIZE'};'EmptyBody'{'GMGN_AUTH_BODY_SIZE'};'NullBody'{'GMGN_AUTH_BODY_SIZE'};'UnpairedSurrogate'{'GMGN_AUTH_BODY_ENCODING'};'Abort'{'GMGN_AUTH_FAULT'};default{''}}
if($code -cne $expectedCode){throw ('OWN_LOCAL_AUTH_UNEXPECTED_'+$code)}
if($Case -cin @('Missing','InvalidKey') -and [IO.Directory]::Exists($directory)){throw 'OWN_LOCAL_AUTH_UNEXPECTED_REGENERATION'}
if($Case -cin @('BodyLimit','MultibyteLimit','UnpairedSurrogate','EmptyBody','NullBody','Abort')){
 if(-not $auth.Faulted){throw 'OWN_LOCAL_AUTH_FAULT_NOT_PERSISTENT'}
 $again='';try{$null=$auth.Sign($body)}catch{$again=Cause $_}
 if($again -cne 'GMGN_AUTH_FAULT'){throw 'OWN_LOCAL_AUTH_FAULT_REOPENED'}
 $again='';try{$null=$auth.Verify($body,$expected)}catch{$again=Cause $_}
 if($again -cne 'GMGN_AUTH_FAULT'){throw 'OWN_LOCAL_AUTH_VERIFY_REOPENED'}
}
[pscustomobject]@{pass=$true;case=$Case;code=$code;provider_calls=0;production_verified=$false;source_sha256=(Get-FileHash -LiteralPath (Join-Path $tools 'GmgnReadLocalAuth.cs')).Hash.ToLowerInvariant()}|ConvertTo-Json -Compress
