param(
  [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
  [string]$WorkspaceRoot = "$env:LOCALAPPDATA\ExponentialEngine\workspaces",
  [string]$StateRoot = "$env:LOCALAPPDATA\ExponentialEngine\flow-state"
)
$ErrorActionPreference = "Stop"
$Python = (Get-Command python).Source
$MergeLoop = Join-Path $RepoRoot "scripts\green_pr_watch_loop.py"
$Builder = Join-Path $RepoRoot "scripts\autonomous_slice_builder.py"
if (-not (Test-Path $MergeLoop)) { throw "green_pr_watch_loop.py missing" }
if (-not (Test-Path $Builder)) { throw "autonomous_slice_builder.py missing" }
New-Item -ItemType Directory -Force -Path $StateRoot | Out-Null
New-Item -ItemType Directory -Force -Path $WorkspaceRoot | Out-Null

$MergeArgs = '"' + $MergeLoop + '" --state "' + $StateRoot + '\green-pr-watcher.json" --poll-seconds 60'
$MergeAction = New-ScheduledTaskAction -Execute $Python -Argument $MergeArgs -WorkingDirectory $RepoRoot
$MergeTrigger = New-ScheduledTaskTrigger -AtLogOn
$MergeSettings = New-ScheduledTaskSettingsSet -RestartCount 20 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable
Register-ScheduledTask -TaskName "ExponentialEngine-GreenPRMerger" -Action $MergeAction -Trigger $MergeTrigger -Settings $MergeSettings -Description "Always-on fail-closed green PR merger for the portfolio" -Force | Out-Null

$BuilderArgs = '"' + $Builder + '" --workspace-root "' + $WorkspaceRoot + '" --state "' + $StateRoot + '\builder.json"'
$BuilderAction = New-ScheduledTaskAction -Execute $Python -Argument $BuilderArgs -WorkingDirectory $RepoRoot
$BuilderTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
$BuilderSettings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 2) -StartWhenAvailable
Register-ScheduledTask -TaskName "ExponentialEngine-AutonomousBuilder" -Action $BuilderAction -Trigger $BuilderTrigger -Settings $BuilderSettings -Description "Idle-gated local Ollama/Aider bounded-slice builder" -Force | Out-Null

Start-ScheduledTask -TaskName "ExponentialEngine-GreenPRMerger"
Start-ScheduledTask -TaskName "ExponentialEngine-AutonomousBuilder"

Write-Host "CONTINUOUS LAPTOP FLOW INSTALLED" -ForegroundColor Green
Write-Host "Green PR merger: always on, 60-second poll"
Write-Host "Autonomous builder: every 5 minutes; script exits while user is active"
Write-Host "Builder model: $env:EXPONENTIAL_BUILDER_MODEL (defaults to ollama/qwen2.5-coder:7b)"
Write-Host "State root: $StateRoot"
