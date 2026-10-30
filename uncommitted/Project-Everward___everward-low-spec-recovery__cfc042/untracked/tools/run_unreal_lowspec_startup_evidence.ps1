[CmdletBinding()]
param(
    [string]$UnrealRoot = "",
    [string]$EvidencePath = "",
    [int]$LaunchTimeoutSeconds = 30,
    [int]$SampleWindowSeconds = 60
)

$ErrorActionPreference = "Stop"

# This script produces a bounded, deterministic startup-success and
# process-memory fact for the opt-in Low-Spec Development profile. It reuses
# the exact scalability contract from tools/run_phase2_first_playtest.ps1
# -LowSpec -MeasureMemory, applied only as per-launch -ExecCmds so no
# production/default scalability setting is changed on disk. It never
# inspects rendering output, controls, or gameplay, and it must not be used
# to infer visual, control-feel, or gameplay Product Reality.

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ProjectPath = Join-Path $RepoRoot "unreal\Everward.uproject"

if (-not (Test-Path $ProjectPath -PathType Leaf)) { throw "Everward project not found at $ProjectPath" }

$Candidates = New-Object System.Collections.Generic.List[string]
if ($UnrealRoot) { $Candidates.Add($UnrealRoot) }
foreach ($Name in @("UE58_ROOT", "UE_5_8_ROOT", "UE_5_8")) {
    $Value = [Environment]::GetEnvironmentVariable($Name)
    if ($Value) { $Candidates.Add($Value) }
}
foreach ($CommonPath in @("C:\Program Files\Epic Games\UE_5.8", "C:\Epic Games\UE_5.8")) { $Candidates.Add($CommonPath) }
$EditorExe = $null
foreach ($Candidate in $Candidates | Select-Object -Unique) {
    $CandidateExe = Join-Path $Candidate "Engine\Binaries\Win64\UnrealEditor.exe"
    if (Test-Path $CandidateExe -PathType Leaf) { $EditorExe = (Resolve-Path $CandidateExe).Path; break }
}
if (-not $EditorExe) { throw "Unreal Engine 5.8 editor executable was not found." }

$GitCommit = (& git -C $RepoRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or -not $GitCommit) { throw "Unable to resolve the current Git commit." }

if (-not $EvidencePath) {
    $StateDir = Join-Path $env:LOCALAPPDATA "Everward\unattended-worker"
    $EvidenceDir = Join-Path $StateDir "logs\$($GitCommit.Substring(0, 12))"
    New-Item -ItemType Directory -Force -Path $EvidenceDir | Out-Null
    $EvidencePath = Join-Path $EvidenceDir "lowspec-startup-evidence.json"
}
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $EvidencePath) | Out-Null

$LowSpecCommands = @(
    "t.MaxFPS 30",
    "r.ScreenPercentage 65",
    "sg.ViewDistanceQuality 0",
    "sg.AntiAliasingQuality 0",
    "sg.ShadowQuality 0",
    "sg.GlobalIlluminationQuality 0",
    "sg.ReflectionQuality 0",
    "sg.PostProcessQuality 0",
    "sg.TextureQuality 0",
    "sg.EffectsQuality 0",
    "sg.FoliageQuality 0",
    "r.Streaming.PoolSize 384"
) -join ","
$Arguments = @("`"$ProjectPath`"", "-WINDOWED", "-ResX=1280", "-ResY=720", "-ExecCmds=`"$LowSpecCommands`"", "-NoSplash", "-Unattended")

function Write-Evidence {
    param([bool]$StartupSuccess, [double]$PeakWorkingSetMiB, [int]$ActualSampleWindowSeconds, [string]$Reason)
    [ordered]@{
        schema_version = 1
        commit = $GitCommit
        profile = "low_spec_development"
        startup_success = $StartupSuccess
        peak_working_set_mib = $PeakWorkingSetMiB
        sample_window_seconds = $ActualSampleWindowSeconds
        source = "UnrealEditor process WorkingSet64"
        interpretation = "process working set only; not total system RAM or shared-GPU memory; a bounded startup/memory fact only, not visual, control-feel, or gameplay Product Reality"
        reason = $Reason
        generated_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    } | ConvertTo-Json -Depth 6 | Set-Content -Path $EvidencePath -Encoding UTF8
}

$Process = $null
try {
    $Process = Start-Process -FilePath $EditorExe -ArgumentList $Arguments -PassThru -WindowStyle Hidden

    $LaunchDeadline = (Get-Date).AddSeconds($LaunchTimeoutSeconds)
    while ((Get-Date) -lt $LaunchDeadline -and -not $Process.HasExited) {
        Start-Sleep -Milliseconds 200
        $Process.Refresh()
    }
    if ($Process.HasExited) {
        Write-Evidence -StartupSuccess $false -PeakWorkingSetMiB 0 -ActualSampleWindowSeconds 0 -Reason "Editor process exited before the bounded launch deadline of $LaunchTimeoutSeconds seconds."
        throw "Low-spec editor startup evidence failed: process exited before the bounded launch deadline."
    }

    $SampleStart = Get-Date
    $SampleDeadline = $SampleStart.AddSeconds($SampleWindowSeconds)
    $PeakWorkingSetBytes = 0L
    $CrashedDuringSample = $false
    while ((Get-Date) -lt $SampleDeadline) {
        if ($Process.HasExited) { $CrashedDuringSample = $true; break }
        $Process.Refresh()
        $PeakWorkingSetBytes = [Math]::Max($PeakWorkingSetBytes, $Process.WorkingSet64)
        Start-Sleep -Milliseconds 500
    }
    $ElapsedSampleSeconds = [Math]::Round(((Get-Date) - $SampleStart).TotalSeconds)
    $PeakWorkingSetMiB = [Math]::Round($PeakWorkingSetBytes / 1MB, 1)

    if ($CrashedDuringSample) {
        Write-Evidence -StartupSuccess $false -PeakWorkingSetMiB $PeakWorkingSetMiB -ActualSampleWindowSeconds $ElapsedSampleSeconds -Reason "Editor process exited during the bounded sample window."
        throw "Low-spec editor startup evidence failed: process exited during the bounded sample window."
    }

    Write-Evidence -StartupSuccess $true -PeakWorkingSetMiB $PeakWorkingSetMiB -ActualSampleWindowSeconds $ElapsedSampleSeconds -Reason ""
    Write-Host "PASS: Bounded low-spec editor startup evidence recorded for exact commit $GitCommit."
    Write-Host "Peak working set during the first $ElapsedSampleSeconds s: $PeakWorkingSetMiB MiB"
    Write-Host "This is a startup/memory fact only; it does not assert visual, control-feel, or gameplay Product Reality."
}
finally {
    if ($Process -and -not $Process.HasExited) {
        try { & taskkill.exe /PID $Process.Id /T /F | Out-Null } catch { Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue }
    }
}
