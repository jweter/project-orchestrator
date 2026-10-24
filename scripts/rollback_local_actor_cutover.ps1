param(
  [string]$TaskName = 'ExponentialEngine-LaptopActor',
  [string]$StateRoot = "$env:LOCALAPPDATA\ExponentialEngine\flow-state",
  [switch]$Apply
)
$ErrorActionPreference = 'Stop'
$backup = Join-Path $StateRoot 'actor-task-before-local-cutover.xml'
$cfg = Join-Path $StateRoot 'actor-local-promotion.json'
if (-not (Test-Path -LiteralPath $backup -PathType Leaf)) { throw 'Original scheduled-task backup missing' }
[xml]$original = Get-Content -LiteralPath $backup -Raw
if (-not $original.Task.Actions) { throw 'Rollback backup is not a valid scheduled task' }
if (-not $Apply) { Write-Output "ROLLBACK_PLAN_OK task=$TaskName"; exit 0 }
Register-ScheduledTask -TaskName $TaskName -Xml ([IO.File]::ReadAllText($backup)) -Force | Out-Null
$restored = Get-ScheduledTask -TaskName $TaskName
$originalExec = [string]$original.Task.Actions.Exec.Command
if ($restored.Actions[0].Execute -ne $originalExec) { throw 'Rollback task action did not match backup' }
if (Test-Path -LiteralPath $cfg) { Rename-Item -LiteralPath $cfg -NewName 'actor-local-promotion.rolled-back.json' -ErrorAction Stop }
Write-Output "ROLLBACK_RESTORED task=$TaskName"
