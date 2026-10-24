# Repoint only the two ALREADY-DISABLED Iron God tasks to an independently
# verified pinned local release. Default is read-only. No task enable/start,
# owner-control state modification, remote bridge change, or worker dispatch.
param(
  [string]$StateRoot = "$env:LOCALAPPDATA\ExponentialEngine\flow-state",
  [switch]$Apply
)
$ErrorActionPreference = 'Stop'

function Read-JsonStrict([string]$path) {
  if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "MISSING_REQUIRED_RECORD: $path" }
  return (Get-Content -LiteralPath $path -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop)
}
function Ensure-OwnerHeld {
  $owner = Read-JsonStrict (Join-Path $StateRoot 'human-mode.json')
  if ($owner.enabled -isnot [bool] -or $owner.enabled -cne $true) {
    throw 'OWNER_CONTROL_NOT_HELD_REFUSING_TASK_REPAIR'
  }
}
function Same-Path([string]$a,[string]$b) {
  if (-not [IO.Path]::IsPathRooted($a) -or -not [IO.Path]::IsPathRooted($b)) { return $false }
  return ([IO.Path]::GetFullPath($a).TrimEnd('\') -ieq [IO.Path]::GetFullPath($b).TrimEnd('\'))
}
function Get-InstalledScript([string]$arguments) {
  $m=[regex]::Match($arguments, '(?i)(?:^|\s)-File\s+"([^"]+)"')
  if (-not $m.Success) { return '' }
  return $m.Groups[1].Value
}
function Get-TaskStrict([string]$name) {
  $task = Get-ScheduledTask -TaskName $name -ErrorAction Stop
  if ($task.State -ne 'Disabled') { throw "TASK_NOT_DISABLED: $name" }
  if (@($task.Actions).Count -ne 1) { throw "TASK_ACTION_COUNT_UNEXPECTED: $name" }
  if ($task.Actions[0].Execute -notmatch '(?i)powershell') { throw "TASK_EXECUTABLE_UNEXPECTED: $name" }
  return $task
}

Ensure-OwnerHeld
$pin=Read-JsonStrict (Join-Path $StateRoot 'supervisor-pinned-release.json')
$actorPin=Read-JsonStrict (Join-Path $StateRoot 'actor-local-promotion.json')
if ($pin.schema_version -ne 1 -or $pin.enabled -cne $true -or $actorPin.enabled -cne $true) {
  throw 'PINNED_RELEASE_NOT_ENABLED'
}
$repo=[string]$pin.accepted_repo
$sha=[string]$pin.accepted_sha
if ($sha -cnotmatch '^[0-9a-f]{40}$' -or -not [IO.Path]::IsPathRooted($repo)) {
  throw 'PINNED_RELEASE_INVALID'
}
if ($actorPin.accepted_sha -cne $sha -or -not (Same-Path $repo ([string]$actorPin.accepted_repo))) {
  throw 'SUPERVISOR_ACTOR_RELEASE_MISMATCH'
}
$receipt=Read-JsonStrict ([string]$pin.receipt_path)
if ($receipt.schema_version -ne 1 -or $receipt.status -cne 'VERIFIED_STAGED' -or
    $receipt.accepted_sha -cne $sha -or $receipt.verification.exact_head -cne $sha -or
    [string]$receipt.verification.exit_code -cne '0' -or
    [string]$receipt.verification.output_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
    -not (Same-Path $repo ([string]$receipt.release_repo))) {
  throw 'VERIFIED_RELEASE_RECEIPT_INVALID'
}
$gitHead=(& git -C $repo rev-parse HEAD).Trim()
$gitTree=(& git -C $repo rev-parse 'HEAD^{tree}').Trim()
$dirty=@(& git -C $repo status --porcelain --untracked-files=normal)
if ($LASTEXITCODE -ne 0 -or $gitHead -cne $sha -or $gitTree -cne [string]$receipt.tree -or $dirty.Count -gt 0) {
  throw 'PINNED_RELEASE_HEAD_TREE_OR_CLEANLINESS_MISMATCH'
}
$actorPath=[string]$actorPin.actor_repo
if (-not [IO.Path]::IsPathRooted($actorPath) -or
    -not (Test-Path -LiteralPath (Join-Path $actorPath '.git')) -or
    (Same-Path $actorPath $repo)) { throw 'PINNED_ACTOR_PATH_INVALID' }
$actualActorHead=(& git -C $actorPath rev-parse HEAD).Trim()
$actorDirty=@(& git -C $actorPath status --porcelain --untracked-files=normal)
if ($LASTEXITCODE -ne 0 -or $actualActorHead -cne $sha -or $actorDirty.Count -gt 0) {
  throw 'PINNED_ACTOR_HEAD_OR_CLEANLINESS_MISMATCH'
}
$live=@(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
  $_.Name -match '^pythonw?\.exe$' -and
  $_.CommandLine -match 'continuous_flow_supervisor|laptop_actor_bootstrap'
})
if ($live.Count -gt 0) { throw 'ENGINE_PROCESSES_RUNNING_REFUSING_TASK_REPAIR' }

$specs=@(
  [pscustomobject]@{
    Name='ExponentialEngine-ContinuousFlow-Watchdog'
    Script=(Join-Path $repo 'scripts\watch_pinned_continuous_flow.ps1')
    Args="-NoProfile -NonInteractive -WindowStyle Hidden -File `"$(Join-Path $repo 'scripts\watch_pinned_continuous_flow.ps1')`" -StateRoot `"$StateRoot`" -PinPath `"$(Join-Path $StateRoot 'supervisor-pinned-release.json')`""
  },
  [pscustomobject]@{
    Name='ExponentialEngine-LaptopActor'
    Script=(Join-Path $repo 'scripts\launch_local_actor.ps1')
    Args="-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$(Join-Path $repo 'scripts\launch_local_actor.ps1')`""
  }
)
$changes=@()
foreach($spec in $specs) {
  if (-not (Test-Path -LiteralPath $spec.Script -PathType Leaf)) {
    throw "PINNED_LAUNCHER_MISSING: $($spec.Script)"
  }
  $task=Get-TaskStrict $spec.Name
  $current=$task.Actions[0]
  $aligned=(Same-Path (Get-InstalledScript ([string]$current.Arguments)) $spec.Script) -and
      (Same-Path ([string]$current.WorkingDirectory) $repo)
  $changes+= [pscustomobject]@{
    name=$spec.Name
    aligned=$aligned
    old_script=Get-InstalledScript ([string]$current.Arguments)
    target_script=$spec.Script
    execute=$current.Execute
    new_arguments=$spec.Args
    old_arguments=[string]$current.Arguments
    old_working_directory=[string]$current.WorkingDirectory
  }
}
if (-not $Apply) {
  [pscustomobject]@{status='AUDIT_ONLY';target_sha=$sha;task_count=$changes.Count;
    drift_count=@($changes|Where-Object{-not $_.aligned}).Count;tasks=$changes;
    owner_control_unchanged=$true;tasks_unchanged=$true} | ConvertTo-Json -Depth 6
  exit 0
}
if (@($changes|Where-Object{-not $_.aligned}).Count -eq 0) {
  [pscustomobject]@{status='ALREADY_ALIGNED_NO_MUTATION';target_sha=$sha} | ConvertTo-Json
  exit 0
}
Ensure-OwnerHeld
# These snapshots are created BEFORE any task change; never overwrite backups.
$backupDir=Join-Path $StateRoot ('task-action-pin-repair-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $backupDir -ErrorAction Stop | Out-Null
foreach($spec in $specs) {
  [IO.File]::WriteAllText(
    (Join-Path $backupDir ($spec.Name + '.xml')),
    (Export-ScheduledTask -TaskName $spec.Name -ErrorAction Stop),
    [Text.UTF8Encoding]::new($false)
  )
}
$changedNames=New-Object 'System.Collections.Generic.List[string]'
try {
  foreach($spec in $specs) {
    $change=@($changes|Where-Object{$_.name -eq $spec.Name})[0]
    if ($change.aligned) { continue }
    Ensure-OwnerHeld
    $task=Get-TaskStrict $spec.Name
    $action=New-ScheduledTaskAction -Execute $change.execute -Argument $spec.Args -WorkingDirectory $repo
    Set-ScheduledTask -TaskName $spec.Name -Action $action -ErrorAction Stop | Out-Null
    $changedNames.Add($spec.Name)
    $updated=Get-TaskStrict $spec.Name
    if (-not (Same-Path (Get-InstalledScript ([string]$updated.Actions[0].Arguments)) $spec.Script) -or
        -not (Same-Path ([string]$updated.Actions[0].WorkingDirectory) $repo)) {
      throw "TASK_ACTION_VERIFICATION_FAILED: $($spec.Name)"
    }
  }
  Ensure-OwnerHeld
  $report=[ordered]@{schema_version=1;status='REPOINTED_DISABLED_NOT_STARTED';
    checked_at_utc=[DateTimeOffset]::UtcNow.ToString('o');accepted_sha=$sha;
    snapshot_directory=$backupDir;task_names=@($changedNames);tasks=$changes;
    owner_control_unchanged=$true;tasks_remain_disabled=$true}
  [IO.File]::WriteAllText((Join-Path $backupDir 'result.json'),
    ($report|ConvertTo-Json -Depth 7),[Text.UTF8Encoding]::new($false))
  $report|ConvertTo-Json -Depth 7
} catch {
  $failure=$_
  $restoreErrors=New-Object 'System.Collections.Generic.List[string]'
  foreach($name in $changedNames) {
    try {
      Register-ScheduledTask -TaskName $name -Xml ([IO.File]::ReadAllText(
        (Join-Path $backupDir ($name + '.xml')))) -Force -ErrorAction Stop | Out-Null
      $restored=Get-TaskStrict $name
      if ($null -eq $restored) { throw 'No restored task' }
    } catch { $restoreErrors.Add("$name : $($_.Exception.Message)") }
  }
  throw "TASK_REPAIR_FAILED: $failure; ROLLBACK_ERRORS: $($restoreErrors -join '; '); BACKUPS: $backupDir"
}
