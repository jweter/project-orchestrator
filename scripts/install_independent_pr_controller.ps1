# Registers an independent, lightweight PR controller; does not alter ChatGPT schedules.
param(
  [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
  [string]$StateRoot = "$env:LOCALAPPDATA\ExponentialEngine\flow-state"
)
$ErrorActionPreference = 'Stop'
$marker = Join-Path $StateRoot 'human-mode.json'
# Registration is deferred unless TAKE THE WHEEL is already active.
if (-not (Test-Path -LiteralPath $marker)) { throw 'OWNER_CONTROL_UNVERIFIED' }
$control = Get-Content -LiteralPath $marker -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
if ($control.enabled -cne $false) { throw 'OWNER_CONTROL_ACTIVE_OR_UNVERIFIED' }
$script = Join-Path $RepoRoot 'scripts\independent_pr_controller.py'
if (-not (Test-Path -LiteralPath $script)) { throw 'PR controller script missing' }
$consolePython = (Get-Command python.exe -ErrorAction Stop).Source
$python = Join-Path (Split-Path -Parent $consolePython) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'PYTHONW_REQUIRED_TO_AVOID_GAME_FOCUS_STEALING' }
$arguments = ('"{0}" --repo-root "{1}" --state-root "{2}"' -f $script, $RepoRoot, $StateRoot)
$action = New-ScheduledTaskAction -Execute $python -Argument $arguments -WorkingDirectory $RepoRoot
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) -RepetitionInterval (New-TimeSpan -Minutes 2)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType InteractiveToken -RunLevel Limited
Register-ScheduledTask -TaskName 'Azathoth-Independent-PR-Controller' -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Write-Output 'REGISTERED: Azathoth-Independent-PR-Controller (2-minute cadence)'
