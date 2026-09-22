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
    -Description "Steward startup sanity: stale actor lock repair and bounded owned-log cleanup" `
    -Force | Out-Null

$rebootTask = Get-ScheduledTask -TaskName $DailyRebootTask -ErrorAction Stop
$preRebootAction = New-ScheduledTaskAction `
    -Execute $PowerShell `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Installed`" -Mode PreReboot"
Set-ScheduledTask -TaskName $DailyRebootTask -Action $preRebootAction | Out-Null

Write-Host "Steward maintenance installed."
Write-Host "Startup task: $StartupTask"
Write-Host "Daily reboot action updated: $DailyRebootTask"
Write-Host "Installed script: $Installed"
