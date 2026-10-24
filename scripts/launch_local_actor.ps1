param(
  [string]$ConfigPath = "$env:LOCALAPPDATA\ExponentialEngine\flow-state\actor-local-promotion.json",
  [string]$ActorRoot = "$env:LOCALAPPDATA\ExponentialEngine\actor-repo",
  [string]$QueueRoot = "$env:LOCALAPPDATA\ExponentialEngine\queue",
  [string]$WorkspaceRoot = "$env:LOCALAPPDATA\ExponentialEngine\workspaces",
  [switch]$VerifyOnly
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) { throw 'Missing local actor promotion configuration' }
$config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
if ($config.enabled -ne $true) { throw 'Local actor promotion not enabled' }
$source = [string]$config.accepted_repo
$sha = [string]$config.accepted_sha
if (-not [IO.Path]::IsPathRooted($source) -or $sha -cnotmatch '^[0-9a-f]{40}$') { throw 'Invalid exact-head promotion configuration' }
if (-not (Test-Path -LiteralPath (Join-Path $source '.git'))) { throw 'Local source repository missing' }
$actual = (& git -C $source rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $actual -cne $sha) { throw 'Local accepted HEAD changed' }
$dirty = & git -C $source status --porcelain --untracked-files=no
if ($LASTEXITCODE -ne 0 -or $dirty) { throw 'Local accepted source is dirty' }
$bootstrap = Join-Path $source 'scripts\laptop_actor_bootstrap.py'
if (-not (Test-Path -LiteralPath $bootstrap -PathType Leaf)) { throw 'Local bootstrap missing' }
if ($VerifyOnly) { Write-Output "LOCAL_ACTOR_PREFLIGHT_OK $sha"; exit 0 }
$env:IRON_GOD_ACCEPTED_REPO = $source
$env:IRON_GOD_ACCEPTED_SHA = $sha
& python $bootstrap --actor-repo $ActorRoot --queue $QueueRoot --workspace-root $WorkspaceRoot --min-idle-seconds 300 --poll-seconds 60
exit $LASTEXITCODE
