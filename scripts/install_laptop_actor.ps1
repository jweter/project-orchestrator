param(
  [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
  [string]$WorkspaceRoot = "$env:USERPROFILE\Projects",
  [string]$QueueRoot = "$env:LOCALAPPDATA\ExponentialEngine\queue"
)
$ErrorActionPreference = "Stop"
$Python = (Get-Command python).Source
$Runner = Join-Path $RepoRoot "scripts\laptop_actor_runner.py"
New-Item -ItemType Directory -Force -Path (Join-Path $QueueRoot "inbox") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $QueueRoot "outbox") | Out-Null
$Action = New-ScheduledTaskAction -Execute $Python -Argument "`"$Runner`" --queue `"$QueueRoot`" --workspace-root `"$WorkspaceRoot`" --poll-seconds 60" -WorkingDirectory $RepoRoot
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable
Register-ScheduledTask -TaskName "ExponentialEngine-LaptopActor" -Action $Action -Trigger $Trigger -Settings $Settings -Description "Persistent bounded local worker for Exponential Engine product packets" -Force | Out-Null
Start-ScheduledTask -TaskName "ExponentialEngine-LaptopActor"
Write-Host "LAPTOP ACTOR INSTALLED AND RUNNING" -ForegroundColor Green
Write-Host "Queue: $QueueRoot"
