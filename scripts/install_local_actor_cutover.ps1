param(
  [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
  [string]$TaskName = 'ExponentialEngine-LaptopActor',
  [string]$StateRoot = "$env:LOCALAPPDATA\ExponentialEngine\flow-state",
  [switch]$Apply
)
$ErrorActionPreference = 'Stop'
$owner = Join-Path $StateRoot 'human-mode.json'
if (-not (Test-Path -LiteralPath $owner)) { throw 'Owner-control marker missing; refusing cutover' }
$mode = Get-Content -LiteralPath $owner -Raw | ConvertFrom-Json
if ($mode.enabled -ne $false) { throw 'Owner has control; refusing cutover' }
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
$head = (& git -C $RepoRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $head -cnotmatch '^[0-9a-f]{40}$') { throw 'No exact accepted commit' }
if (& git -C $RepoRoot status --porcelain --untracked-files=no) { throw 'Development source is dirty' }
$launcher = Join-Path $RepoRoot 'scripts\launch_local_actor.ps1'
if (-not (Test-Path -LiteralPath $launcher)) { throw 'Pinned launcher missing' }
$python = (Get-Command python -ErrorAction Stop).Source
$cfg = Join-Path $StateRoot 'actor-local-promotion.json'
$backup = Join-Path $StateRoot 'actor-task-before-local-cutover.xml'
$arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$launcher`""
if (-not $Apply) {
  Write-Output "CUTOVER_PLAN_OK head=$head task=$TaskName"
  Write-Output "Original task backup: $backup"
  Write-Output "Pinned configuration: $cfg"
  exit 0
}
if (Test-Path -LiteralPath $backup) { throw 'Existing rollback backup; refusing overwrite' }
if (Test-Path -LiteralPath $cfg) { throw 'Existing promotion config; refusing overwrite' }
$xml = Export-ScheduledTask -TaskName $TaskName
[IO.File]::WriteAllText($backup, $xml)
@{enabled=$true;accepted_repo=$RepoRoot;accepted_sha=$head} | ConvertTo-Json | Set-Content -LiteralPath $cfg -Encoding UTF8
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
