param(
  [string]$StateRoot = "$env:LOCALAPPDATA\ExponentialEngine\flow-state"
)
$ErrorActionPreference = "Stop"
$humanMode = Join-Path $StateRoot "human-mode.json"
Remove-Item -Force $humanMode -ErrorAction SilentlyContinue

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
