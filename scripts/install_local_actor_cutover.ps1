param(
  [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
  [string]$TaskName = 'ExponentialEngine-LaptopActor',
  [string]$StateRoot = "$env:LOCALAPPDATA\ExponentialEngine\flow-state",
  [string]$ReceiptPath,
  [switch]$Apply
)
$ErrorActionPreference = 'Stop'
$owner = Join-Path $StateRoot 'human-mode.json'
if (-not (Test-Path -LiteralPath $owner)) { throw 'Owner-control marker missing; refusing cutover' }
$mode = Get-Content -LiteralPath $owner -Raw | ConvertFrom-Json
if ($mode.enabled -ne $false) { throw 'Owner has control; refusing cutover' }
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
if ($task.State -ne 'Disabled') { throw 'Actor task must be disabled before release cutover' }
$watchdog = Get-ScheduledTask -TaskName 'ExponentialEngine-ContinuousFlow-Watchdog' -ErrorAction Stop
if ($watchdog.State -ne 'Disabled') { throw 'Continuous-flow watchdog must be disabled before actor cutover' }
$instanceFile = Join-Path $StateRoot 'supervisor-process-instance.json'
if (-not (Test-Path -LiteralPath $instanceFile -PathType Leaf)) { throw 'Supervisor ownership history unavailable' }
$instance = Get-Content -LiteralPath $instanceFile -Raw | ConvertFrom-Json
$supervisorPid = [int]$instance.worker.pid
if ($supervisorPid -le 0) { throw 'Supervisor process identity unavailable' }
if (Get-Process -Id $supervisorPid -ErrorAction SilentlyContinue) { throw 'Old supervisor is still running; cutover refused' }
$head = (& git -C $RepoRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $head -cnotmatch '^[0-9a-f]{40}$') { throw 'No exact accepted commit' }
if (& git -C $RepoRoot status --porcelain --untracked-files=normal) { throw 'Release checkout is dirty' }
if ([string]::IsNullOrWhiteSpace($ReceiptPath)) { throw 'A verified local-release receipt is required' }
if (-not (Test-Path -LiteralPath $ReceiptPath -PathType Leaf)) { throw 'Release receipt is missing' }
$receipt = Get-Content -LiteralPath $ReceiptPath -Raw | ConvertFrom-Json
if ($receipt.schema_version -ne 1 -or $receipt.status -cne 'VERIFIED_STAGED') { throw 'Release receipt is not verified' }
if ($receipt.accepted_sha -cne $head -or $receipt.verification.exact_head -cne $head) { throw 'Release receipt revision mismatch' }
if ([string]$receipt.verification.exit_code -cne '0') { throw 'Native preflight has not passed' }
if ([string]$receipt.verification.output_sha256 -cnotmatch '^[0-9a-f]{64}$') { throw 'Native preflight receipt is invalid' }
$releaseRoot = [IO.Path]::GetFullPath([string]$receipt.release_repo).TrimEnd('\')
$actualRoot = [IO.Path]::GetFullPath($RepoRoot).TrimEnd('\')
if ($releaseRoot -ine $actualRoot) { throw 'Release receipt points to a different checkout' }
$tree = (& git -C $RepoRoot rev-parse 'HEAD^{tree}').Trim()
if ($LASTEXITCODE -ne 0 -or $tree -cne [string]$receipt.tree) { throw 'Release tree differs from verified receipt' }
$launcher = Join-Path $RepoRoot 'scripts\launch_local_actor.ps1'
if (-not (Test-Path -LiteralPath $launcher)) { throw 'Pinned launcher missing' }
$python = (Get-Command python -ErrorAction Stop).Source
$cfg = Join-Path $StateRoot 'actor-local-promotion.json'
$backup = Join-Path $StateRoot 'actor-task-before-local-cutover.xml'
$arguments = "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$launcher`""
if (-not $Apply) {
  Write-Output "CUTOVER_PLAN_OK head=$head task=$TaskName"
  Write-Output "Original task backup: $backup"
  Write-Output "Pinned configuration: $cfg"
  exit 0
}
if (Test-Path -LiteralPath $backup) { throw 'Existing rollback backup; refusing overwrite' }
if (Test-Path -LiteralPath $cfg) { throw 'Existing promotion config; refusing overwrite' }
$mode = Get-Content -LiteralPath $owner -Raw | ConvertFrom-Json
if ($mode.enabled -cne $false) { throw 'Owner took control during migration' }
# Preserve the former actor checkout, including all unique untracked work.
$runtime = Join-Path (Split-Path -Parent $StateRoot) ("runtime-actors\" + $head)
if (Test-Path -LiteralPath $runtime) { throw 'Existing actor runtime requires reconciliation' }
& git clone --quiet --no-local --no-checkout $RepoRoot $runtime
if ($LASTEXITCODE -ne 0) { throw 'Actor runtime clone failed' }
& git -C $runtime checkout --quiet --detach $head
if ($LASTEXITCODE -ne 0) { throw 'Actor runtime checkout failed' }
$mode = Get-Content -LiteralPath $owner -Raw | ConvertFrom-Json
if ($mode.enabled -cne $false) { throw 'Owner took control during actor preparation' }
$xml = Export-ScheduledTask -TaskName $TaskName
[IO.File]::WriteAllText($backup, $xml)
@{enabled=$true;accepted_repo=$RepoRoot;accepted_sha=$head;actor_repo=$runtime} | ConvertTo-Json | Set-Content -LiteralPath $cfg -Encoding UTF8
try {
  & powershell -NoProfile -ExecutionPolicy Bypass -File $launcher -VerifyOnly
  if ($LASTEXITCODE -ne 0) { throw 'Pinned launcher preflight failed' }
  $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments -WorkingDirectory $RepoRoot
  Set-ScheduledTask -TaskName $TaskName -Action $action | Out-Null
  $installed = Get-ScheduledTask -TaskName $TaskName
  if ($installed.Actions[0].Execute -notmatch 'powershell' -or $installed.Actions[0].Arguments -notlike '*launch_local_actor.ps1*') { throw 'Task action verification failed' }
  Write-Output "CUTOVER_CONFIGURED head=$head task=$TaskName"
  Write-Output 'Runtime execution and health verification remain pending.'
} catch {
  $failure = $_
  Register-ScheduledTask -TaskName $TaskName -Xml ([IO.File]::ReadAllText($backup)) -Force | Out-Null
  Remove-Item -LiteralPath $cfg -ErrorAction SilentlyContinue
  throw "Cutover failed and original task was restored: $failure"
}
