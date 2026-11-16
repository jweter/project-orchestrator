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
$destination = Join-Path $Base 'tools\VerifiedReleaseUpdater\IronGod-Daily-Verified-Update.ps1'
if ($At -cnotmatch '^([01][0-9]|2[0-3]):[0-5][0-9]$') { throw 'INVALID_LOCAL_UPDATE_TIME' }
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
  Write-Output 'DRY_RUN: does not register the task, install a release, disable workers, or change owner control'
  exit 0
}
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
  $matchesTarget = (@($existing.Actions).Count -eq 1 -and
    [string]$existing.Actions[0].Arguments -like ('*'+[IO.Path]::GetFileName($destination)+'*'))
  $matchesDaily = @($existing.Triggers | Where-Object { $_.CimClass.CimClassName -eq 'MSFT_TaskDailyTrigger' }).Count -eq 1
  if (-not $matchesTarget -or -not $matchesDaily) {
    throw 'EXISTING_DAILY_TASK_DIFFERS_MANUAL_RECONCILIATION_REQUIRED'
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
$timestamp = [DateTime]::Today.Add([TimeSpan]::ParseExact($At, 'hh\:mm', [Globalization.CultureInfo]::InvariantCulture))
$trigger = New-ScheduledTaskTrigger -Daily -At $timestamp
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument (
  '-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $destination + '"'
)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'Daily read-only GitHub main check; guarded install ONLY under existing owner-held quiescence; never restart workers' -ErrorAction Stop | Out-Null
$verify = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
Write-Output "REGISTERED: $taskName"
Write-Output "SCHEDULE_LOCAL: daily $At"
Write-Output "ACTION: $($verify.Actions[0].Execute) $($verify.Actions[0].Arguments)"
Write-Output 'SAFETY: no owner grant, no task restarts, existing updater independently verifies all gates'
