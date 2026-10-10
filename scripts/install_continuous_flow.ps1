param(
  [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
  [string]$WorkspaceRoot = "$env:LOCALAPPDATA\ExponentialEngine\workspaces",
  [string]$StateRoot = "$env:LOCALAPPDATA\ExponentialEngine\flow-state"
)
$ErrorActionPreference = "Stop"
$Python = (Get-Command python).Source
$Supervisor = Join-Path $RepoRoot "scripts\continuous_flow_supervisor.py"
$OwnerGate = Join-Path $RepoRoot "scripts\owner_control_gate.py"
if (-not (Test-Path $Supervisor)) { throw "continuous_flow_supervisor.py missing" }
if (-not (Test-Path $OwnerGate)) { throw "owner_control_gate.py missing" }
New-Item -ItemType Directory -Force -Path $StateRoot,$WorkspaceRoot | Out-Null
$Startup = [Environment]::GetFolderPath("Startup")
$Launcher = Join-Path $Startup "ExponentialEngine-ContinuousFlow.cmd"
$Log = Join-Path $StateRoot "startup.log"
$HumanMode = Join-Path $StateRoot "human-mode.json"
$Line = '@echo off' + [Environment]::NewLine +
  '"' + $Python + '" "' + $OwnerGate + '" --state-root "' + $StateRoot + '"' + [Environment]::NewLine +
  'if errorlevel 1 exit /b 0' + [Environment]::NewLine +
  'start "" /min "' + $Python + '" "' + $Supervisor + '" --repo-root "' + $RepoRoot +
  '" --workspace-root "' + $WorkspaceRoot + '" --state-root "' + $StateRoot +
  '" >> "' + $Log + '" 2>&1'
Set-Content -Path $Launcher -Value $Line -Encoding ASCII
# Never terminate a productive supervisor merely to reinstall startup hooks.
$Existing = @(Get-CimInstance Win32_Process | Where-Object {
  $_.CommandLine -like "*continuous_flow_supervisor.py*" -and $_.ProcessId -ne $PID
})
& $Python $OwnerGate --state-root $StateRoot
if ($LASTEXITCODE -eq 0 -and $Existing.Count -eq 0) {
  Start-Process -FilePath $Python -ArgumentList @(
    $Supervisor,"--repo-root",$RepoRoot,"--workspace-root",$WorkspaceRoot,"--state-root",$StateRoot
  ) -WorkingDirectory $RepoRoot -WindowStyle Hidden
} elseif ($LASTEXITCODE -ne 0) {
  Write-Host "OWNER CONTROL ACTIVE OR UNVERIFIED: autonomous flow not started." -ForegroundColor Yellow
} else {
  Write-Host "Existing supervisor preserved; no duplicate launched." -ForegroundColor Yellow
}
Write-Host "CONTINUOUS LAPTOP FLOW INSTALLED (CURRENT USER)" -ForegroundColor Green
Write-Host "Startup launcher: $Launcher"
Write-Host "Green PR merger: 60-second poll"
Write-Host "Autonomous builder: 5-minute loop; heavy work remains idle-gated"
Write-Host "State root: $StateRoot"
