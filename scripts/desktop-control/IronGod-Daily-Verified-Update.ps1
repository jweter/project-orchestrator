# IRON CONTINUUM #747: one daily check for a newer verified main release.
# This task never revokes owner authority, stops workers, edits worktrees,
# replaces pins or starts the engine. A guarded install is allowed ONLY if
# Give Me Control has already established OWNER_CONTROL_ACTIVE and the
# existing owner-held verified updater independently rechecks every gate.
[CmdletBinding()]
param(
  [string]$Base = (Join-Path $env:LOCALAPPDATA 'ExponentialEngine'),
  [switch]$CheckOnly,
  [switch]$SelfTest
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repository = 'jweter/project-orchestrator'
$branch = 'main'
$shaPattern = '^[0-9a-f]{40}$'
$state = Join-Path $Base 'flow-state'
$reportFile = Join-Path $state 'iron-god-self-update\daily-status.json'
$updater = Join-Path $Base 'tools\VerifiedReleaseUpdater\IronGod-Update.ps1'
$tasks = @('ExponentialEngine-ContinuousFlow-Watchdog', 'ExponentialEngine-LaptopActor')

function Decide-IronGodDailyUpdate(
  [string]$installed,
  [string]$latest,
  [bool]$ownerHeld,
  [bool]$tasksDisabled,
  [bool]$noEngineProcesses
) {
  if ($latest -ceq $installed) { return 'CURRENT' }
  if (-not $ownerHeld) { return 'UPDATE_PENDING_OWNER_CONTROL' }
  if (-not $tasksDisabled -or -not $noEngineProcesses) { return 'UPDATE_PENDING_QUIESCENCE' }
  return 'VERIFIED_INSTALL_ELIGIBLE'
}

function Record-IronGodDailyStatus(
  [string]$result, [string]$detail, [string]$installed,
  [string]$latest, [string]$nowInstalled
) {
  $record = [ordered]@{
    schema_version = 1
    observed_at_utc = [DateTime]::UtcNow.ToString('o')
    repository = $repository
    branch = $branch
    status = $result
    detail = $detail
    installed_before = $installed
    latest_main_observed = $latest
    installed_after = $nowInstalled
    advisory_only = $true
    owner_grant_created = $false
    workers_stopped = $false
    task_triggered_engine_start = $false
  }
  $json = $record | ConvertTo-Json -Depth 5
  $directory = Split-Path -Parent $reportFile
  New-Item -ItemType Directory -Path $directory -Force -ErrorAction Stop | Out-Null
  $scratch = $reportFile + '.' + [guid]::NewGuid().ToString('N') + '.tmp'
  try {
    [IO.File]::WriteAllText($scratch, $json + [Environment]::NewLine, (New-Object System.Text.UTF8Encoding $false))
    if ([IO.File]::Exists($reportFile)) {
      [IO.File]::Replace($scratch, $reportFile, $null)
    } else {
      [IO.File]::Move($scratch, $reportFile)
    }
  } finally {
    if ([IO.File]::Exists($scratch)) { [IO.File]::Delete($scratch) }
  }
  Write-Output $json
}

if ($SelfTest) {
  $same = 'a' * 40
  $newer = 'b' * 40
  $cases = @(
    @($same, $same, $false, $false, $false, 'CURRENT'),
    @($same, $newer, $false, $false, $false, 'UPDATE_PENDING_OWNER_CONTROL'),
    @($same, $newer, $true, $false, $true, 'UPDATE_PENDING_QUIESCENCE'),
    @($same, $newer, $true, $true, $false, 'UPDATE_PENDING_QUIESCENCE'),
    @($same, $newer, $true, $true, $true, 'VERIFIED_INSTALL_ELIGIBLE')
  )
  foreach ($case in $cases) {
    $observed = Decide-IronGodDailyUpdate $case[0] $case[1] $case[2] $case[3] $case[4]
    if ($observed -cne $case[5]) { throw "DAILY_UPDATE_SELFTEST_FAILED: $observed vs $($case[5])" }
  }
  Write-Output 'DAILY_UPDATE_SELFTEST_PASSED: 5 scenarios'
  exit 0
}

$installed = $null
$latest = $null
try {
  $pinFile = Join-Path $state 'supervisor-pinned-release.json'
  if (-not (Test-Path -LiteralPath $pinFile -PathType Leaf)) { throw 'INSTALLED_RELEASE_PIN_MISSING' }
  $pin = Get-Content -LiteralPath $pinFile -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
  $installed = [string]$pin.accepted_sha
  if ($installed -cnotmatch $shaPattern) { throw 'INSTALLED_RELEASE_SHA_INVALID' }
  if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'GIT_UNAVAILABLE' }

  # Pinned immutable upstream; never run git pull/reset in a developer worktree.
  $ref = @(& git -c http.lowSpeedLimit=1024 -c http.lowSpeedTime=15 ls-remote --exit-code 'https://github.com/jweter/project-orchestrator.git' 'refs/heads/main')
  if ($LASTEXITCODE -ne 0 -or $ref.Count -ne 1) { throw 'GITHUB_MAIN_QUERY_FAILED' }
  if ($ref[0] -cnotmatch '^([0-9a-f]{40})\s+refs/heads/main$') { throw 'GITHUB_MAIN_REF_INVALID' }
  $latest = [string]$Matches[1]
  if ($latest -cnotmatch $shaPattern) { throw 'GITHUB_MAIN_SHA_INVALID' }

  if ($latest -ceq $installed) {
    Record-IronGodDailyStatus 'CURRENT' 'Pinned release matches GitHub main' $installed $latest $installed
    exit 0
  }
  $allDisabled = $true
  foreach ($name in $tasks) {
    $task = Get-ScheduledTask -TaskName $name -ErrorAction Stop
    if ($task.State -ne 'Disabled') { $allDisabled = $false }
  }
  $quiet = $false
  if ($allDisabled) {
    $active = @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
      $_.Name -match '^(pythonw?|powershell|pwsh)\.exe$' -and $_.CommandLine -and
      $_.CommandLine -match '(continuous_flow_supervisor|laptop_actor_bootstrap|iron_god_.*_author\.py|iron_god_.*_worker\.py|watch_pinned_continuous_flow)'
    })
    $quiet = ($active.Count -eq 0)
  }
  $ownerHeld = $false
  if ($allDisabled -and $quiet) {
    $release = [string]$pin.accepted_repo
    $controller = Join-Path $release 'scripts\desktop-control\IronGodOwnerControl.ps1'
    if ((Test-Path -LiteralPath $controller -PathType Leaf) -and (Test-Path -LiteralPath (Join-Path $release 'src') -PathType Container)) {
      . $controller
      $ownerHeld = (Get-IronGodOwnerStatus $state (Join-Path $release 'src')) -ceq 'OWNER_CONTROL_ACTIVE'
    }
  }
  $choice = Decide-IronGodDailyUpdate $installed $latest $ownerHeld $allDisabled $quiet
  if ($choice -ne 'VERIFIED_INSTALL_ELIGIBLE') {
    Record-IronGodDailyStatus $choice 'New merged main detected; no engine tasks or worker processes were modified' $installed $latest $installed
    exit 0
  }
  if ($CheckOnly) {
    Record-IronGodDailyStatus 'UPDATE_READY_DRY_RUN' 'Installer eligible, but this check never installs' $installed $latest $installed
    exit 0
  }
  if (-not (Test-Path -LiteralPath $updater -PathType Leaf)) { throw 'GUARDED_DESKTOP_UPDATER_MISSING' }
  # This is the same owner-operated script as the desktop icon: it independently
  # verifies owner control, disabled tasks, process quiescence, exact main,
  # merged PR provenance, CI, FULL preflight and guarded release promotion.
  & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $updater -Base $Base -Repository $repository -Branch $branch
  if ($LASTEXITCODE -ne 0) { throw "GUARDED_INSTALL_FAILED_CODE_$LASTEXITCODE" }
  $newPin = Get-Content -LiteralPath $pinFile -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
  $after = [string]$newPin.accepted_sha
  if ($after -cnotmatch $shaPattern -or $after -ceq $installed) { throw 'INSTALLED_PIN_NOT_ADVANCED' }
  Record-IronGodDailyStatus 'UPDATED_DISABLED_OWNER_RESUME_REQUIRED' 'Verified updater installed merged main; owner must click Take the Wheel' $installed $latest $after
  exit 0
} catch {
  $message = ($_.Exception.Message -replace '[\r\n]', ' ')
  if ($message.Length -gt 250) { $message = $message.Substring(0,250) }
  Record-IronGodDailyStatus 'CHECK_OR_UPDATE_FAILED' $message $installed $latest $installed
  exit 1
}
