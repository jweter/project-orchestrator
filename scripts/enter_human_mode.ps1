param(
  [string]$StateRoot = "$env:LOCALAPPDATA\ExponentialEngine\flow-state"
)
$ErrorActionPreference = "SilentlyContinue"
New-Item -ItemType Directory -Force -Path $StateRoot | Out-Null
$humanMode = Join-Path $StateRoot "human-mode.json"

@{
  enabled = $true
  since = [DateTimeOffset]::UtcNow.ToString("o")
  source = "desktop-human-control"
} | ConvertTo-Json -Depth 4 | Set-Content -Encoding UTF8 $humanMode
# The file above is only a stop signal for unmigrated SYSTEM guardians; the
# authoritative revocation is recorded in the owner controller.
# Success is reported only when the controller confirms OWNER_CONTROL_ACTIVE (the same test as the
# desktop Give Me Control); the stops below run regardless, because stopping work is the safe direction.
$controllerLoaded = $false
$revocation = "OWNER_CONTROLLER_UNAVAILABLE"
try {
  $pinned = Get-Content (Join-Path $StateRoot "supervisor-pinned-release.json") -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
  . (Join-Path ([string]$pinned.accepted_repo) "scripts\desktop-control\IronGodOwnerControl.ps1")
  $controllerLoaded = $true
  $issued = Invoke-IronGodOwnerController -StateRoot $StateRoot -Arguments @("issue","--action","GIVE_ME_CONTROL")
  $revocation = if ($issued.ExitCode -eq 0) { "ISSUED" } else { [string]$issued.Result.reason }
} catch {
  $revocation = "OWNER_CONTROLLER_ERROR: " + $_.Exception.Message
}

$tasks = @(
  "ExponentialEngine-ContinuousFlow-Watchdog",
  "ExponentialEngine-LaptopActor",
  "Portfolio Idle Worker",
  "Rocksmith CDLC Unattended Worker",
  "Everward Unattended Product Reality",
  "Azathoth-Idle-App-Watcher",
  "Azathoth-Idle-Mine-Watcher",
  "Datadog AI Usage Agent"
)
foreach($name in $tasks){
  $task=Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
  if($task){
    Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    Disable-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue | Out-Null
  }
}

Get-CimInstance Win32_Process | Where-Object {
  $_.Name -like "python*" -and (
    $_.CommandLine -like "*continuous_flow_supervisor.py*" -or
    $_.CommandLine -like "*green_pr_watch_loop.py*" -or
    $_.CommandLine -like "*autonomous_slice_builder.py*" -or
    $_.CommandLine -like "*laptop_actor_bootstrap.py*"
  )
} | ForEach-Object {
  Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}

$ownerActive = $false
if ($controllerLoaded) {
  try { $ownerActive = (Get-IronGodOwnerStatus $StateRoot) -ceq "OWNER_CONTROL_ACTIVE" } catch { $ownerActive = $false }
}
if (-not $ownerActive) {
  Write-Host "HUMAN MODE NOT CONFIRMED - workers were stopped, but the owner controller did not record Give Me Control ($revocation). Use the desktop Give Me Control icon." -ForegroundColor Red
  exit 3
}
Write-Host "HUMAN MODE ACTIVE - autonomous development stopped." -ForegroundColor Green
exit 0
