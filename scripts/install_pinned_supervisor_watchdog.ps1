# Configure the existing disabled watchdog task for a verified pinned local release.
# This installer does not enable the task, launch a supervisor or erase work.
param(
  [string]$RepoRoot,
  [string]$ReceiptPath,
  [string]$TaskName = 'ExponentialEngine-ContinuousFlow-Watchdog',
  [string]$StateRoot = "$env:LOCALAPPDATA\ExponentialEngine\flow-state",
  [switch]$Apply
)
$ErrorActionPreference = 'Stop'

if (-not [IO.Path]::IsPathRooted($RepoRoot) -or -not [IO.Path]::IsPathRooted($ReceiptPath)) {
  throw 'Absolute release repository and receipt paths are required'
}
$ownerPath = Join-Path $StateRoot 'human-mode.json'
if (-not (Test-Path -LiteralPath $ownerPath -PathType Leaf)) { throw 'Owner mode unavailable' }
$owner = Get-Content -LiteralPath $ownerPath -Raw | ConvertFrom-Json
if ($owner.enabled -cne $false) { throw 'Owner has control; refusing watchdog migration' }
$watchdog = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
if ($watchdog.State -ne 'Disabled') { throw 'Watchdog must be disabled before migration' }
$actor = Get-ScheduledTask -TaskName 'ExponentialEngine-LaptopActor' -ErrorAction Stop
if ($actor.State -ne 'Disabled') { throw 'LaptopActor must remain disabled during migration' }

$instancePath = Join-Path $StateRoot 'supervisor-process-instance.json'
if (-not (Test-Path -LiteralPath $instancePath -PathType Leaf)) { throw 'Old supervisor identity unavailable' }
$instance = Get-Content -LiteralPath $instancePath -Raw | ConvertFrom-Json
$priorPid = [int]$instance.worker.pid
if ($priorPid -le 0 -or (Get-Process -Id $priorPid -ErrorAction SilentlyContinue)) {
  throw 'Cannot prove the old supervisor is stopped'
}
$live = @(Get-CimInstance Win32_Process | Where-Object {
  $_.Name -match '^python' -and $_.CommandLine -like '*continuous_flow_supervisor.py*'
})
if ($live.Count -gt 0) { throw 'Another continuous-flow supervisor is running' }

$head = (& git -C $RepoRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $head -cnotmatch '^[0-9a-f]{40}$') { throw 'Release head is not exact' }
$dirty = & git -C $RepoRoot status --porcelain --untracked-files=normal
if ($LASTEXITCODE -ne 0 -or $dirty) { throw 'Release checkout is dirty' }
$tree = (& git -C $RepoRoot rev-parse 'HEAD^{tree}').Trim()
if ($LASTEXITCODE -ne 0 -or $tree -cnotmatch '^[0-9a-f]{40}$') { throw 'Release tree unavailable' }
if (-not (Test-Path -LiteralPath $ReceiptPath -PathType Leaf)) { throw 'Release receipt missing' }
$receipt = Get-Content -LiteralPath $ReceiptPath -Raw | ConvertFrom-Json
if ($receipt.schema_version -ne 1 -or $receipt.status -cne 'VERIFIED_STAGED') { throw 'Release was not verified' }
if ($receipt.accepted_sha -cne $head -or $receipt.tree -cne $tree -or $receipt.verification.exact_head -cne $head) {
  throw 'Release receipt and exact source revision disagree'
}
if ([string]$receipt.verification.exit_code -cne '0' -or
    [string]$receipt.verification.output_sha256 -cnotmatch '^[0-9a-f]{64}$') {
  throw 'Native preflight evidence invalid'
}
if ([IO.Path]::GetFullPath($RepoRoot).TrimEnd('\') -ine
    [IO.Path]::GetFullPath([string]$receipt.release_repo).TrimEnd('\')) { throw 'Receipt checkout mismatch' }

$launcher = Join-Path $RepoRoot 'scripts\watch_pinned_continuous_flow.ps1'
if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) { throw 'Pinned watcher missing' }
$pinPath = Join-Path $StateRoot 'supervisor-pinned-release.json'
$backupPath = Join-Path $StateRoot 'watchdog-task-before-pinned-cutover.xml'
if ((Test-Path -LiteralPath $pinPath) -or (Test-Path -LiteralPath $backupPath)) {
  throw 'Existing pin/rollback record requires reconciliation'
}
if (-not $Apply) { Write-Output "PINNED_WATCHDOG_PLAN_OK $head"; exit 0 }

# Owner authority may change during Git checks; reread immediately before writing.
$owner = Get-Content -LiteralPath $ownerPath -Raw | ConvertFrom-Json
if ($owner.enabled -cne $false) { throw 'Owner took control during migration' }
$xml = Export-ScheduledTask -TaskName $TaskName -ErrorAction Stop
[IO.File]::WriteAllText($backupPath, $xml)
@{schema_version=1;enabled=$true;accepted_repo=$RepoRoot;accepted_sha=$head;receipt_path=$ReceiptPath} |
  ConvertTo-Json | Set-Content -LiteralPath $pinPath -Encoding UTF8
try {
  $check = & powershell -NoProfile -NonInteractive -File $launcher -StateRoot $StateRoot -PinPath $pinPath -VerifyOnly
  if ($LASTEXITCODE -ne 0 -or "$check" -cne "PINNED_SUPERVISOR_RELEASE_VERIFIED $head") {
    throw 'Pinned watcher validation failed'
  }
  $args = "-NoProfile -NonInteractive -WindowStyle Hidden -File `"$launcher`" -StateRoot `"$StateRoot`" -PinPath `"$pinPath`""
  $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $args -WorkingDirectory $RepoRoot
  Set-ScheduledTask -TaskName $TaskName -Action $action | Out-Null
  $installed = Get-ScheduledTask -TaskName $TaskName
  if ($installed.State -ne 'Disabled' -or $installed.Actions[0].Arguments -notlike '*watch_pinned_continuous_flow.ps1*') {
    throw 'Disabled pinned watchdog task action was not verified'
  }
  Write-Output "PINNED_WATCHDOG_CONFIGURED_NOT_STARTED $head"
} catch {
  $failure = $_
  Register-ScheduledTask -TaskName $TaskName -Xml ([IO.File]::ReadAllText($backupPath)) -Force | Out-Null
  if (Test-Path -LiteralPath $pinPath) {
    Rename-Item -LiteralPath $pinPath -NewName ('supervisor-pin-failed-' + (Get-Date -Format yyyyMMddHHmmss) + '.json')
  }
  throw "Pinned watchdog migration was rolled back: $failure"
}
