$ErrorActionPreference = 'Continue'

function Section([string]$Name) {
    Write-Host "`n=== $Name ==="
}

function Run([string]$Label, [scriptblock]$Command) {
    Write-Host "-- $Label"
    try { & $Command } catch { Write-Host "ERROR: $($_.Exception.Message)" }
}

Write-Host "Exponential Engine - Laptop Actor Diagnostic"
Write-Host "UTC: $([DateTime]::UtcNow.ToString('o'))"
Write-Host "This report intentionally omits GitHub tokens and private payload contents."

Section 'Scheduled tasks'
foreach ($taskName in @('Everward Unattended Product Reality', 'ExponentialEngine-LaptopActor')) {
    Run $taskName {
        $task = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
        $info = Get-ScheduledTaskInfo -TaskName $taskName -ErrorAction Stop
        [pscustomobject]@{
            TaskName = $taskName
            State = $task.State
            LastRunTime = $info.LastRunTime
            LastTaskResult = $info.LastTaskResult
            NextRunTime = $info.NextRunTime
        } | Format-List
    }
}

Section 'Tool availability'
Run 'git' { git --version }
Run 'python' { python --version }
Run 'GitHub CLI' { gh --version | Select-Object -First 1 }
Run 'GitHub authentication' {
    gh auth status -h github.com 2>&1 | ForEach-Object {
        # Keep account/auth state useful while suppressing token-like material.
        $_ -replace '(?i)(token:).*','$1 [REDACTED]'
    }
}

Section 'Actor checkouts'
$roots = @(
    [pscustomobject]@{ Name='Portfolio bootstrap foothold'; Path=(Join-Path $env:LOCALAPPDATA 'ProjectOrchestrator\actor') },
    [pscustomobject]@{ Name='Portfolio scheduled actor'; Path=(Join-Path $env:LOCALAPPDATA 'ExponentialEngine\actor-repo') }
)
foreach ($root in $roots) {
    Run $root.Name {
        Write-Host "Exists: $(Test-Path $root.Path)"
        if (Test-Path (Join-Path $root.Path '.git')) {
            Push-Location $root.Path
            try {
                Write-Host "HEAD: $(git rev-parse HEAD)"
                Write-Host "Branch: $(git branch --show-current)"
                $origin = git remote get-url origin
                if ($origin -match 'github\.com[:/]([^/]+/[^/.]+)(?:\.git)?$') {
                    Write-Host "Origin repository: $($Matches[1])"
                } else {
                    Write-Host 'Origin repository: UNRECOGNIZED'
                }
                Write-Host "Dirty files: $((git status --porcelain | Measure-Object).Count)"
            } finally { Pop-Location }
        }
    }
}

Section 'Queue workspace'
$queue = Join-Path $env:LOCALAPPDATA 'ExponentialEngine\queue'
Run 'Local queue state' {
    Write-Host "Queue root exists: $(Test-Path $queue)"
    foreach ($name in @('inbox','outbox','results','failed')) {
        $path = Join-Path $queue $name
        $count = if (Test-Path $path) { (Get-ChildItem $path -File -ErrorAction SilentlyContinue | Measure-Object).Count } else { 0 }
        Write-Host "$name files: $count"
    }
}

Section 'Bootstrap files'
$scheduledActor = Join-Path $env:LOCALAPPDATA 'ExponentialEngine\actor-repo'
foreach ($relative in @('scripts\laptop_actor_bootstrap.py','scripts\laptop_actor_github_bridge.py','scripts\laptop_actor_runner.py','scripts\install_laptop_actor.ps1')) {
    Write-Host "$relative : $(Test-Path (Join-Path $scheduledActor $relative))"
}

Section 'Interpretation'
Write-Host 'Expected healthy state:'
Write-Host '  * ExponentialEngine-LaptopActor exists and has a successful recent LastTaskResult (0).'
Write-Host '  * Scheduled actor checkout exists and points to jweter/project-orchestrator.'
Write-Host '  * git, Python, and gh are available; gh is authenticated.'
Write-Host '  * Bootstrap/bridge/runner files exist in the scheduled actor checkout.'
Write-Host 'If the portfolio task is missing, the Everward -> portfolio bootstrap boundary failed.'
Write-Host 'If the task exists but LastTaskResult is nonzero, the actor execution boundary failed.'
Write-Host 'If all local checks are healthy but GitHub jobs remain pending, inspect queue transport/runner evidence next.'
