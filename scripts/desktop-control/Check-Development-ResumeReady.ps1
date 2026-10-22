# Read-only readiness proof for the desktop "Take the Wheel" control.
# It NEVER enables a task, releases owner control, or starts a process.
param([switch]$Quiet)
$ErrorActionPreference = 'Stop'
$state = Join-Path $env:LOCALAPPDATA 'ExponentialEngine\flow-state'
$issues = New-Object 'System.Collections.Generic.List[string]'
function Issue([string]$why) { $script:issues.Add($why) }
function Read-Record([string]$name) {
  $path = Join-Path $state $name
  if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
    Issue ("MISSING_" + $name); return $null
  }
  try { return (Get-Content -LiteralPath $path -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop) }
  catch { Issue ("INVALID_" + $name); return $null }
}
$owner = Read-Record 'human-mode.json'
if ($null -ne $owner -and $owner.enabled -isnot [bool]) { Issue 'OWNER_MARKER_NOT_BOOLEAN' }

$watchdog = $null
$actor = $null
try { $watchdog = Get-ScheduledTask -TaskName 'ExponentialEngine-ContinuousFlow-Watchdog' -ErrorAction Stop }
catch { Issue 'WATCHDOG_TASK_MISSING' }
try { $actor = Get-ScheduledTask -TaskName 'ExponentialEngine-LaptopActor' -ErrorAction Stop }
catch { Issue 'ACTOR_TASK_MISSING' }
if ($null -ne $watchdog -and
    @($watchdog.Actions | Where-Object { $_.Execute -match 'powershell' -and $_.Arguments -like '*watch_pinned_continuous_flow.ps1*' }).Count -ne 1) {
  Issue 'WATCHDOG_NOT_PINNED_TO_VERIFIED_RELEASE'
}
if ($null -ne $actor -and
    @($actor.Actions | Where-Object { $_.Execute -match 'powershell' -and $_.Arguments -like '*launch_local_actor.ps1*' }).Count -ne 1) {
  Issue 'ACTOR_NOT_PINNED_TO_VERIFIED_RELEASE'
}
# A second Windows Startup path must not reload the former actor checkout.
$startup = Join-Path ([Environment]::GetFolderPath('Startup')) 'ExponentialEngine-ContinuousFlow.cmd'
if (Test-Path -LiteralPath $startup -PathType Leaf) {
  try {
    $legacy = Get-Content -LiteralPath $startup -Raw -ErrorAction Stop
    if ($legacy -match 'actor-repo|continuous_flow_supervisor.py' -or
        $legacy -notmatch 'watch_pinned_continuous_flow.ps1') {
      Issue 'LEGACY_STARTUP_STILL_TARGETS_OLD_ACTOR'
    }
  } catch { Issue 'STARTUP_LAUNCHER_UNREADABLE' }
}
$pin = Read-Record 'supervisor-pinned-release.json'
$actorPin = Read-Record 'actor-local-promotion.json'
if ($null -ne $pin -and $null -ne $actorPin) {
  if ($pin.schema_version -ne 1 -or $pin.enabled -cne $true) { Issue 'WATCHDOG_PIN_NOT_ENABLED' }
  if ($actorPin.enabled -cne $true) { Issue 'ACTOR_PIN_NOT_ENABLED' }
  if ($pin.accepted_sha -cne $actorPin.accepted_sha -or $pin.accepted_repo -ine $actorPin.accepted_repo) {
    Issue 'SUPERVISOR_AND_ACTOR_PINS_DISAGREE'
  }
}
if ($null -ne $pin) {
  $sha = [string]$pin.accepted_sha
  $repo = [string]$pin.accepted_repo
  $receiptPath = [string]$pin.receipt_path
  if ($sha -cnotmatch '^[a-f0-9]{40}$') { Issue 'PIN_SHA_INVALID' }
  if (-not [IO.Path]::IsPathRooted($repo) -or -not [IO.Path]::IsPathRooted($receiptPath)) {
    Issue 'PIN_PATH_INVALID'
  } elseif (-not (Test-Path -LiteralPath $repo -PathType Container)) {
    Issue 'PINNED_REPOSITORY_MISSING'
  } elseif (-not (Test-Path -LiteralPath $receiptPath -PathType Leaf)) {
    Issue 'PINNED_RECEIPT_MISSING'
  } else {
    try {
      $receipt = Get-Content -LiteralPath $receiptPath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
      if ($receipt.schema_version -ne 1 -or $receipt.status -cne 'VERIFIED_STAGED' -or
          $receipt.accepted_sha -cne $sha -or $receipt.verification.exact_head -cne $sha -or
          [string]$receipt.verification.exit_code -cne '0' -or
          [string]$receipt.verification.output_sha256 -cnotmatch '^[a-f0-9]{64}$') {
        Issue 'RELEASE_RECEIPT_UNVERIFIED'
      }
      if ([IO.Path]::GetFullPath($repo).TrimEnd('\') -ine
          [IO.Path]::GetFullPath([string]$receipt.release_repo).TrimEnd('\')) {
        Issue 'RECEIPT_SOURCE_MISMATCH'
      }
      $actual = (& git -C $repo rev-parse HEAD)
      if ($LASTEXITCODE -ne 0 -or $actual.Trim() -cne $sha) { Issue 'RELEASE_HEAD_MISMATCH' }
      $tree = (& git -C $repo rev-parse 'HEAD^{tree}')
      if ($LASTEXITCODE -ne 0 -or $tree.Trim() -cne [string]$receipt.tree) { Issue 'RELEASE_TREE_MISMATCH' }
      $dirty = & git -C $repo status --porcelain --untracked-files=normal
      if ($LASTEXITCODE -ne 0 -or $dirty) { Issue 'RELEASE_NOT_CLEAN' }
    } catch { Issue 'RELEASE_VERIFICATION_FAILED' }
  }
}
$result = [ordered]@{
  schema_version = 1
  ready = ($issues.Count -eq 0)
  status = $(if ($issues.Count -eq 0) { 'RESUME_READY' } else { 'RESUME_BLOCKED' })
  issues = @($issues)
  owner_control_unchanged = $true
  tasks_unchanged = $true
}
if (-not $Quiet) { $result | ConvertTo-Json -Depth 4 }
if ($issues.Count) { exit 5 }
exit 0
