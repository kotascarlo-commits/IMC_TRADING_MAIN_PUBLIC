param([Parameter(Mandatory)][string]$Workspace,
 [ValidateSet('Binding','Expired','Frequency','Overflow','Zero','Regression','Deadline','OneTick','Partial','CloseAfterElapsed')][string]$Case)
$ErrorActionPreference='Stop'
. (Join-Path (Split-Path -Parent $PSScriptRoot) 'TOOLS\IMC_STORAGE_GUARDS.ps1')
$f=[Reflection.BindingFlags]'Instance,NonPublic'
$frequency=[Diagnostics.Stopwatch]::Frequency
$end=[Diagnostics.Stopwatch]::GetTimestamp()+30*$frequency
$baseline=[IMCStorageGuards]::RetainedCount
if($Case -cin @('Expired','Frequency','Overflow','Zero')) {
 $invalidEnd=$end;$invalidFrequency=$frequency
 switch($Case){
  'Expired'{$invalidEnd=[Diagnostics.Stopwatch]::GetTimestamp()}
  'Frequency'{$invalidFrequency++}
  'Overflow'{$invalidEnd=[long]::MaxValue}
  'Zero'{$invalidEnd=0L}
 }
 $code='';try{$null=[IMCStorageGuards]::new($Workspace,('a'*32),$invalidEnd,$invalidFrequency)}catch{$code=$_.Exception.GetBaseException().Message}
 if($code -cne 'STORAGE_GENERATION_INVALID' -or [IMCStorageGuards]::RetainedCount -ne $baseline){throw 'OWN_ABSOLUTE_CONSTRUCTOR_LEAK'}
 Write-Output 'ABSOLUTE_GUARD_PASS';exit 0
}
$group=[IMCStorageGuards]::new($Workspace,('a'*32),$end,$frequency)
$source=Join-Path $Workspace 'source.db'
if($Case -ceq 'Binding') {
 $other=[IMCStorageGuards]::new($Workspace,('b'*32),$end,$frequency)
 foreach($item in @($group,$other)) {
  if($item.GetType().GetField('endTick',$f).GetValue($item) -ne $end -or
     $item.GetType().GetField('frequency',$f).GetValue($item) -ne $frequency){throw 'OWN_ABSOLUTE_RENEWED'}
 }
 $group.Acquire([string[]]@('source'),[string[]]@($source),[string[]]@())
 $other.AcquireScopes([string[]]@(Join-Path $Workspace 'journal'))
 $group.Close();$other.AssertAll()
 if([IMCStorageGuards]::RetainedCount -ne $baseline+1){throw 'OWN_ABSOLUTE_OWNERSHIP'}
 $other.Close()
 if([IMCStorageGuards]::RetainedCount -ne $baseline){throw 'OWN_ABSOLUTE_CLEANUP'}
 Write-Output 'ABSOLUTE_GUARD_PASS';exit 0
}
if($Case -ceq 'Partial') {
 $code='';try{$group.Acquire([string[]]@('source','duplicate'),[string[]]@($source,$source),[string[]]@())}catch{$code=$_.Exception.GetBaseException().Message}
 $all=$group.GetType().GetField('all',$f).GetValue($group)
 if(-not $group.Faulted -or $group.Closed -or $code -cne 'STORAGE_MEMBER_ALIAS_OR_VOLUME'){throw 'OWN_ABSOLUTE_NO_PARTIAL_FAULT'}
 $open=0;foreach($member in $all){
  if($member.GetType().GetField('Path',$f).GetValue($member) -ceq $source -and
     $member.GetType().GetField('Handle',$f).GetValue($member) -ne [IntPtr]::Zero){$open++}
 }
 if($open -ne 2){throw 'OWN_ABSOLUTE_PARTIAL_HANDLES_LOST'}
}else{
 $group.Acquire([string[]]@('source'),[string[]]@($source),[string[]]@())
 if($Case -ceq 'CloseAfterElapsed') {
  $group.GetType().GetField('endTick',$f).SetValue($group,[long]1)
  $group.Close()
  if(-not $group.Closed -or [IMCStorageGuards]::RetainedCount -ne $baseline){throw 'OWN_ABSOLUTE_CLOSE_CHANGED'}
  Write-Output 'ABSOLUTE_GUARD_PASS';exit 0
 }
 if($Case -ceq 'Regression'){$group.GetType().GetField('lastTick',$f).SetValue($group,[long]::MaxValue);$expected='STORAGE_DEADLINE_COUNTER'}
 else{
  $expiredEnd=if($Case -ceq 'OneTick'){[Diagnostics.Stopwatch]::GetTimestamp()+1}else{1L}
  $group.GetType().GetField('endTick',$f).SetValue($group,[long]$expiredEnd);$expected='STORAGE_DEADLINE'
 }
 $code='';try{$group.AssertAll()}catch{$code=$_.Exception.GetBaseException().Message}
 if($code -cne $expected){throw ('OWN_ABSOLUTE_WRONG_ERROR_'+$code)}
}
if(-not $group.Faulted -or $group.Closed -or [IMCStorageGuards]::RetainedCount -ne $baseline+1){throw 'OWN_ABSOLUTE_FAULT_NOT_RETAINED'}
foreach($operation in @('Check','Close')) {
 $code='';try{if($operation -ceq 'Check'){$group.AssertAll()}else{$group.Close()}}catch{$code=$_.Exception.GetBaseException().Message}
 $expected=if($operation -ceq 'Check'){'STORAGE_GROUP_UNCONFIRMED'}else{'STORAGE_CLOSE_UNCONFIRMED'}
 if($code -cne $expected){throw 'OWN_ABSOLUTE_FAULT_RESET'}
}
try{$stream=[IO.File]::Open($source,[IO.FileMode]::Open,[IO.FileAccess]::Write,[IO.FileShare]::ReadWrite);$stream.Dispose();throw 'OWN_ABSOLUTE_SOURCE_UNLOCKED'}catch [IO.IOException]{}
Write-Output 'ABSOLUTE_GUARD_PASS'
