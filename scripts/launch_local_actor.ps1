param(
  [string]$ConfigPath = "$env:LOCALAPPDATA\ExponentialEngine\flow-state\actor-local-promotion.json",
  [string]$ActorRoot = "",
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
$stagedActor = [string]$config.actor_repo
if (-not [IO.Path]::IsPathRooted($stagedActor)) { throw "Pinned actor checkout missing or not absolute" }
if ($ActorRoot -and [IO.Path]::GetFullPath($ActorRoot).TrimEnd('\') -ine [IO.Path]::GetFullPath($stagedActor).TrimEnd('\')) { throw "ActorRoot conflicts with accepted pinned actor checkout" }
$ActorRoot = $stagedActor
if ([IO.Path]::IsPathRooted($source) -and [IO.Path]::GetFullPath($source).TrimEnd('\') -ieq [IO.Path]::GetFullPath($ActorRoot).TrimEnd('\')) { throw "Actor checkout must differ from accepted source" }
if (-not [IO.Path]::IsPathRooted($source) -or $sha -cnotmatch '^[0-9a-f]{40}$') { throw 'Invalid exact-head promotion configuration' }
if (-not (Test-Path -LiteralPath (Join-Path $source '.git'))) { throw 'Local source repository missing' }
$actual = (& git -C $source rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $actual -cne $sha) { throw 'Local accepted HEAD changed' }
$dirty = & git -C $source status --porcelain --untracked-files=normal
if ($LASTEXITCODE -ne 0 -or $dirty) { throw 'Local accepted source is dirty' }
if (-not (Test-Path -LiteralPath (Join-Path $ActorRoot '.git'))) { throw "Pinned actor checkout is not a Git repository" }
$runtimeHead = & git -C $ActorRoot rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or $runtimeHead.Trim() -cne $sha) { throw 'Actor runtime does not match release' }
$runtimeDirty = & git -C $ActorRoot status --porcelain --untracked-files=normal
if ($LASTEXITCODE -ne 0 -or $runtimeDirty) { throw 'Actor runtime is dirty; preserving changes' }
$bootstrap = Join-Path $source 'scripts\laptop_actor_bootstrap.py'
if (-not (Test-Path -LiteralPath $bootstrap -PathType Leaf)) { throw 'Local bootstrap missing' }
if ($VerifyOnly) { Write-Output "LOCAL_ACTOR_PREFLIGHT_OK $sha actor=$ActorRoot"; exit 0 }
$env:IRON_GOD_ACCEPTED_REPO = $source
$env:IRON_GOD_ACCEPTED_SHA = $sha
& python $bootstrap --actor-repo $ActorRoot --queue $QueueRoot --workspace-root $WorkspaceRoot --min-idle-seconds 0 --poll-seconds 60
exit $LASTEXITCODE
