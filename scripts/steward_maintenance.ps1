param(
    [ValidateSet("PreReboot", "StartupCheck")]
    [string]$Mode = "StartupCheck",
    [int]$RetentionDays = 5,
    [switch]$NoRestart
)

$ErrorActionPreference = "Stop"
$Base = Join-Path $env:LOCALAPPDATA "ExponentialEngine"
$StewardDir = Join-Path $Base "steward"
$Marker = Join-Path $StewardDir "maintenance.json"
$Report = Join-Path $StewardDir "maintenance-last.json"
New-Item -ItemType Directory -Force -Path $StewardDir | Out-Null

function Write-MaintenanceMarker {
    param([string]$Status)
    @{
        schema_version = 1
        status = $Status
        mode = $Mode
        updated_at = (Get-Date).ToUniversalTime().ToString("o")
        pid = $PID
    } | ConvertTo-Json | Set-Content -Encoding UTF8 -Path $Marker
}

function Remove-OldOwnedFiles {
    param([string[]]$Roots, [int]$Days)
    $cutoff = (Get-Date).AddDays(-[Math]::Max(1, $Days))
    $removed = @()
    foreach ($root in $Roots) {
        if (-not (Test-Path $root)) { continue }
        Get-ChildItem -Path $root -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object {
                $_.LastWriteTime -lt $cutoff -and
                $_.Extension -in @(".log", ".jsonl", ".tmp")
            } |
            ForEach-Object {
                $full = $_.FullName
                Remove-Item -LiteralPath $full -Force -ErrorAction SilentlyContinue
                if (-not (Test-Path -LiteralPath $full)) { $removed += $full }
            }
    }
    return $removed
}

function Get-LaptopActorQueue {
    $task = Get-ScheduledTask -TaskName "ExponentialEngine-LaptopActor" -ErrorAction SilentlyContinue
    if (-not $task) { return $null }
    $arguments = ($task.Actions | Select-Object -First 1).Arguments
    if ($arguments -match '--queue\s+"([^"]+)"') { return $Matches[1] }
    if ($arguments -match '--queue\s+(\S+)') { return $Matches[1] }
    return $null
}

function Repair-StaleActorLock {
    $queue = Get-LaptopActorQueue
    if (-not $queue) { return @{ status = "QUEUE_UNKNOWN" } }
    $lock = Join-Path (Join-Path $queue "state") "actor.lock"
    if (-not (Test-Path $lock)) { return @{ status = "NO_LOCK"; path = $lock } }
    $rawPid = (Get-Content -Path $lock -Raw -ErrorAction SilentlyContinue).Trim()
    $process = $null
    if ($rawPid -match '^\d+$') {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $rawPid" -ErrorAction SilentlyContinue
    }
    if ($process -and $process.CommandLine -like "*laptop_actor_bootstrap.py*") {
        return @{ status = "LIVE_LOCK"; path = $lock; owner_pid = [int]$rawPid }
    }
    Remove-Item -LiteralPath $lock -Force
    return @{ status = "STALE_LOCK_REMOVED"; path = $lock; former_pid = $rawPid }
}

$started = (Get-Date).ToUniversalTime()
$removed = @()
$lockResult = @{ status = "NOT_CHECKED" }

if ($Mode -eq "PreReboot") {
    Write-MaintenanceMarker -Status "PRE_REBOOT_MAINTENANCE"
    foreach ($taskName in @("ExponentialEngine-LaptopActor", "Portfolio Idle Worker")) {
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    }
    $removed = Remove-OldOwnedFiles -Roots @(
        (Join-Path $Base "logs"),
        (Join-Path $env:LOCALAPPDATA "PortfolioIdleWorker\logs"),
        (Join-Path $Base "flow-state")
    ) -Days $RetentionDays
} else {
    Write-MaintenanceMarker -Status "STARTUP_SANITY"
    $lockResult = Repair-StaleActorLock
    $removed = Remove-OldOwnedFiles -Roots @(
        (Join-Path $Base "logs"),
        (Join-Path $env:LOCALAPPDATA "PortfolioIdleWorker\logs"),
        (Join-Path $Base "flow-state")
    ) -Days $RetentionDays
}

$payload = @{
    schema_version = 1
    mode = $Mode
    started_at_utc = $started.ToString("o")
    completed_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    removed_owned_file_count = @($removed).Count
    removed_owned_files = @($removed)
    actor_lock = $lockResult
    retention_days = $RetentionDays
    restart_requested = ($Mode -eq "PreReboot" -and -not $NoRestart)
}
$payload | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 -Path $Report

if ($Mode -eq "StartupCheck") {
    Remove-Item -LiteralPath $Marker -Force -ErrorAction SilentlyContinue
    exit 0
}

if (-not $NoRestart) {
    Restart-Computer -Force
}
exit 0
