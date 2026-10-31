# Shared bridge from the desktop controls and the pinned watchdog to the
# authoritative Iron God owner controller (project_orchestrator.iron_god_owner_control).
# Dot-source this file from the CURRENTLY PINNED release so the controller code
# that decides is always the accepted release's, never a stale or foreign copy.
# It never reads the retired owner marker and never grants anything itself.

function Get-IronGodPinnedRelease([string]$StateRoot) {
  $pinPath = Join-Path $StateRoot 'supervisor-pinned-release.json'
  if (-not (Test-Path -LiteralPath $pinPath -PathType Leaf)) { return $null }
  try {
    $pin = Get-Content -LiteralPath $pinPath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
  } catch { return $null }
  $repo = [string]$pin.accepted_repo
  if ([string]$pin.accepted_sha -cnotmatch '^[0-9a-f]{40}$' -or -not [IO.Path]::IsPathRooted($repo)) { return $null }
  [pscustomobject]@{ PinPath = $pinPath; Sha = [string]$pin.accepted_sha; Repo = $repo; Src = (Join-Path $repo 'src') }
}

function Get-IronGodPython {
  $preferred = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
  if (Test-Path -LiteralPath $preferred -PathType Leaf) { return $preferred }
  $found = Get-Command python.exe -ErrorAction SilentlyContinue
  if ($found) { return $found.Source }
  return $null
}

function Invoke-IronGodOwnerController {
  param([Parameter(Mandatory)][string]$StateRoot, [Parameter(Mandatory)][string[]]$Arguments, [string]$Src)
  # -Src selects the controller code of a release being installed; by default
  # the currently pinned release decides.
  $release = if ($Src) { [pscustomobject]@{ Src = $Src } } else { Get-IronGodPinnedRelease $StateRoot }
  $python = Get-IronGodPython
  if ($null -eq $release -or $null -eq $python) {
    return [pscustomobject]@{ ExitCode = 4; Result = [pscustomobject]@{ status = 'REFUSED'; reason = 'OWNER_CONTROLLER_UNAVAILABLE' }; Raw = '' }
  }
  $root = Join-Path $StateRoot 'IronGodControl'
  $previous = $env:PYTHONPATH
  $env:PYTHONPATH = $release.Src
  try {
    $output = @(& $python -m project_orchestrator.iron_god_owner_control --expect-source $release.Src --root $root @Arguments 2>$null)
    $code = $LASTEXITCODE
  } catch {
    $output = @(); $code = 4
  } finally {
    $env:PYTHONPATH = $previous
  }
  $parsed = $null
  if ($output.Count -gt 0) {
    try { $parsed = ($output | Select-Object -Last 1) | ConvertFrom-Json -ErrorAction Stop } catch { $parsed = $null }
  }
  if ($null -eq $parsed) { $parsed = [pscustomobject]@{ status = 'REFUSED'; reason = 'OWNER_CONTROLLER_OUTPUT_INVALID' } }
  [pscustomobject]@{ ExitCode = $code; Result = $parsed; Raw = ($output | Out-String) }
}

function Get-IronGodOwnerStatus([string]$StateRoot, [string]$Src) {
  $call = Invoke-IronGodOwnerController -StateRoot $StateRoot -Arguments @('owner-status') -Src $Src
  if ($call.Result.owner_status) { return [string]$call.Result.owner_status }
  return 'OWNER_CONTROL_UNVERIFIED'
}

# Compatibility stop signal for machine-wide SYSTEM guardians in
# C:\ProgramData\AzathothControl that have not been migrated (they need
# administrator rights). It is written, never read, by Iron God and grants nothing.
function Set-IronGodLegacyGuardianSignal([string]$StateRoot, [bool]$OwnerHasControl, [string]$Source) {
  $path = Join-Path $StateRoot 'human-mode.json'
  $temp = Join-Path $StateRoot ('guardian-signal-' + [guid]::NewGuid().ToString('N') + '.tmp')
  $payload = [ordered]@{ enabled = $OwnerHasControl; since = [DateTimeOffset]::UtcNow.ToString('o'); source = $Source; authority = 'NONE_COMPATIBILITY_SIGNAL_ONLY' }
  [IO.File]::WriteAllText($temp, ($payload | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
  Move-Item -LiteralPath $temp -Destination $path -Force
}
