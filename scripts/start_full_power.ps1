param(
  [string]$StateRoot = "$env:LOCALAPPDATA\ExponentialEngine\flow-state"
)
$ErrorActionPreference = "Stop"
# Authority comes only from the authoritative owner controller. A remote or
# dashboard request can never grant it: only the desktop Take the Wheel icon
# records a grant. This script may only (re)start the engine for a grant the
# owner already made and a live supervisor still holds or is about to ack.
$ownerReleased = $false
try {
  $pinned = Get-Content (Join-Path $StateRoot "supervisor-pinned-release.json") -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
  . (Join-Path ([string]$pinned.accepted_repo) "scripts\desktop-control\IronGodOwnerControl.ps1")
  $ownerReleased = ((Get-IronGodOwnerStatus $StateRoot) -ceq "AUTHORIZED")
} catch {
  $ownerReleased = $false
}
if(-not $ownerReleased){
  throw "OWNER_CONTROL_ACTIVE_OR_UNVERIFIED_USE_TAKE_THE_WHEEL"
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
    Enable-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue | Out-Null
    Start-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
  }
}

$watcher = Join-Path $env:LOCALAPPDATA "ExponentialEngine\Watch-ContinuousFlow.ps1"
if(Test-Path $watcher){
  Start-Process powershell.exe -WindowStyle Hidden -ArgumentList @(
    "-NoProfile","-ExecutionPolicy","Bypass","-WindowStyle","Hidden","-File",('"' + $watcher + '"')
  )
}
Write-Host "FULL POWER ACTIVE - autonomous development started." -ForegroundColor Green
