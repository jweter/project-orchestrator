param(
 [switch]$AuditOnly,
 [switch]$KeepRemote,
 [switch]$SelfTest
)
# A single authoritative Give-Me-Control transaction.
# All guard entrypoints consume the same persisted owner marker.
$ErrorActionPreference = 'Continue'
if($SelfTest){$AuditOnly=$true;$KeepRemote=$true}
$base = Join-Path $env:LOCALAPPDATA 'ExponentialEngine'
$state = Join-Path $base 'flow-state'
New-Item -ItemType Directory -Path $state -Force | Out-Null
$marker = Join-Path $state 'human-mode.json'
$receipt = Join-Path $state 'human-control-stop.json'
$log = Join-Path $state 'owner-control-events.log'
$events = New-Object 'System.Collections.Generic.List[string]'
$taskNames = @(
 'Azathoth-Independent-PR-Controller',
 'ExponentialEngine-ContinuousFlow-Watchdog', 'ExponentialEngine-LaptopActor',
 'Portfolio Idle Worker', 'Rocksmith CDLC Unattended Worker',
 'Everward Unattended Product Reality', 'Azathoth-Idle-App-Watcher',
 'Azathoth-Idle-Mine-Watcher', 'Azathoth-Pytest-Diagnostic-8fe823f',
 'Datadog AI Usage Agent', 'Azathoth-Dashboard-Guardian',
 'Azathoth-Dashboard-Telemetry', 'Azathoth-Remote-Bridge-Elevated',
 'Azathoth-Remote-Control-Watchdog', 'Azathoth-Remote-Recovery-Test'
)
$workerPattern = 'watch_pinned_continuous_flow\.ps1|launch_local_actor\.ps1|continuous_flow_supervisor|independent_pr_controller\.py|green_pr_watch_loop\.py|github_evidence_watch_loop\.py|autonomous_slice_builder\.py|laptop_actor_bootstrap\.py|portfolio-idle-worker\.ps1|--unattended-worker|Idle-App-Watcher\.ps1|Ensure-IdleMineRunning\.ps1|Guard-GitHubMailbox\.ps1|Watch-GitHubMailbox\.ps1|foundry_dashboard_server\.py|DashboardGuardian\.ps1|DashboardTelemetryRefresher\.ps1|RemoteBridgeGuardian\.ps1|RemoteBridgeSupervisor\.ps1'
$remotePattern = 'desktop-commander|@wonderwhy-er[/\\]desktop-commander'
function Find-OwnerWorkers {
 @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
  if(-not $_.CommandLine -or $_.ProcessId -eq $PID) { return $false }
  $name=$_.Name; $command=$_.CommandLine
  if($name -match '^pythonw?\.exe$') { return ($command -match $workerPattern) }
  if($name -match '^(powershell|pwsh)\.exe$') {
    return ($command -match '(?i)-File\b' -and $command -match $workerPattern)
  }
  if($name -eq 'cmd.exe') {
    return ($command -match '(?i)start-dashboard\.cmd|ExponentialEngine-ContinuousFlow\.cmd|Azathoth-Remote-Control-Watchdog\.cmd')
  }
  return $false
 })
}
function Find-RemoteProcesses {
 @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
  $_.Name -match '^(node|cmd)\.exe$' -and
  $_.CommandLine -and
  $_.CommandLine -match $remotePattern
 })
}
function Get-OwnerState {
 $active = $false
 if(Test-Path $marker) {
  try{$active=[bool]((Get-Content $marker -Raw|ConvertFrom-Json).enabled)}
  catch{$active=$true}
 }
 $active
}
if(-not $AuditOnly) {
 $json = @{enabled=$true;since=[DateTimeOffset]::UtcNow.ToString('o');source='master-owner-control'} | ConvertTo-Json
 $json | Set-Content -LiteralPath $marker -Encoding UTF8 -ErrorAction Stop
 @{schema_version=1;status='OWNER_HANDOFF_IN_PROGRESS';checked_at=[DateTimeOffset]::UtcNow.ToString('o');human_mode=$true} |
    ConvertTo-Json | Set-Content -LiteralPath $receipt -Encoding UTF8
 # Immediately stop workers and the remote transport before slower scheduled-task cleanup.
 $targets = @(Find-OwnerWorkers)
 foreach($p in $targets) {
  try {Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop; $events.Add('KILL_WORKER '+$p.ProcessId)}
  catch {$events.Add('KILL_WORKER_FAIL '+$p.ProcessId)}
 }
 if(-not $KeepRemote) {
 # Early stage: revoke remote access before slower maintenance cleanup; the local process continues and writes its receipt.
 try {Stop-ScheduledTask -TaskName 'Azathoth-Remote-Desktop-Commander' -ErrorAction Stop; $events.Add('STOP_REMOTE_TASK')}
 catch {$events.Add('STOP_REMOTE_TASK_FAILED')}
 try {Disable-ScheduledTask -TaskName 'Azathoth-Remote-Desktop-Commander' -ErrorAction Stop | Out-Null; $events.Add('DISABLE_REMOTE_TASK')}
 catch {$events.Add('DISABLE_REMOTE_TASK_DENIED')}
 foreach($r in @(Find-RemoteProcesses)) {
  try {Stop-Process -Id $r.ProcessId -Force -ErrorAction Stop} catch {}
 }
}
 foreach($name in $taskNames) {
  $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
  if(-not $task){continue}
  try {Stop-ScheduledTask -TaskName $name -TaskPath $task.TaskPath -ErrorAction Stop; $events.Add('STOP '+$name)}
  catch {$events.Add('STOP_FAIL '+$name+': '+$_.Exception.Message)}
  try {Disable-ScheduledTask -TaskName $name -TaskPath $task.TaskPath -ErrorAction Stop | Out-Null; $events.Add('DISABLE '+$name)}
  catch {$events.Add('DISABLE_DENIED '+$name)}
 }

}
$remaining = @(Find-OwnerWorkers)
$taskRunning = @($taskNames | ForEach-Object {
 $name = $_
 $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
 if($task -and $task.State -eq 'Running') { $name }
})
$ownerActive = Get-OwnerState
$remainingRemote = @(Find-RemoteProcesses)
$status = if(-not $ownerActive){'FAILED_MARKER'} elseif($remaining.Count -ne 0 -or $taskRunning.Count -ne 0){'PARTIAL'} elseif($remainingRemote.Count -ne 0){'PARTIAL_REMOTE_ACTIVE'} else {'OWNER_CONTROL_GRANTED'}
$report = [ordered]@{
 schema_version=1
 checked_at=[DateTimeOffset]::UtcNow.ToString('o')
 human_mode=$ownerActive
 status=$status
 worker_count=$remaining.Count
 worker_pids=@($remaining|ForEach-Object {$_.ProcessId})
 remote_process_count=$remainingRemote.Count
 running_task_names=@($taskRunning)
 rocksmith_running=[bool](Get-Process -Name Rocksmith2014 -ErrorAction SilentlyContinue)
 remote_disconnect_requested=(!$AuditOnly -and !$KeepRemote)
 changes=@($events)
}
if(-not $AuditOnly) {
 $report|ConvertTo-Json -Depth 6|Set-Content -LiteralPath $receipt -Encoding UTF8
 Add-Content -LiteralPath $log -Value ("{0:o} {1}: workers={2} running_tasks={3}" -f (Get-Date),$status,$remaining.Count,$taskRunning.Count)
}
$report|ConvertTo-Json -Depth 6
if($SelfTest){
 $pass=($ownerActive -and $remaining.Count -eq 0 -and $taskRunning.Count -eq 0)
 @{checked_at=[DateTimeOffset]::UtcNow.ToString('o');passed=$pass;mode='human';owner_active=$ownerActive;worker_count=$remaining.Count;running_task_count=$taskRunning.Count;non_destructive=$true} |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $state 'desktop-human-selftest.json') -Encoding UTF8
 if(-not $pass){exit 3}
 exit 0
}
# Never report a successful desktop handoff when the owner lock or worker
# shutdown could not be verified. The VBS launcher propagates this exit code.
if($status -ne 'OWNER_CONTROL_GRANTED') { exit 3 }
exit 0
