# Update EXISTING disabled pinned Iron God tasks/JSON to a verified staged
# local exact-head release. Keep owner in control. Backup/rollback all changes.
# This never enables/starts workers or changes the owner's control marker.
param(
  [Parameter(Mandatory=$true)][string]$NewSha,
  [string]$Base="$env:LOCALAPPDATA\ExponentialEngine",
  [switch]$Apply
)
$ErrorActionPreference='Stop'
function ParseJson([string]$path) {
  if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {throw "MISSING_REQUIRED_RECORD: $path"}
  return (Get-Content -LiteralPath $path -Raw -ErrorAction Stop|ConvertFrom-Json -ErrorAction Stop)
}
function EnsureHeld {
  $owner=ParseJson (Join-Path $state 'human-mode.json')
  if ($owner.enabled -isnot [bool] -or $owner.enabled -cne $true) {
    throw 'OWNER_MUST_KEEP_CONTROL_DURING_PIN_PROMOTION'
  }
}
function RequireTaskDisabled([string]$name) {
  $t=Get-ScheduledTask -TaskName $name -ErrorAction Stop
  if ($t.State -ne 'Disabled') {throw "TASK_NOT_DISABLED: $name"}
  if (@($t.Actions).Count -ne 1) {throw "TASK_ACTION_COUNT_INVALID: $name"}
  return $t
}
function GetHead([string]$repo) {
  $result=(& git -C $repo rev-parse HEAD)
  if($LASTEXITCODE -ne 0){throw "GIT_HEAD_UNVERIFIED: $repo"}
  return $result.Trim()
}
function ValidateCheckout([string]$repo,[string]$sha,[string]$tree) {
  if (-not (Test-Path -LiteralPath (Join-Path $repo '.git'))) {throw "REPOSITORY_UNVERIFIED: $repo"}
  if ((GetHead $repo) -cne $sha) {throw "HEAD_MISMATCH: $repo"}
  $actualTree=(& git -C $repo rev-parse 'HEAD^{tree}')
  if ($LASTEXITCODE -ne 0 -or $actualTree.Trim() -cne $tree) {throw "TREE_MISMATCH: $repo"}
  $dirty=@(& git -C $repo status --porcelain --untracked-files=normal)
  if ($LASTEXITCODE -ne 0 -or $dirty.Count) {throw "CHECKOUT_DIRTY: $repo"}
}
function WritePin([string]$path,$data) {
  $temp=Join-Path (Split-Path -Parent $path) ('pin-'+[guid]::NewGuid().ToString('N')+'.tmp')
  try {
    [IO.File]::WriteAllText($temp,($data|ConvertTo-Json -Depth 6),[Text.UTF8Encoding]::new($false))
    [IO.File]::Replace($temp,$path,[NullString]::Value)
  } finally {
    if (Test-Path -LiteralPath $temp) {Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue}
  }
}
if ($NewSha -cnotmatch '^[0-9a-f]{40}$' -or -not [IO.Path]::IsPathRooted($Base)) {
  throw 'EXACT_NEW_RELEASE_SHA_AND_ABSOLUTE_BASE_REQUIRED'
}
$state=Join-Path $Base 'flow-state'
$pinFile=Join-Path $state 'supervisor-pinned-release.json'
$actorPinFile=Join-Path $state 'actor-local-promotion.json'
$pin=ParseJson $pinFile
$actorPin=ParseJson $actorPinFile
EnsureHeld
$names=@('ExponentialEngine-ContinuousFlow-Watchdog','ExponentialEngine-LaptopActor')
foreach($n in $names) { $null=RequireTaskDisabled $n }
if ($pin.accepted_sha -cne $actorPin.accepted_sha -or $pin.accepted_repo -ine $actorPin.accepted_repo) {
  throw 'OLD_RUNTIME_PINS_ALREADY_INCONSISTENT'
}
$running=@(Get-CimInstance Win32_Process -ErrorAction Stop|Where-Object {
  $_.Name -match '^pythonw?\.exe$' -and
  $_.CommandLine -match 'continuous_flow_supervisor|laptop_actor_bootstrap'
})
if ($running.Count) {throw 'ENGINE_PROCESSES_RUNNING'}
$oldSha=[string]$pin.accepted_sha
if ($oldSha -eq $NewSha) {throw 'TARGET_ALREADY_PINNED_RECONCILE_INSTEAD'}
$release=Join-Path (Join-Path $Base 'releases') $NewSha
$actor=Join-Path (Join-Path $Base 'runtime-actors') $NewSha
$receiptFile=Join-Path (Join-Path $Base 'release-receipts') ($NewSha+'.json')
$receipt=ParseJson $receiptFile
if ($receipt.schema_version -ne 1 -or $receipt.status -cne 'VERIFIED_STAGED' -or
    $receipt.accepted_sha -cne $NewSha -or $receipt.verification.exact_head -cne $NewSha -or
    [string]$receipt.verification.exit_code -cne '0' -or
    [string]$receipt.verification.output_sha256 -cnotmatch '^[0-9a-f]{64}$') {
  throw 'NEW_RELEASE_RECEIPT_UNVERIFIED'
}
if ([IO.Path]::GetFullPath([string]$receipt.release_repo).TrimEnd('\') -ine [IO.Path]::GetFullPath($release).TrimEnd('\')) {
  throw 'NEW_RELEASE_RECEIPT_WRONG_CHECKOUT'
}
ValidateCheckout $release $NewSha ([string]$receipt.tree)
ValidateCheckout $actor $NewSha ([string]$receipt.tree)
& git -C $release merge-base --is-ancestor $oldSha $NewSha 2>$null
if($LASTEXITCODE -ne 0){throw 'NEW_RELEASE_IS_NOT_DESCENDANT_OF_CURRENT_PIN'}
$repair=Join-Path $release 'scripts\desktop-control\Repair-DisabledPinnedTasks.ps1'
if (-not (Test-Path -LiteralPath $repair -PathType Leaf)) {
  throw 'VERIFIED_RELEASE_MISSING_TASK_REPAIR_TOOL'
}
$plan=[ordered]@{status='DISABLED_LOCAL_RELEASE_PROMOTION_PLAN';
  old_sha=$oldSha;new_sha=$NewSha;release=$release;actor=$actor;
  tasks=$names;owner_control_unchanged=$true;tasks_remain_disabled=$true}
if (-not $Apply) {$plan|ConvertTo-Json -Depth 5;exit 0}
$backup=Join-Path $state ('pinned-release-promotion-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $backup -ErrorAction Stop|Out-Null
$pinBackup=Join-Path $backup 'supervisor-pinned-release.before.json'
$actorBackup=Join-Path $backup 'actor-local-promotion.before.json'
Copy-Item -LiteralPath $pinFile -Destination $pinBackup -ErrorAction Stop
Copy-Item -LiteralPath $actorPinFile -Destination $actorBackup -ErrorAction Stop
foreach($n in $names){
  [IO.File]::WriteAllText((Join-Path $backup ($n+'.xml')),
    (Export-ScheduledTask -TaskName $n -ErrorAction Stop),[Text.UTF8Encoding]::new($false))
}
try {
  EnsureHeld
  $newPin=[ordered]@{schema_version=1;enabled=$true;accepted_repo=$release;
    accepted_sha=$NewSha;receipt_path=$receiptFile}
  $newActorPin=[ordered]@{enabled=$true;accepted_repo=$release;
    accepted_sha=$NewSha;actor_repo=$actor}
  WritePin $pinFile $newPin
  WritePin $actorPinFile $newActorPin
  EnsureHeld
  $out=@(& powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $repair -StateRoot $state -Apply 2>&1)
  if ($LASTEXITCODE -ne 0) {throw ('DISABLED_TASK_REPAIR_FAILED: '+($out|Out-String))}
  foreach($n in $names){$null=RequireTaskDisabled $n}
  $ownerNow=ParseJson (Join-Path $state 'human-mode.json')
  if($ownerNow.enabled -cne $true){throw 'OWNER_CONTROL_CHANGED_DURING_CUTOVER'}
  $readiness=Join-Path $Base 'Check-Development-ResumeReady.ps1'
  $check=@(& powershell.exe -NoProfile -NonInteractive -File $readiness 2>&1)
  if ($LASTEXITCODE -ne 0) {throw ('NEW_RELEASE_DESKTOP_READINESS_FAILED: '+($check|Out-String))}
  $ready=($check|Out-String|ConvertFrom-Json)
  if ($ready.ready -cne $true -or $ready.status -cne 'RESUME_READY') {
    throw 'NEW_PINNED_RUNTIME_NOT_READY'
  }
  $result=[ordered]@{status='NEW_LOCAL_RELEASE_PINNED_TASKS_DISABLED';
    accepted_sha=$NewSha;old_sha=$oldSha;backup=$backup;
    owner_control_unchanged=$true;workers_started=$false;
    tasks_enabled=$false;desktop_readiness=$ready.status;
    completed_at_utc=[DateTimeOffset]::UtcNow.ToString('o')}
  [IO.File]::WriteAllText((Join-Path $backup 'promotion-result.json'),
    ($result|ConvertTo-Json -Depth 5),[Text.UTF8Encoding]::new($false))
  $result|ConvertTo-Json -Depth 5
} catch {
  $errorText=$_.Exception.Message
  $failures=New-Object 'System.Collections.Generic.List[string]'
  foreach($n in $names) {
    try {
      Register-ScheduledTask -TaskName $n -Xml ([IO.File]::ReadAllText(
        (Join-Path $backup ($n+'.xml')))) -Force -ErrorAction Stop|Out-Null
      $null=RequireTaskDisabled $n
    } catch {$failures.Add(('TASK '+$n+': '+$_.Exception.Message))}
  }
  try {Copy-Item -LiteralPath $pinBackup -Destination $pinFile -Force -ErrorAction Stop}
  catch {$failures.Add('SUPERVISOR_PIN: '+$_.Exception.Message)}
  try {Copy-Item -LiteralPath $actorBackup -Destination $actorPinFile -Force -ErrorAction Stop}
  catch {$failures.Add('ACTOR_PIN: '+$_.Exception.Message)}
  throw ('LOCAL_PIN_PROMOTION_ROLLED_BACK: '+$errorText+
         '; ROLLBACK_ERRORS='+($failures -join '; ')+
         '; BACKUPS='+$backup)
}
