param(
  [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
  [string]$WorkspaceRoot = "$env:LOCALAPPDATA\ExponentialEngine\workspaces",
  [string]$QueueRoot = "$env:LOCALAPPDATA\ExponentialEngine\queue",
  [string]$ActorRoot = "$env:LOCALAPPDATA\ExponentialEngine\actor-repo"
)
$ErrorActionPreference = "Stop"
$Python = (Get-Command python).Source
$Git = (Get-Command git).Source
$Origin = (& $Git -C $RepoRoot remote get-url origin).Trim()
if (-not $Origin) { throw "Could not resolve project-orchestrator origin." }

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ActorRoot) | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $QueueRoot "inbox") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $QueueRoot "outbox") | Out-Null
New-Item -ItemType Directory -Force -Path $WorkspaceRoot | Out-Null

if (-not (Test-Path (Join-Path $ActorRoot ".git"))) {
  & $Git clone --branch main --single-branch $Origin $ActorRoot
  if ($LASTEXITCODE -ne 0) { throw "Could not create dedicated laptop actor checkout." }
} else {
  & $Git -C $ActorRoot fetch origin main
  if ($LASTEXITCODE -ne 0) { throw "Could not refresh dedicated laptop actor checkout." }
  & $Git -C $ActorRoot reset --hard origin/main
  if ($LASTEXITCODE -ne 0) { throw "Could not reset dedicated laptop actor checkout." }
}

$Bootstrap = Join-Path $ActorRoot "scripts\laptop_actor_bootstrap.py"
if (-not (Test-Path $Bootstrap)) { throw "Dedicated actor checkout does not contain laptop_actor_bootstrap.py." }

$Arguments = "`"$Bootstrap`" --actor-repo `"$ActorRoot`" --queue `"$QueueRoot`" --workspace-root `"$WorkspaceRoot`" --min-idle-seconds 600"
$Action = New-ScheduledTaskAction -Execute $Python -Argument $Arguments -WorkingDirectory $ActorRoot
$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
$Settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable
Register-ScheduledTask -TaskName "ExponentialEngine-LaptopActor" -Action $Action -Trigger $Trigger -Settings $Settings -Description "Self-updating bounded idle laptop actor for Exponential Engine product packets" -Force | Out-Null
Start-ScheduledTask -TaskName "ExponentialEngine-LaptopActor"

Write-Host "LAPTOP ACTOR INSTALLED, SELF-UPDATING, AND RUNNING" -ForegroundColor Green
Write-Host "Dedicated actor checkout: $ActorRoot"
Write-Host "Managed product workspaces: $WorkspaceRoot"
Write-Host "Queue: $QueueRoot"
Write-Host "Polling: every 5 minutes; product execution requires 10 minutes of Windows idle time"
if (Get-Command gh -ErrorAction SilentlyContinue) {
  Write-Host "GitHub bridge: enabled when existing gh authentication is valid"
} else {
  Write-Host "GitHub bridge: local-only until GitHub CLI is available"
}
