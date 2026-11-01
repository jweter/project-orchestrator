# Restore the records saved by Promote-DisabledLocalRelease.ps1 so the owner can return to the previously pinned local
# release. Without -Apply this is a pure dry-run: it validates everything, reports the plan and writes nothing.
# It never enables or starts a task or worker, never changes owner control, and never alters the backup it restores from.
param(
  [Parameter(Mandatory=$true)][string]$ExpectedSha,
  [string]$BackupDir,
  [string]$Base="$env:LOCALAPPDATA\ExponentialEngine",
  [switch]$Apply,
  [ValidateSet('Scheduler','Files')][string]$TaskBackend='Scheduler',
  [ValidateSet('','AfterPins','AfterTasks','AfterDesktop')][string]$InjectFailure=''
)
$ErrorActionPreference='Stop'
function ParseJson([string]$path,[string]$code) {
  if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {throw "${code}_MISSING: $path"}
  try {$parsed=(Get-Content -LiteralPath $path -Raw -ErrorAction Stop|ConvertFrom-Json -ErrorAction Stop)}
  catch {throw "${code}_CORRUPT: $path"}
  if ($null -eq $parsed) {throw "${code}_CORRUPT: $path"}
  return $parsed
}
function Hash([string]$path) {return (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash}
function SameFile([string]$a,[string]$b) {
  return (Test-Path -LiteralPath $b -PathType Leaf) -and ((Hash $a) -ceq (Hash $b))
}
function SamePath([string]$a,[string]$b) {
  return [IO.Path]::GetFullPath($a).TrimEnd('\') -ieq [IO.Path]::GetFullPath($b).TrimEnd('\')
}
function WriteText([string]$path,[string]$text) {
  [IO.File]::WriteAllText($path,$text,[Text.UTF8Encoding]::new($false))
}
function WritePin([string]$path,[string]$sourceBackup) {
  $temp=Join-Path (Split-Path -Parent $path) ('pin-'+[guid]::NewGuid().ToString('N')+'.tmp')
  try {
    Copy-Item -LiteralPath $sourceBackup -Destination $temp -ErrorAction Stop
    if (Test-Path -LiteralPath $path -PathType Leaf) {[IO.File]::Replace($temp,$path,[NullString]::Value)}
    else {Move-Item -LiteralPath $temp -Destination $path}
  } finally {
    if (Test-Path -LiteralPath $temp) {Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue}
  }
}
# Task access goes through one backend so tests can run against a sandbox. The Files backend is refused for the real root.
function TaskState([string]$name) {
  if ($TaskBackend -eq 'Files') {
    $f=Join-Path $sandbox ($name+'.state')
    if (-not (Test-Path -LiteralPath $f -PathType Leaf)) {throw "TASK_MISSING: $name"}
    return (Get-Content -LiteralPath $f -Raw).Trim()
  }
  return [string](Get-ScheduledTask -TaskName $name -ErrorAction Stop).State
}
function TaskXml([string]$name) {
  if ($TaskBackend -eq 'Files') {return [IO.File]::ReadAllText((Join-Path $sandbox ($name+'.xml')))}
  return [string](Export-ScheduledTask -TaskName $name -ErrorAction Stop)
}
function SetTaskXml([string]$name,[string]$xml) {
  if ($TaskBackend -eq 'Files') {WriteText (Join-Path $sandbox ($name+'.xml')) $xml;return}
  Register-ScheduledTask -TaskName $name -Xml $xml -Force -ErrorAction Stop|Out-Null
}
function TaskActions([string]$xml) {
  $doc=[xml]$xml
  $ns=New-Object Xml.XmlNamespaceManager($doc.NameTable)
  $ns.AddNamespace('t','http://schemas.microsoft.com/windows/2004/02/mit/task')
  return @($doc.SelectNodes('//t:Actions/t:Exec',$ns)|ForEach-Object {
    ([string]$_.SelectSingleNode('t:Command',$ns).InnerText)+'|'+([string]$_.SelectSingleNode('t:Arguments',$ns).InnerText)
  })
}
function RequireDisabled([string]$name) {
  if ((TaskState $name) -ne 'Disabled') {throw "TASK_NOT_DISABLED: $name"}
}
function RequireOwnerHeld {
  if ((Get-IronGodOwnerStatus $state (Join-Path $release 'src')) -ceq 'AUTHORIZED') {
    throw 'OWNER_MUST_KEEP_CONTROL_DURING_RESTORE'
  }
}
function ValidateCheckout([string]$repo,[string]$sha,[string]$tree) {
  if (-not (Test-Path -LiteralPath (Join-Path $repo '.git'))) {throw "REPOSITORY_UNVERIFIED: $repo"}
  $head=(& git -C $repo rev-parse HEAD)
  if ($LASTEXITCODE -ne 0 -or $head.Trim() -cne $sha) {throw "HEAD_MISMATCH: $repo"}
  $actual=(& git -C $repo rev-parse 'HEAD^{tree}')
  if ($LASTEXITCODE -ne 0 -or $actual.Trim() -cne $tree) {throw "TREE_MISMATCH: $repo"}
  $dirty=@(& git -C $repo status --porcelain --untracked-files=normal)
  if ($LASTEXITCODE -ne 0 -or $dirty.Count) {throw "CHECKOUT_DIRTY: $repo"}
}

if ($ExpectedSha -cnotmatch '^[0-9a-f]{40}$' -or -not [IO.Path]::IsPathRooted($Base)) {
  throw 'EXACT_RESTORE_SHA_AND_ABSOLUTE_BASE_REQUIRED'
}
$state=Join-Path $Base 'flow-state'
$sandbox=Join-Path $Base 'task-sandbox'
if (($TaskBackend -eq 'Files' -or $InjectFailure) -and $env:LOCALAPPDATA -and
    (SamePath $Base (Join-Path $env:LOCALAPPDATA 'ExponentialEngine'))) {
  throw 'TEST_BACKEND_REFUSED_FOR_REAL_ENGINE_ROOT'
}
if ($InjectFailure -and $TaskBackend -ne 'Files') {throw 'FAILURE_INJECTION_REQUIRES_FILES_BACKEND'}
if (-not $BackupDir) {
  $newest=@(Get-ChildItem -LiteralPath $state -Directory -Filter 'pinned-release-promotion-*' -ErrorAction Stop|
    Sort-Object LastWriteTimeUtc -Descending)
  if (-not $newest.Count) {throw 'BACKUP_NOT_FOUND'}
  $BackupDir=$newest[0].FullName
}
if (-not (Test-Path -LiteralPath $BackupDir -PathType Container)) {throw "BACKUP_NOT_FOUND: $BackupDir"}

# --- Validate the backup (read-only) ---
$pinFile=Join-Path $state 'supervisor-pinned-release.json'
$actorPinFile=Join-Path $state 'actor-local-promotion.json'
$pinBackup=Join-Path $BackupDir 'supervisor-pinned-release.before.json'
$actorBackup=Join-Path $BackupDir 'actor-local-promotion.before.json'
$beforePin=ParseJson $pinBackup 'BACKUP_PIN'
$beforeActor=ParseJson $actorBackup 'BACKUP_ACTOR_PIN'
$promotion=ParseJson (Join-Path $BackupDir 'promotion-result.json') 'BACKUP_PROMOTION_RESULT'
if ([string]$promotion.old_sha -cne $ExpectedSha) {throw 'BACKUP_OLD_SHA_MISMATCH'}
if ([string]$promotion.accepted_sha -cnotmatch '^[0-9a-f]{40}$' -or [string]$promotion.accepted_sha -ceq $ExpectedSha) {
  throw 'BACKUP_PROMOTION_RESULT_INVALID'
}
foreach($p in @($beforePin,$beforeActor)) {
  if ([string]$p.accepted_sha -cne $ExpectedSha) {throw 'BACKUP_PIN_SHA_MISMATCH'}
}
$names=@('ExponentialEngine-ContinuousFlow-Watchdog','ExponentialEngine-LaptopActor')
$taskBackups=@{}
foreach($n in $names) {
  $f=Join-Path $BackupDir ($n+'.xml')
  if (-not (Test-Path -LiteralPath $f -PathType Leaf)) {throw "BACKUP_TASK_XML_MISSING: $n"}
  $xml=[IO.File]::ReadAllText($f)
  try {$null=[xml]$xml} catch {throw "BACKUP_TASK_XML_CORRUPT: $n"}
  if ((TaskActions $xml).Count -ne 1) {throw "BACKUP_TASK_ACTION_INVALID: $n"}
  # A backup that would re-enable a worker is not a safe restore source.
  if ($xml -notmatch '(?i)<Enabled>\s*false\s*</Enabled>') {throw "BACKUP_TASK_NOT_DISABLED: $n"}
  if ($xml.IndexOf($ExpectedSha,[StringComparison]::OrdinalIgnoreCase) -lt 0) {throw "BACKUP_TASK_NOT_PINNED_TO_EXPECTED_RELEASE: $n"}
  $taskBackups[$n]=$xml
}
$desktopBackup=Join-Path $BackupDir 'desktop-controls'
if (-not (Test-Path -LiteralPath $desktopBackup -PathType Container)) {throw 'BACKUP_DESKTOP_CONTROLS_MISSING'}
$desktopFiles=@(Get-ChildItem -LiteralPath $desktopBackup -File)
if (-not $desktopFiles.Count) {throw 'BACKUP_DESKTOP_CONTROLS_EMPTY'}
# The backup must belong to the install that is current now (or to a restore that was interrupted part-way).
$current=ParseJson $pinFile 'CURRENT_PIN'
$currentActor=ParseJson $actorPinFile 'CURRENT_ACTOR_PIN'
foreach($p in @($current,$currentActor)) {
  $sha=[string]$p.accepted_sha
  if ($sha -cne [string]$promotion.accepted_sha -and $sha -cne $ExpectedSha) {throw 'BACKUP_DOES_NOT_MATCH_CURRENT_INSTALL'}
}
# The release being restored must still be intact on disk.
$release=Join-Path (Join-Path $Base 'releases') $ExpectedSha
$actor=Join-Path (Join-Path $Base 'runtime-actors') $ExpectedSha
$receipt=ParseJson (Join-Path (Join-Path $Base 'release-receipts') ($ExpectedSha+'.json')) 'RESTORE_RELEASE_RECEIPT'
if ([string]$receipt.accepted_sha -cne $ExpectedSha) {throw 'RESTORE_RELEASE_RECEIPT_MISMATCH'}
ValidateCheckout $release $ExpectedSha ([string]$receipt.tree)
ValidateCheckout $actor $ExpectedSha ([string]$receipt.tree)
$bridge=Join-Path $release 'scripts\desktop-control\IronGodOwnerControl.ps1'
if (-not (Test-Path -LiteralPath $bridge -PathType Leaf)) {throw 'RESTORE_RELEASE_LACKS_OWNER_CONTROLLER'}
. $bridge
RequireOwnerHeld
foreach($n in $names) {RequireDisabled $n}
$running=@(Get-CimInstance Win32_Process -ErrorAction Stop|Where-Object {
  $_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -match 'continuous_flow_supervisor|laptop_actor_bootstrap' -and
  # A sandbox root only owns processes started from it; the real engine root is checked machine-wide.
  ($TaskBackend -eq 'Scheduler' -or $_.CommandLine.IndexOf($Base,[StringComparison]::OrdinalIgnoreCase) -ge 0)
})
if ($running.Count) {throw 'ENGINE_PROCESSES_RUNNING'}

$plan=[ordered]@{status='PINNED_RELEASE_RESTORE_PLAN';mode=$(if($Apply){'APPLY'}else{'DRY_RUN'});
  restore_sha=$ExpectedSha;replaces_sha=[string]$promotion.accepted_sha;backup=$BackupDir;
  restores=@('supervisor-pinned-release.json','actor-local-promotion.json')+@($names|ForEach-Object {'task:'+$_})+
           @($desktopFiles|ForEach-Object {'desktop:'+$_.Name});
  owner_control_unchanged=$true;tasks_remain_disabled=$true;mutations_performed=0}
if (-not $Apply) {$plan|ConvertTo-Json -Depth 5;exit 0}

# --- Apply: snapshot first so a failed restore can itself be undone ---
$snap=Join-Path $state ('pre-restore-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $snap -ErrorAction Stop|Out-Null
$snapDesktop=Join-Path $snap 'desktop-controls'
New-Item -ItemType Directory -Path $snapDesktop -ErrorAction Stop|Out-Null
Copy-Item -LiteralPath $pinFile -Destination (Join-Path $snap 'supervisor-pinned-release.json') -ErrorAction Stop
Copy-Item -LiteralPath $actorPinFile -Destination (Join-Path $snap 'actor-local-promotion.json') -ErrorAction Stop
foreach($n in $names) {WriteText (Join-Path $snap ($n+'.xml')) (TaskXml $n)}
foreach($f in $desktopFiles) {
  $installed=Join-Path $Base $f.Name
  if (Test-Path -LiteralPath $installed -PathType Leaf) {Copy-Item -LiteralPath $installed -Destination (Join-Path $snapDesktop $f.Name) -ErrorAction Stop}
}
WriteText (Join-Path $snap 'restore-in-progress.json') (([ordered]@{restore_sha=$ExpectedSha;backup=$BackupDir;
  started_at_utc=[DateTimeOffset]::UtcNow.ToString('o')})|ConvertTo-Json)
try {
  RequireOwnerHeld
  WritePin $pinFile $pinBackup
  WritePin $actorPinFile $actorBackup
  if ($InjectFailure -eq 'AfterPins') {throw 'INJECTED_FAILURE_AFTER_PINS'}
  foreach($n in $names) {SetTaskXml $n $taskBackups[$n]}
  if ($InjectFailure -eq 'AfterTasks') {throw 'INJECTED_FAILURE_AFTER_TASKS'}
  foreach($f in $desktopFiles) {Copy-Item -LiteralPath $f.FullName -Destination (Join-Path $Base $f.Name) -Force -ErrorAction Stop}
  if ($InjectFailure -eq 'AfterDesktop') {throw 'INJECTED_FAILURE_AFTER_DESKTOP'}
  # Verify each restored record independently of the copy that wrote it.
  if (-not (SameFile $pinBackup $pinFile) -or -not (SameFile $actorBackup $actorPinFile)) {throw 'RESTORED_PIN_UNVERIFIED'}
  foreach($n in $names) {
    RequireDisabled $n
    if ((TaskActions (TaskXml $n)) -join ';' -cne ((TaskActions $taskBackups[$n]) -join ';')) {throw "RESTORED_TASK_ACTION_UNVERIFIED: $n"}
  }
  foreach($f in $desktopFiles) {
    if (-not (SameFile $f.FullName (Join-Path $Base $f.Name))) {throw "RESTORED_DESKTOP_CONTROL_UNVERIFIED: $($f.Name)"}
  }
  RequireOwnerHeld
  $readiness='NOT_CHECKED_IN_SANDBOX'
  if ($TaskBackend -eq 'Scheduler') {
    $check=@(& powershell.exe -NoProfile -NonInteractive -File (Join-Path $Base 'Check-Development-ResumeReady.ps1') 2>&1)
    if ($LASTEXITCODE -ne 0) {throw ('RESTORED_RUNTIME_READINESS_FAILED: '+($check|Out-String))}
    $ready=($check|Out-String|ConvertFrom-Json)
    if ($ready.ready -cne $true -or $ready.status -cne 'RESUME_READY') {throw 'RESTORED_RUNTIME_NOT_READY'}
    $readiness=[string]$ready.status
  }
  # The success record is written last: its absence always means the restore did not complete.
  $result=[ordered]@{status='PINNED_RELEASE_RESTORED_TASKS_DISABLED';accepted_sha=$ExpectedSha;
    replaced_sha=[string]$promotion.accepted_sha;backup=$BackupDir;snapshot=$snap;
    owner_control_unchanged=$true;workers_started=$false;tasks_enabled=$false;readiness=$readiness;
    completed_at_utc=[DateTimeOffset]::UtcNow.ToString('o')}
  WriteText (Join-Path $snap 'restore-result.json') ($result|ConvertTo-Json -Depth 5)
  $result|ConvertTo-Json -Depth 5
} catch {
  $errorText=$_.Exception.Message
  $failures=New-Object 'System.Collections.Generic.List[string]'
  try {WritePin $pinFile (Join-Path $snap 'supervisor-pinned-release.json')} catch {$failures.Add('SUPERVISOR_PIN: '+$_.Exception.Message)}
  try {WritePin $actorPinFile (Join-Path $snap 'actor-local-promotion.json')} catch {$failures.Add('ACTOR_PIN: '+$_.Exception.Message)}
  foreach($n in $names) {
    try {SetTaskXml $n ([IO.File]::ReadAllText((Join-Path $snap ($n+'.xml'))));RequireDisabled $n}
    catch {$failures.Add('TASK '+$n+': '+$_.Exception.Message)}
  }
  foreach($f in @(Get-ChildItem -LiteralPath $snapDesktop -File)) {
    try {Copy-Item -LiteralPath $f.FullName -Destination (Join-Path $Base $f.Name) -Force -ErrorAction Stop}
    catch {$failures.Add('DESKTOP '+$f.Name+': '+$_.Exception.Message)}
  }
  WriteText (Join-Path $snap 'restore-failed.json') (([ordered]@{error=$errorText;revert_errors=@($failures)})|ConvertTo-Json)
  throw ('PINNED_RELEASE_RESTORE_FAILED_REVERTED: '+$errorText+'; REVERT_ERRORS='+($failures -join '; ')+'; SNAPSHOT='+$snap)
}
