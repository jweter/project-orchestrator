# Intentional development transition; never steal a running game session.
param([switch]$SelfTest,[switch]$ExplicitUserAuthorization)
$ErrorActionPreference = 'Continue'
$root=Join-Path $env:LOCALAPPDATA 'ExponentialEngine'
$state=Join-Path $root 'flow-state'
New-Item -ItemType Directory -Path $state -Force | Out-Null
$log=Join-Path $state 'control-switch.log'

# A failed launch is not owner consent to leave autonomous scheduling enabled.
# Restore the existing authoritative owner-control mechanism and preserve the
# remote recovery channel. Exit nonzero unless the stop has been acknowledged.
function Restore-OwnerAfterResumeFailure([string]$reason, [int]$returnCode=6) {
  Add-Content -LiteralPath $log -Value ("{0:o} TAKE_THE_WHEEL_ROLLBACK: {1}" -f (Get-Date),$reason)
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
    $ownerNow = Get-Content -LiteralPath (Join-Path $state 'human-mode.json') -Raw | ConvertFrom-Json
    if ($ownerNow.enabled -cne $true) { exit 8 }
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
 @{checked_at=[DateTimeOffset]::UtcNow.ToString('o');passed=$pass;mode='wheel';owner_marker_preserved=(Test-Path (Join-Path $state 'human-mode.json'));non_destructive=$true} |
  ConvertTo-Json | Set-Content -LiteralPath (Join-Path $state 'desktop-wheel-selftest.json') -Encoding UTF8
 if($pass){exit 0}else{exit 4}
}
if(Get-Process -Name 'Rocksmith2014' -ErrorAction SilentlyContinue) {
 Add-Content -Path $log -Value ("{0:o} TAKE_THE_WHEEL_DENIED: Rocksmith running" -f (Get-Date))
 exit 2
}
# An explicit click is owner consent to resume, NOT authority to revive the
# obsolete supervisor. Refuse until both launchers point to the same verified
# local release and the legacy Startup path is retired.
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
 Add-Content -Path $log -Value ("{0:o} TAKE_THE_WHEEL_BLOCKED_UNVERIFIED_RELEASE: {1}" -f (Get-Date),$reason)
 # The desktop VBS wrapper displays a single actionable message for exit 5.
 # Neither the owner marker nor any scheduled task has been changed.
 exit 5
}
if(-not $ExplicitUserAuthorization){
 Add-Type -AssemblyName PresentationFramework
 $choice=[System.Windows.MessageBox]::Show(
  'This will START Azathoth development workers and take control of the laptop. Continue?',
  'TAKE THE WHEEL - Start Development',
  'YesNo', 'Warning')
}else{ $choice='Yes'; Add-Content -Path $log -Value ("{0:o} TAKE_THE_WHEEL_EXPLICIT_USER_AUTHORIZATION" -f (Get-Date)) }
if($choice -ne 'Yes') {
 Add-Content -Path $log -Value ("{0:o} TAKE_THE_WHEEL_CANCELLED" -f (Get-Date))
 exit 0
}
$marker=Join-Path $state 'human-mode.json'
# The game may have opened after the first check or while confirmation was showing.
if(Get-Process -Name 'Rocksmith2014' -ErrorAction SilentlyContinue) {
 Add-Content -Path $log -Value ("{0:o} TAKE_THE_WHEEL_DENIED: Rocksmith opened during confirmation" -f (Get-Date))
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
 Add-Content -Path $log -Value ("{0:o} CONTROL_GUARD_MISSING: {1}" -f (Get-Date),($guardIssues -join '; '))
 [System.Windows.MessageBox]::Show(
  'Safety guards are missing or outdated. Development remains stopped. Ask ChatGPT to repair Azathoth before resuming.',
  'TAKE THE WHEEL BLOCKED', 'OK', 'Warning') | Out-Null
 exit 4
}
# Owner-authorized transition: preserve the marker for reliable remote verification.
try {
    if (-not (Test-Path -LiteralPath $marker)) {
        throw "Owner-control marker missing; refusing transition."
    }

    $current = Get-Content -LiteralPath $marker -Raw -ErrorAction Stop |
        ConvertFrom-Json -ErrorAction Stop

    if ($current.enabled -isnot [bool]) {
        throw "Owner-control marker is not a verified boolean; refusing transition."
    }
    # A second owner click may arrive after a previous released-but-stopped
    # state. An explicitly approved, verified release may resume idempotently.

    $next = [ordered]@{
        since   = [DateTimeOffset]::UtcNow.ToString('o')
        source  = 'master-owner-control'
        enabled = $false
    }

    $temp = Join-Path $state ("human-mode-" + [guid]::NewGuid().ToString('N') + ".tmp")

    try {
        $json = $next | ConvertTo-Json
        [System.IO.File]::WriteAllText(
            $temp,
            $json,
            (New-Object System.Text.UTF8Encoding($false))
        )

        # Preserve the owner-control marker using an atomic same-directory rename.
$replacementFile = Join-Path $state ("human-mode-" + [guid]::NewGuid().ToString('N') + ".json")
[System.IO.File]::Copy($temp, $replacementFile)
Move-Item -LiteralPath $replacementFile -Destination $marker -Force -ErrorAction Stop

        $verified = Get-Content -LiteralPath $marker -Raw |
            ConvertFrom-Json -ErrorAction Stop

        if ($verified.enabled -ne $false) {
            throw "Marker verification failed."
        }
    }
    finally {
        if (Test-Path -LiteralPath $temp) {
            Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue
        }
    }
}
catch {
    Add-Content -Path $log -Value (
        "{0:o} OWNER_LOCK_RELEASE_FAILED: {1}" -f
        (Get-Date), $_.Exception.Message
    )
    exit 3
}
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
 catch { Add-Content -Path $log -Value ("{0:o} REMOTE_ENABLE_FAILED: {1}" -f (Get-Date),$_.Exception.Message) }
 try { Start-ScheduledTask -TaskName 'Azathoth-Remote-Desktop-Commander' -ErrorAction Stop }
 catch { Add-Content -Path $log -Value ("{0:o} REMOTE_START_FAILED: {1}" -f (Get-Date),$_.Exception.Message) }
}
try {
 Enable-ScheduledTask -TaskName 'Azathoth-Remote-Bridge-Elevated' -ErrorAction Stop | Out-Null
 Start-ScheduledTask -TaskName 'Azathoth-Remote-Bridge-Elevated' -ErrorAction Stop
}
catch { Add-Content -Path $log -Value ("{0:o} REMOTE_BRIDGE_START_FAILED: {1}" -f (Get-Date),$_.Exception.Message) }
# Success requires observed runtime identity, not just a task-start request.
try {
  $pin=Get-Content (Join-Path $state 'supervisor-pinned-release.json') -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
  $actorPin=Get-Content (Join-Path $state 'actor-local-promotion.json') -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
} catch {
  Restore-OwnerAfterResumeFailure ("POST_RELEASE_PIN_READ_FAILED: {0}" -f $_.Exception.Message) 6
}
if ($pin.accepted_sha -cne $actorPin.accepted_sha -or
    $pin.accepted_repo -ine $actorPin.accepted_repo) {
  Restore-OwnerAfterResumeFailure 'POST_RELEASE_PIN_MISMATCH' 6
}
$deadline=(Get-Date).AddSeconds(120)
$runtimeVerified=$false
while((Get-Date) -lt $deadline) {
 try {
  $ownerNow=Get-Content $marker -Raw | ConvertFrom-Json
  if($ownerNow.enabled -cne $false){
    Restore-OwnerAfterResumeFailure 'OWNER_RECLAIMED_DURING_STARTUP' 7
  }
  $heartbeat=Get-Content (Join-Path $state 'supervisor-generation-heartbeat.json') -Raw | ConvertFrom-Json
  $age=([DateTimeOffset]::UtcNow-[DateTimeOffset]::Parse($heartbeat.heartbeat_at_utc)).TotalSeconds
  $fresh=($age -ge 0 -and $age -lt 180 -and $heartbeat.status -ceq 'RUNNING_CURRENT')
  $procs=@(Get-CimInstance Win32_Process | Where-Object {$_.Name -match '^python' -and $_.CommandLine -like '*continuous_flow_supervisor.py*'})
  $actors=@(Get-CimInstance Win32_Process | Where-Object {$_.Name -match '^python' -and $_.CommandLine -like '*laptop_actor_bootstrap.py*' -and $_.CommandLine.Contains([string]$actorPin.actor_repo)})
  if($fresh -and $heartbeat.actor_revision -ceq $pin.accepted_sha -and $procs.Count -eq 1 -and $procs[0].CommandLine.Contains([string]$pin.accepted_repo) -and $actors.Count -eq 1){$runtimeVerified=$true;break}
 } catch {}
 Start-Sleep -Seconds 2
}
@{checked_at=[DateTimeOffset]::UtcNow.ToString('o');verified=$runtimeVerified;accepted_sha=$pin.accepted_sha} | ConvertTo-Json | Set-Content (Join-Path $state 'desktop-resume-runtime.json') -Encoding UTF8
if(-not $runtimeVerified){
  Restore-OwnerAfterResumeFailure 'TAKE_THE_WHEEL_RUNTIME_NOT_VERIFIED' 6
}
Add-Content -Path $log -Value ("{0:o} TAKE_THE_WHEEL_CONFIRMED revision={1}" -f (Get-Date),$pin.accepted_sha)
exit 0
