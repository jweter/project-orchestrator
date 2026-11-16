# IRON CONTINUUM #747. Owner-requested ONCE-DAILY local Windows task.
# Registers a single unprivileged scheduled checker; does not change owner mode,
# trigger an install during registration, or enable/disable Iron God tasks.
[CmdletBinding()]
param(
  [string]$Base = (Join-Path $env:LOCALAPPDATA 'ExponentialEngine'),
  [string]$Source = '',
  [string]$At = '02:20',
  [switch]$Plan
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($Source)) {
  $Source = Join-Path $PSScriptRoot 'IronGod-Daily-Verified-Update.ps1'
}
$taskName = 'IronGod-Daily-Verified-Update'
$Base = [IO.Path]::GetFullPath($Base)
$destination = Join-Path $Base 'tools\VerifiedReleaseUpdater\IronGod-Daily-Verified-Update.ps1'
# The scheduled checker must inspect the SAME installation passed to this
# installer; implicit %LOCALAPPDATA% would silently target another engine.
$taskArguments = ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "' +
  $destination + '" -Base "' + $Base + '"')
if ($At -cnotmatch '^([01][0-9]|2[0-3]):[0-5][0-9]$') { throw 'INVALID_LOCAL_UPDATE_TIME' }
$timestamp = [DateTime]::Today.Add([TimeSpan]::ParseExact($At, 'hh\:mm', [Globalization.CultureInfo]::InvariantCulture))
if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) { throw 'UPDATE_CHECK_SOURCE_MISSING' }
if (-not (Test-Path -LiteralPath (Join-Path $Base 'tools\VerifiedReleaseUpdater\IronGod-Update.ps1') -PathType Leaf)) {
  throw 'EXISTING_VERIFIED_INSTALLER_MISSING'
}
$errors = $null; $tokens = $null
[void][System.Management.Automation.Language.Parser]::ParseFile($Source, [ref]$tokens, [ref]$errors)
if ($errors.Count -gt 0) { throw 'UPDATE_CHECK_SOURCE_POWERSHELL_SYNTAX_INVALID' }

if ($Plan) {
  Write-Output "DRY_RUN_TASK: $taskName"
  Write-Output "DRY_RUN_SCHEDULE_LOCAL: daily $At"
  Write-Output "DRY_RUN_CHECKER: $destination"
  Write-Output "DRY_RUN_ACTION_ARGUMENTS: $taskArguments"
  Write-Output 'DRY_RUN: does not register the task, install a release, disable workers, or change owner control'
  exit 0
}
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
  # The already-installed task from v1 omits -Base but uses the same
  # default ExponentialEngine root. Treat that exact legacy action as
  # equivalent *only* for the default installation; never accept it for a
  # caller-selected non-default installation.
  $defaultBase = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'ExponentialEngine'))
  $legacyArgs = ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "' +
    $destination + '"')
  $actualArgs = [string]$existing.Actions[0].Arguments
  $matchesTarget = (@($existing.Actions).Count -eq 1 -and
    [string]$existing.Actions[0].Execute -ieq 'powershell.exe' -and (
    $actualArgs -ceq $taskArguments -or
    ($Base -ieq $defaultBase -and $actualArgs -ceq $legacyArgs)))
  $triggers = @($existing.Triggers)
  $matchesDaily = $false
  if ($triggers.Count -eq 1) {
    $daily = $triggers[0]
    $matchesDaily = (
      $daily.CimClass.CimClassName -eq 'MSFT_TaskDailyTrigger' -and
      $daily.Enabled -eq $true -and
      [int]$daily.DaysInterval -eq 1 -and
      [datetime]::Parse($daily.StartBoundary).TimeOfDay -eq $timestamp.TimeOfDay
    )
  }
  if (-not $matchesTarget -or -not $matchesDaily) {
    throw 'EXISTING_DAILY_TASK_DIFFERS_MANUAL_RECONCILIATION_REQUIRED'
  }
  if (-not (Test-Path -LiteralPath $destination -PathType Leaf)) {
    throw 'INSTALLED_DAILY_CHECK_MISSING_MANUAL_RECONCILIATION_REQUIRED'
  }
  if ((Get-FileHash -LiteralPath $Source -Algorithm SHA256).Hash -cne
      (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash) {
    throw 'INSTALLED_DAILY_CHECK_DIFFERS_MANUAL_RECONCILIATION_REQUIRED'
  }
  Write-Output "ALREADY_REGISTERED: $taskName"
  exit 0
}

# Never replace a separately edited updater helper without reconciling it.
if (Test-Path -LiteralPath $destination -PathType Leaf) {
  $left=(Get-FileHash -LiteralPath $Source -Algorithm SHA256).Hash
  $right=(Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash
  if ($left -cne $right) { throw 'INSTALLED_DAILY_CHECK_DIFFERS_MANUAL_RECONCILIATION_REQUIRED' }
} else {
  New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
  Copy-Item -LiteralPath $Source -Destination $destination -ErrorAction Stop
}
$trigger = New-ScheduledTaskTrigger -Daily -At $timestamp
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $taskArguments
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'Daily read-only GitHub main check; guarded install ONLY under existing owner-held quiescence; never restart workers' -ErrorAction Stop | Out-Null
$verify = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
Write-Output "REGISTERED: $taskName"
Write-Output "SCHEDULE_LOCAL: daily $At"
Write-Output "ACTION: $($verify.Actions[0].Execute) $($verify.Actions[0].Arguments)"
Write-Output 'SAFETY: no owner grant, no task restarts, existing updater independently verifies all gates'
