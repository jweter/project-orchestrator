# Intentional development transition; never steal a running game session.
# Owner decision 2026-10-25: execution authority comes ONLY from the
# authoritative owner controller. This icon records an authenticated owner
# Take the Wheel command (bound to the accepted release and this desktop
# session), starts the pinned engine, and reports success only after the
# running supervisor has acknowledged the grant and recorded a permitting
# execution decision. Any failure revokes and returns control to the owner.
param([switch]$SelfTest,[switch]$ExplicitUserAuthorization)
$ErrorActionPreference = 'Continue'
$root=Join-Path $env:LOCALAPPDATA 'ExponentialEngine'
$state=Join-Path $root 'flow-state'
New-Item -ItemType Directory -Path $state -Force | Out-Null
$log=Join-Path $state 'control-switch.log'
$script:controllerLoaded=$false

function Write-WheelLog([string]$message) {
  Add-Content -LiteralPath $log -Value ("{0:o} {1}" -f (Get-Date),$message)
}

# A failed launch is not owner consent to leave autonomous scheduling enabled.
# Give Me Control revokes through the controller, stops the engine and
# preserves the remote recovery channel. Exit nonzero unless it is confirmed.
function Restore-OwnerAfterResumeFailure([string]$reason, [int]$returnCode=6) {
  Write-WheelLog ("TAKE_THE_WHEEL_ROLLBACK: {0}" -f $reason)
  $human=Join-Path $root 'Enter-HumanMode.ps1'
  if (-not (Test-Path -LiteralPath $human -PathType Leaf)) {
    Add-Content -LiteralPath $log -Value 'OWNER_ROLLBACK_SCRIPT_MISSING'
    exit 8
  }
  try {
    $stopReport=@(& powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $human -KeepRemote 2>&1)
    $stopCode=$LASTEXITCODE
    if($stopCode -ne 0) {
      Add-Content -LiteralPath $log -Value ("OWNER_ROLLBACK_NOT_VERIFIED: {0}" -f ($stopReport|Out-String))
      exit 8
    }
    if ($script:controllerLoaded -and (Get-IronGodOwnerStatus $state) -cne 'OWNER_CONTROL_ACTIVE') {
      Add-Content -LiteralPath $log -Value 'OWNER_ROLLBACK_CONTROLLER_NOT_REVOKED'
      exit 8
    }
    Add-Content -LiteralPath $log -Value 'OWNER_ROLLBACK_CONFIRMED_REMOTE_PRESERVED'
  } catch {
    Add-Content -LiteralPath $log -Value ("OWNER_ROLLBACK_EXCEPTION: {0}" -f $_.Exception.Message)
    exit 8
  }
  exit $returnCode
}
if($SelfTest){
 $audit=Join-Path $root 'Test-DesktopControlShortcuts.ps1'
 $pass=$false
 if(Test-Path $audit){
  $null=& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $audit 2>&1
  $pass=($LASTEXITCODE -eq 0)
 }
 @{checked_at=[DateTimeOffset]::UtcNow.ToString('o');passed=$pass;mode='wheel';owner_controller_present=(Test-Path (Join-Path $state 'IronGodControl'));non_destructive=$true} |
  ConvertTo-Json | Set-Content -LiteralPath (Join-Path $state 'desktop-wheel-selftest.json') -Encoding UTF8
 if($pass){exit 0}else{exit 4}
}
if(Get-Process -Name 'Rocksmith2014' -ErrorAction SilentlyContinue) {
 Write-WheelLog 'TAKE_THE_WHEEL_DENIED: Rocksmith running'
 exit 2
}
# An explicit click is owner consent to resume, NOT authority to revive the
# obsolete supervisor. Refuse until both launchers point to the same verified
# local release (which must carry the owner controller) and the legacy
# Startup path is retired.
$releaseReadiness=Join-Path $root 'Check-Development-ResumeReady.ps1'
$readinessOutput=@()
$releaseReady=$false
if(Test-Path -LiteralPath $releaseReadiness -PathType Leaf) {
 try {
  $readinessOutput=@(& powershell.exe -NoProfile -NonInteractive -File $releaseReadiness 2>&1)
  $releaseReady=($LASTEXITCODE -eq 0)
 } catch { $readinessOutput=@($_.Exception.Message) }
}
if(-not $releaseReady) {
 $reason=if($readinessOutput.Count -gt 0){($readinessOutput|Out-String).Trim()}else{'No verified local-release readiness evidence.'}
 Write-WheelLog ("TAKE_THE_WHEEL_BLOCKED_UNVERIFIED_RELEASE: {0}" -f $reason)
 # The desktop VBS wrapper displays a single actionable message for exit 5.
 # No owner command has been recorded and no scheduled task has been changed.
 exit 5
}
if(-not $ExplicitUserAuthorization){
 Add-Type -AssemblyName PresentationFramework
 $choice=[System.Windows.MessageBox]::Show(
  'This will START Azathoth development workers and take control of the laptop. Continue?',
  'TAKE THE WHEEL - Start Development',
  'YesNo', 'Warning')
}else{ $choice='Yes'; Write-WheelLog 'TAKE_THE_WHEEL_EXPLICIT_USER_AUTHORIZATION' }
if($choice -ne 'Yes') {
 Write-WheelLog 'TAKE_THE_WHEEL_CANCELLED'
 exit 0
}
# The game may have opened after the first check or while confirmation was showing.
if(Get-Process -Name 'Rocksmith2014' -ErrorAction SilentlyContinue) {
 Write-WheelLog 'TAKE_THE_WHEEL_DENIED: Rocksmith opened during confirmation'
 exit 2
}
# Fail closed if an installer or update has overwritten an owner-control veto.
$requiredGuards=@(
 [pscustomobject]@{Path='C:\ProgramData\AzathothControl\LivenessGuardian.ps1';Token='HOST_REBOOT_SUPPRESSED'},
 [pscustomobject]@{Path='C:\ProgramData\AzathothControl\DashboardGuardian.ps1';Token='Test-AzathothOwnerControl'},
 [pscustomobject]@{Path='C:\ProgramData\AzathothControl\DashboardTelemetryRefresher.ps1';Token='Test-AzathothOwnerControl'},
 [pscustomobject]@{Path='C:\ProgramData\AzathothControl\RemoteBridgeGuardian.ps1';Token='ownerRemoteGuardMarker'},
 [pscustomobject]@{Path='C:\ProgramData\AzathothControl\RemoteBridgeSupervisor.ps1';Token='ownerRemoteSupervisorLoopMarker'},
 [pscustomobject]@{Path=(Join-Path $root 'FallbackControl\Watch-GitHubMailbox.ps1');Token='REJECTED_OWNER_CONTROL_OR_GAME'}
)
$guardIssues=New-Object 'System.Collections.Generic.List[string]'
foreach($guard in $requiredGuards) {
 try {
  if(-not(Test-Path -LiteralPath $guard.Path)){ $guardIssues.Add($guard.Path); continue }
  $source=Get-Content -LiteralPath $guard.Path -Raw -ErrorAction Stop
  if(-not $source.Contains($guard.Token)) { $guardIssues.Add($guard.Path) }
 } catch { $guardIssues.Add($guard.Path) }
}
if($guardIssues.Count -ne 0) {
 Write-WheelLog ("CONTROL_GUARD_MISSING: {0}" -f ($guardIssues -join '; '))
 [System.Windows.MessageBox]::Show(
  'Safety guards are missing or outdated. Development remains stopped. Ask ChatGPT to repair Azathoth before resuming.',
  'TAKE THE WHEEL BLOCKED', 'OK', 'Warning') | Out-Null
 exit 4
}
# Load the owner-controller bridge from the CURRENTLY PINNED release only.
try {
 $pinned=Get-Content (Join-Path $state 'supervisor-pinned-release.json') -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
 . (Join-Path ([string]$pinned.accepted_repo) 'scripts\desktop-control\IronGodOwnerControl.ps1')
 $script:controllerLoaded=$true
} catch {
 Write-WheelLog ("TAKE_THE_WHEEL_BLOCKED_OWNER_CONTROLLER_UNAVAILABLE: {0}" -f $_.Exception.Message)
 exit 9
}
# The authenticated owner grant. The controller itself verifies that this
# process is the desktop-icon launch in the active console session, binds the
# grant to the pinned release SHA, and refuses replays and stale generations.
$grant=Invoke-IronGodOwnerController -StateRoot $state -Arguments @('issue','--action','TAKE_THE_WHEEL','--pin',(Join-Path $state 'supervisor-pinned-release.json'))
if($grant.ExitCode -ne 0 -or -not $grant.Result.generation) {
 Write-WheelLog ("TAKE_THE_WHEEL_REFUSED_BY_CONTROLLER: {0}" -f $grant.Result.reason)
 exit 9
}
$generation=[int]$grant.Result.generation
Write-WheelLog ("TAKE_THE_WHEEL_OWNER_GRANT_RECORDED generation={0} command={1} release={2}" -f $generation,$grant.Result.command_id,$grant.Result.release_sha)
# Unmigrated SYSTEM guardians still key their veto off the legacy file; tell
# them the owner released control. Iron God never reads it and it grants nothing.
try { Set-IronGodLegacyGuardianSignal $state $false 'take-the-wheel' }
catch { Restore-OwnerAfterResumeFailure ("LEGACY_GUARDIAN_SIGNAL_FAILED: {0}" -f $_.Exception.Message) 3 }
# Only an execution decision recorded after this point can confirm the resume.
$resumeStartedUtc=[DateTimeOffset]::UtcNow
# Start only the two validated engine entrypoints. Legacy standalone workers
# must not silently regain scheduling authority during a local-release cutover.
foreach($name in @('ExponentialEngine-ContinuousFlow-Watchdog','ExponentialEngine-LaptopActor')) {
 try {
  Enable-ScheduledTask -TaskName $name -ErrorAction Stop | Out-Null
  Start-ScheduledTask -TaskName $name -ErrorAction Stop
 } catch {
  Restore-OwnerAfterResumeFailure ("START_FAILED {0}: {1}" -f $name,$_.Exception.Message) 6
 }
}
foreach($name in @('Azathoth-Dashboard-Guardian','Azathoth-Dashboard-Telemetry')) {
 try {
  Enable-ScheduledTask -TaskName $name -ErrorAction Stop | Out-Null
  Start-ScheduledTask -TaskName $name -ErrorAction Stop
 } catch { Add-Content $log ("OPTIONAL_SERVICE_START_FAILED {0}: {1}" -f $name,$_.Exception.Message) }
}
$remote=@(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.Name -eq 'node.exe' -and $_.CommandLine -match 'desktop-commander.*remote' })
if($remote.Count -eq 0) {
 try { Enable-ScheduledTask -TaskName 'Azathoth-Remote-Desktop-Commander' -ErrorAction Stop | Out-Null }
 catch { Write-WheelLog ("REMOTE_ENABLE_FAILED: {0}" -f $_.Exception.Message) }
 try { Start-ScheduledTask -TaskName 'Azathoth-Remote-Desktop-Commander' -ErrorAction Stop }
 catch { Write-WheelLog ("REMOTE_START_FAILED: {0}" -f $_.Exception.Message) }
}
try {
 Enable-ScheduledTask -TaskName 'Azathoth-Remote-Bridge-Elevated' -ErrorAction Stop | Out-Null
 Start-ScheduledTask -TaskName 'Azathoth-Remote-Bridge-Elevated' -ErrorAction Stop
}
catch { Write-WheelLog ("REMOTE_BRIDGE_START_FAILED: {0}" -f $_.Exception.Message) }
# Success requires observed runtime identity, not just a task-start request.
try {
  $pin=Get-Content (Join-Path $state 'supervisor-pinned-release.json') -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
  $actorPin=Get-Content (Join-Path $state 'actor-local-promotion.json') -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
} catch {
  Restore-OwnerAfterResumeFailure ("POST_RELEASE_PIN_READ_FAILED: {0}" -f $_.Exception.Message) 6
}
if ($pin.accepted_sha -cne $actorPin.accepted_sha -or
    $pin.accepted_repo -ine $actorPin.accepted_repo -or
    $pin.accepted_sha -cne $grant.Result.release_sha) {
  Restore-OwnerAfterResumeFailure 'POST_RELEASE_PIN_MISMATCH' 6
}
# The running supervisor must acknowledge THIS grant inside the controller's
# acknowledgment window; nothing else can make it effective.
$ack=Invoke-IronGodOwnerController -StateRoot $state -Arguments @('wait-ack','--generation',[string]$generation,'--timeout','150')
if($ack.ExitCode -eq 7) { Restore-OwnerAfterResumeFailure 'OWNER_RECLAIMED_DURING_STARTUP' 7 }
if($ack.ExitCode -ne 0) { Restore-OwnerAfterResumeFailure 'TAKE_THE_WHEEL_SUPERVISOR_ACK_MISSING' 6 }
Write-WheelLog ("TAKE_THE_WHEEL_SUPERVISOR_ACKNOWLEDGED generation={0}" -f $generation)
$deadline=(Get-Date).AddSeconds(120)
$runtimeVerified=$false
$executionAuthorized=$false
$controllerPermits=$false
while((Get-Date) -lt $deadline) {
 try {
  if((Get-IronGodOwnerStatus $state) -ceq 'OWNER_CONTROL_ACTIVE'){
    Restore-OwnerAfterResumeFailure 'OWNER_RECLAIMED_DURING_STARTUP' 7
  }
  $heartbeat=Get-Content (Join-Path $state 'supervisor-generation-heartbeat.json') -Raw | ConvertFrom-Json
  $age=([DateTimeOffset]::UtcNow-[DateTimeOffset]::Parse($heartbeat.heartbeat_at_utc)).TotalSeconds
  $fresh=($age -ge 0 -and $age -lt 180 -and $heartbeat.status -ceq 'RUNNING_CURRENT')
  $procs=@(Get-CimInstance Win32_Process | Where-Object {$_.Name -match '^python' -and $_.CommandLine -like '*continuous_flow_supervisor.py*'})
  $actors=@(Get-CimInstance Win32_Process | Where-Object {$_.Name -match '^python' -and $_.CommandLine -like '*laptop_actor_bootstrap.py*' -and $_.CommandLine.Contains([string]$actorPin.actor_repo)})
  $runtimeVerified=($fresh -and $heartbeat.actor_revision -ceq $pin.accepted_sha -and $procs.Count -eq 1 -and $procs[0].CommandLine.Contains([string]$pin.accepted_repo) -and $actors.Count -eq 1)
  # Resident processes are not authority: the running supervisor must also
  # record a permitting execution decision for this resume ...
  $executionAuthorized=$false
  try {
   $execution=Get-Content (Join-Path $state 'supervisor-execution-authority.json') -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
   $decidedAt=[DateTimeOffset]::Parse([string]$execution.observed_at_utc)
   $executionAuthorized=($execution.permitted -is [bool] -and $execution.permitted -eq $true -and $decidedAt -ge $resumeStartedUtc -and [string]$execution.release_sha -ceq [string]$pin.accepted_sha)
  } catch { $executionAuthorized=$false }
  # ... and the controller itself must agree, independently of that record.
  $decision=Invoke-IronGodOwnerController -StateRoot $state -Arguments @('status','--release-sha',[string]$pin.accepted_sha)
  $controllerPermits=($decision.ExitCode -eq 0 -and $decision.Result.permitted -eq $true -and [int]$decision.Result.generation -eq $generation)
  if($runtimeVerified -and $executionAuthorized -and $controllerPermits){break}
 } catch {}
 Start-Sleep -Seconds 2
}
@{checked_at=[DateTimeOffset]::UtcNow.ToString('o');verified=$runtimeVerified;execution_authorized=$executionAuthorized;controller_permits=$controllerPermits;generation=$generation;accepted_sha=$pin.accepted_sha} | ConvertTo-Json | Set-Content (Join-Path $state 'desktop-resume-runtime.json') -Encoding UTF8
if(-not $runtimeVerified){
  Restore-OwnerAfterResumeFailure 'TAKE_THE_WHEEL_RUNTIME_NOT_VERIFIED' 6
}
if(-not $executionAuthorized -or -not $controllerPermits){
  Restore-OwnerAfterResumeFailure 'TAKE_THE_WHEEL_EXECUTION_NOT_AUTHORIZED' 6
}
Write-WheelLog ("TAKE_THE_WHEEL_CONFIRMED revision={0} generation={1}" -f $pin.accepted_sha,$generation)
exit 0
