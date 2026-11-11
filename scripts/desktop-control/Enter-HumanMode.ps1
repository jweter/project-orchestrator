param(
 [switch]$AuditOnly,
 [switch]$KeepRemote,
 [switch]$SelfTest
)
# A single authoritative Give-Me-Control transaction.
# Owner decision 2026-10-25: the revocation is recorded in the authoritative
# owner controller first (effective immediately for any new dispatch), the
# running supervisor is given a short window to acknowledge it, and then the
# engine is stopped. The legacy marker is still written only as a stop signal
# for unmigrated SYSTEM guardians; it grants nothing.
$ErrorActionPreference = 'Continue'
if($SelfTest){$AuditOnly=$true;$KeepRemote=$true}
$base = Join-Path $env:LOCALAPPDATA 'ExponentialEngine'
$state = Join-Path $base 'flow-state'
New-Item -ItemType Directory -Path $state -Force | Out-Null
$marker = Join-Path $state 'human-mode.json'
$receipt = Join-Path $state 'human-control-stop.json'
$log = Join-Path $state 'owner-control-events.log'
$events = New-Object 'System.Collections.Generic.List[string]'
# Diagnostics are observations, not changes: an audit-only self-test must
# still report zero changes.
$diagnostics = New-Object 'System.Collections.Generic.List[string]'
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
function Get-OllamaBase {
 # Only ever the local server; a configured non-loopback endpoint is ignored rather than contacted.
 $base = if($env:OLLAMA_API_BASE) { $env:OLLAMA_API_BASE.TrimEnd('/') } else { 'http://127.0.0.1:11434' }
 if($base -match '^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$') { return $base }
 return 'http://127.0.0.1:11434'
}
function Get-LoadedOllamaModels {
 try {
  $ps = Invoke-RestMethod -Uri ((Get-OllamaBase) + '/api/ps') -Method Get -TimeoutSec 3 -ErrorAction Stop
  return @($ps.models | ForEach-Object { [string]$_.name } | Where-Object { $_ })
 } catch { return @() }
}
function Unload-OllamaModel([string]$Name) {
 try {
  $body = @{ model = $Name; keep_alive = 0 } | ConvertTo-Json -Compress
  Invoke-RestMethod -Uri ((Get-OllamaBase) + '/api/generate') -Method Post -Body $body -ContentType 'application/json' -TimeoutSec 10 -ErrorAction Stop | Out-Null
  return $true
 } catch { return $false }
}
function Find-OwnerWorkers {
 @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
  if(-not $_.CommandLine -or $_.ProcessId -eq $PID) { return $false }
  # A developer test run names engine scripts in its test file paths
  # (tests/test_laptop_actor_bootstrap.py); it is not an autonomous worker.
  if($_.CommandLine -match '(?i)\s-m\s+pytest\b') { return $false }
  $name=$_.Name; $command=$_.CommandLine
  # The owner's remote management bridge is not an autonomous builder.
  if($KeepRemote -and $command -match 'RemoteBridgeGuardian\.ps1|RemoteBridgeSupervisor\.ps1') { return $false }
  if($name -match '^pythonw?\.exe$') { return ($command -match $workerPattern) }
  if($name -match '^(powershell|pwsh)\.exe$') {
    return ($command -match '(?i)-File\b' -and $command -match $workerPattern)
  }
  if($name -eq 'cmd.exe') {
    if($KeepRemote -and $command -match '(?i)Azathoth-Remote-Control-Watchdog\.cmd') { return $false }
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
$script:controllerLoaded = $false
try {
 $pinned = Get-Content (Join-Path $state 'supervisor-pinned-release.json') -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
 . (Join-Path ([string]$pinned.accepted_repo) 'scripts\desktop-control\IronGodOwnerControl.ps1')
 $script:controllerLoaded = $true
} catch { $diagnostics.Add('OWNER_CONTROLLER_UNAVAILABLE: ' + $_.Exception.Message) }
function Get-OwnerState {
 if($script:controllerLoaded) { return ((Get-IronGodOwnerStatus $state) -ceq 'OWNER_CONTROL_ACTIVE') }
 # Pre-controller release pinned: report the legacy stop signal instead.
 $active = $false
 if(Test-Path $marker) {
  try{$active=[bool]((Get-Content $marker -Raw|ConvertFrom-Json).enabled)}
  catch{$active=$true}
 }
 $active
}
function Save-ControlReceipt($value) {
 $tmp=Join-Path $state ('owner-stop-'+[guid]::NewGuid().ToString('N')+'.tmp')
 try {
  [IO.File]::WriteAllText($tmp,($value|ConvertTo-Json -Depth 6),(New-Object Text.UTF8Encoding($false)))
  for($attempt=0;$attempt -lt 8;$attempt++) {
   try {
    if(Test-Path -LiteralPath $receipt){[IO.File]::Replace($tmp,$receipt,[NullString]::Value)}
    else{[IO.File]::Move($tmp,$receipt)}
    return $true
   } catch {if($attempt -eq 7){throw};Start-Sleep -Milliseconds 100}
  }
 } catch { $events.Add('RECEIPT_WRITE_FAILED: '+$_.Exception.Message); return $false }
 finally {if(Test-Path -LiteralPath $tmp){Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue}}
}
$receiptSaved=$true
if(-not $AuditOnly) {
 $json = @{enabled=$true;since=[DateTimeOffset]::UtcNow.ToString('o');source='master-owner-control'} | ConvertTo-Json
 $json | Set-Content -LiteralPath $marker -Encoding UTF8 -ErrorAction Stop
 $null=Save-ControlReceipt @{schema_version=1;status='OWNER_HANDOFF_IN_PROGRESS';checked_at=[DateTimeOffset]::UtcNow.ToString('o');human_mode=$true}
 # Authoritative revocation first, so the live supervisor can acknowledge it.
 if($script:controllerLoaded) {
  $revocation = Invoke-IronGodOwnerController -StateRoot $state -Arguments @('issue','--action','GIVE_ME_CONTROL','--pin',(Join-Path $state 'supervisor-pinned-release.json'))
  if($revocation.ExitCode -eq 0 -and $revocation.Result.generation) {
   $events.Add('CONTROLLER_REVOKED generation=' + $revocation.Result.generation)
   $liveSupervisors = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -like '*continuous_flow_supervisor.py*' })
   if($liveSupervisors.Count -gt 0) {
    $ack = Invoke-IronGodOwnerController -StateRoot $state -Arguments @('wait-ack','--generation',[string]$revocation.Result.generation,'--timeout','20')
    if($ack.ExitCode -eq 0) { $events.Add('SUPERVISOR_REVOCATION_ACKNOWLEDGED') } else { $events.Add('SUPERVISOR_REVOCATION_ACK_TIMEOUT_STOPPING') }
   } else { $events.Add('NO_SUPERVISOR_RUNNING') }
  } else { $events.Add('CONTROLLER_REVOKE_FAILED: ' + $revocation.Result.reason) }
 }
 # Immediately stop workers and the remote transport before slower scheduled-task cleanup.
 $targets = @(Find-OwnerWorkers)
 foreach($p in $targets) {
  try {Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop; $events.Add('KILL_WORKER '+$p.ProcessId)}
  catch {$events.Add('KILL_WORKER_FAIL '+$p.ProcessId)}
 }
 # Owner 2026-11-10: Give Me Control gives the laptop back for Rocksmith, so a model that is still resident (several GiB of
 # RAM, all-core llama-server work) must not linger after the worker is gone. Ask the local Ollama (loopback only) to unload
 # whatever it reports as loaded; best effort, bounded, and it never fails or delays the handoff.
 foreach($loaded in @(Get-LoadedOllamaModels)) {
  if(Unload-OllamaModel $loaded) { $events.Add('OLLAMA_UNLOADED '+$loaded) } else { $events.Add('OLLAMA_UNLOAD_FAILED '+$loaded) }
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
  if($KeepRemote -and $name -match '^Azathoth-Remote-') { $events.Add('KEEP_REMOTE_TASK '+$name); continue }
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
 if(-not ($KeepRemote -and $name -match '^Azathoth-Remote-')) {
   $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
   if($task -and $task.State -eq 'Running') { $name }
 }
})
$ownerActive = Get-OwnerState
$remainingRemote = @(Find-RemoteProcesses)
$status = if(-not $ownerActive){'FAILED_MARKER'} elseif($remaining.Count -ne 0 -or $taskRunning.Count -ne 0){'PARTIAL'} elseif($remainingRemote.Count -ne 0 -and -not $KeepRemote){'PARTIAL_REMOTE_ACTIVE'} else {'OWNER_CONTROL_GRANTED'}
# Owner decision 2026-11-08: Take the Wheel is the signal to develop (Dev Mode may close the approved memory hogs), and
# Give Me Control gives the laptop back. If Dev Mode closed programs, relaunch them now.
# Best effort and detached: it cannot delay, fail or change the handoff above, and it never runs in an audit or self-test.
if(-not $AuditOnly) {
 try {
  $pinForDevMode = Get-IronGodPinnedRelease $state
  $pythonForDevMode = Get-IronGodPython
  $devModeScript = if($pinForDevMode) { Join-Path $pinForDevMode.Repo 'scripts\iron_god_dev_mode.py' } else { $null }
  if($pythonForDevMode -and $devModeScript -and (Test-Path -LiteralPath $devModeScript -PathType Leaf)) {
   Start-Process -FilePath $pythonForDevMode -ArgumentList @(('"' + $devModeScript + '"'),'restore','--state-root',('"' + $state + '"')) -WindowStyle Hidden | Out-Null
   $events.Add('DEV_MODE_RESTORE_STARTED')
  } else { $diagnostics.Add('DEV_MODE_RESTORE_UNAVAILABLE_IN_PINNED_RELEASE') }
 } catch { $diagnostics.Add('DEV_MODE_RESTORE_FAILED: ' + $_.Exception.Message) }
}
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
 diagnostics=@($diagnostics)
}
if(-not $AuditOnly) {
 $receiptSaved=Save-ControlReceipt $report
 if(-not $receiptSaved){$status='PARTIAL_RECEIPT_UNAVAILABLE';$report.status=$status}
 Add-Content -LiteralPath $log -Value ("{0:o} {1}: workers={2} running_tasks={3}" -f (Get-Date),$status,$remaining.Count,$taskRunning.Count)
}
$report|ConvertTo-Json -Depth 6
if($SelfTest){
 # A non-destructive self-test verifies fixture owner authority and no mutation;
 # it cannot demand that unrelated live autonomous processes have stopped.
 # A real Give Me Control handoff still requires zero workers/tasks above.
 $pass=($ownerActive -and $AuditOnly -and $KeepRemote)
 @{checked_at=[DateTimeOffset]::UtcNow.ToString('o');passed=$pass;mode='human';owner_active=$ownerActive;worker_count=$remaining.Count;running_task_count=$taskRunning.Count;non_destructive=$true} |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $state 'desktop-human-selftest.json') -Encoding UTF8
 if(-not $pass){exit 3}
 exit 0
}
# Never report a successful desktop handoff when the owner lock or worker
# shutdown could not be verified. The VBS launcher propagates this exit code.
if($status -ne 'OWNER_CONTROL_GRANTED') { exit 3 }
exit 0
