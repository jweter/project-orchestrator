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
try {
  $pinned = Get-Content (Join-Path $StateRoot "supervisor-pinned-release.json") -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
  . (Join-Path ([string]$pinned.accepted_repo) "scripts\desktop-control\IronGodOwnerControl.ps1")
  $null = Invoke-IronGodOwnerController -StateRoot $StateRoot -Arguments @("issue","--action","GIVE_ME_CONTROL")
} catch {}

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

Write-Host "HUMAN MODE ACTIVE - autonomous development stopped." -ForegroundColor Green
