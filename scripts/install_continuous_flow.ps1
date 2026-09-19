param(
  [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
  [string]$WorkspaceRoot = "$env:LOCALAPPDATA\ExponentialEngine\workspaces",
  [string]$StateRoot = "$env:LOCALAPPDATA\ExponentialEngine\flow-state"
)
$ErrorActionPreference = "Stop"
$Python = (Get-Command python).Source
$Supervisor = Join-Path $RepoRoot "scripts\continuous_flow_supervisor.py"
if (-not (Test-Path $Supervisor)) { throw "continuous_flow_supervisor.py missing" }
New-Item -ItemType Directory -Force -Path $StateRoot,$WorkspaceRoot | Out-Null
$Startup = [Environment]::GetFolderPath("Startup")
$Launcher = Join-Path $Startup "ExponentialEngine-ContinuousFlow.cmd"
$Log = Join-Path $StateRoot "startup.log"
$Line = '@echo off' + [Environment]::NewLine +
  'start "" /min "' + $Python + '" "' + $Supervisor + '" --repo-root "' + $RepoRoot +
  '" --workspace-root "' + $WorkspaceRoot + '" --state-root "' + $StateRoot +
  '" >> "' + $Log + '" 2>&1'
Set-Content -Path $Launcher -Value $Line -Encoding ASCII
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*continuous_flow_supervisor.py*" -and $_.ProcessId -ne $PID } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
$ArgsLine = '"' + $Supervisor + '" --repo-root "' + $RepoRoot + '" --workspace-root "' + $WorkspaceRoot + '" --state-root "' + $StateRoot + '"'
Start-Process -FilePath $Python -ArgumentList $ArgsLine -WorkingDirectory $RepoRoot -WindowStyle Hidden
Write-Host "CONTINUOUS LAPTOP FLOW INSTALLED (CURRENT USER)" -ForegroundColor Green
Write-Host "Startup launcher: $Launcher"
Write-Host "Green PR merger: 60-second poll"
Write-Host "Autonomous builder: 5-minute loop; heavy work remains idle-gated"
Write-Host "State root: $StateRoot"
