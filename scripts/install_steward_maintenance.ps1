param(
    [string]$InstallRoot = "$env:LOCALAPPDATA\\ExponentialEngine\\Steward",
    [string]$DailyRebootTask = "Azathoth-Daily-Reboot",
    [string]$StartupTask = "Azathoth-Steward-Startup"
)

$ErrorActionPreference = "Stop"
$Source = Join-Path $PSScriptRoot "steward_maintenance.ps1"
if (-not (Test-Path $Source)) {
    throw "steward_maintenance.ps1 not found next to installer"
}
New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
$Installed = Join-Path $InstallRoot "steward_maintenance.ps1"
Copy-Item -LiteralPath $Source -Destination $Installed -Force

$PowerShell = (Get-Command powershell.exe).Source
$StartupFolder = [Environment]::GetFolderPath("Startup")
$StartupCmd = Join-Path $StartupFolder "Azathoth-Steward-Startup.cmd"
$startupMode = "scheduled-task"
$startupError = $null
$rebootMode = "wrapped-existing-task"
$rebootError = $null
try {
    $startupAction = New-ScheduledTaskAction `
        -Execute $PowerShell `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Installed`" -Mode StartupCheck"
    $startupTrigger = New-ScheduledTaskTrigger -AtLogOn
    $startupSettings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -MultipleInstances IgnoreNew
    Register-ScheduledTask `
        -TaskName $StartupTask `
        -Action $startupAction `
        -Trigger $startupTrigger `
        -Settings $startupSettings `
        -Description "Steward startup sanity and bounded owned-log cleanup" `
        -Force -ErrorAction Stop | Out-Null
    Remove-Item -LiteralPath $StartupCmd -Force -ErrorAction SilentlyContinue
} catch {
    $startupMode = "startup-folder-fallback"
    $startupError = $_.Exception.Message
    @(
        "@echo off",
        "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"%LOCALAPPDATA%\\ExponentialEngine\\Steward\\steward_maintenance.ps1`" -Mode StartupCheck"
    ) | Set-Content -Encoding ASCII -Path $StartupCmd
}
try {
    $rebootTask = Get-ScheduledTask -TaskName $DailyRebootTask -ErrorAction Stop
    $preRebootAction = New-ScheduledTaskAction `
        -Execute $PowerShell `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Installed`" -Mode PreReboot"
    Set-ScheduledTask `
        -TaskName $DailyRebootTask `
        -Action $preRebootAction `
        -ErrorAction Stop | Out-Null
} catch {
    $rebootMode = "existing-reboot-preserved"
    $rebootError = $_.Exception.Message
}

& $PowerShell -NoProfile -ExecutionPolicy Bypass `
    -File $Installed -Mode StartupCheck | Out-Null
$startupCheckExit = $LASTEXITCODE

$status = @{
    schema_version = 1
    installed_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    installed_script = $Installed
    startup_mode = $startupMode
    startup_error = $startupError
    startup_check_exit_code = $startupCheckExit
    reboot_mode = $rebootMode
    reboot_error = $rebootError
    existing_daily_reboot_preserved = ($rebootMode -eq "existing-reboot-preserved")
    daily_hygiene_authority = "LaptopActor run_daily_hygiene"
}
$statusPath = Join-Path $InstallRoot "install-status.json"
$status | ConvertTo-Json -Depth 4 | Set-Content -Encoding UTF8 -Path $statusPath

Write-Host "Steward maintenance installed."
Write-Host "Startup mode: $startupMode"
Write-Host "Daily reboot mode: $rebootMode"
Write-Host "Installed script: $Installed"
Write-Host "Status: $statusPath"
