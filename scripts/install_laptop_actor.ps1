param(
  [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
  [string]$WorkspaceRoot = "$env:USERPROFILE\Projects",
  [string]$QueueRoot = "$env:LOCALAPPDATA\ExponentialEngine\queue"
)
$ErrorActionPreference = "Stop"
$Python = (Get-Command python).Source
$Runner = Join-Path $RepoRoot "scripts\laptop_actor_runner.py"
$Bridge = Join-Path $RepoRoot "scripts\laptop_actor_github_bridge.py"
New-Item -ItemType Directory -Force -Path (Join-Path $QueueRoot "inbox") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $QueueRoot "outbox") | Out-Null
$Action = New-ScheduledTaskAction -Execute $Python -Argument "`"$Bridge`" --queue `"$QueueRoot`" --workspace-root `"$WorkspaceRoot`" --runner `"$Runner`" --poll-seconds 60" -WorkingDirectory $RepoRoot
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable
Register-ScheduledTask -TaskName "ExponentialEngine-LaptopActor" -Action $Action -Trigger $Trigger -Settings $Settings -Description "Persistent bounded local worker with GitHub-backed Exponential Engine product packet transport" -Force | Out-Null
Start-ScheduledTask -TaskName "ExponentialEngine-LaptopActor"
Write-Host "LAPTOP ACTOR INSTALLED AND RUNNING" -ForegroundColor Green
Write-Host "Queue: $QueueRoot"
if (Get-Command gh -ErrorAction SilentlyContinue) {
  Write-Host "GitHub bridge: enabled when existing gh authentication is valid"
} else {
  Write-Host "GitHub bridge: local-only until GitHub CLI is available"
}
