# Replacement action for the EXISTING ContinuousFlow watchdog task only.
# Installation is a separate, explicitly authorized, verified release operation.
param(
  [string]$StateRoot = "$env:LOCALAPPDATA\ExponentialEngine\flow-state",
  [string]$PinPath = "$env:LOCALAPPDATA\ExponentialEngine\flow-state\supervisor-pinned-release.json",
  [switch]$VerifyOnly
)
$ErrorActionPreference = 'Stop'

# Owner authority comes only from the authoritative owner controller. A
# supervisor may start only for a fresh owner Take the Wheel (pending its
# acknowledgment) or a grant held by a live supervisor; a restart can never
# revive a grant bound to an exited supervisor.
. (Join-Path $PSScriptRoot 'desktop-control\IronGodOwnerControl.ps1')
function Test-OwnerAllowsRun {
  try { return ((Get-IronGodOwnerStatus $StateRoot) -ceq 'AUTHORIZED') } catch { return $false }
}

if (-not (Test-OwnerAllowsRun)) { exit 0 }
if (-not (Test-Path -LiteralPath $PinPath -PathType Leaf)) { exit 3 }

try {
  $pin = Get-Content -LiteralPath $PinPath -Raw | ConvertFrom-Json
  if ($pin.schema_version -ne 1 -or $pin.enabled -cne $true) { exit 3 }
  $sha = [string]$pin.accepted_sha
  $source = [string]$pin.accepted_repo
  $receiptPath = [string]$pin.receipt_path
  if ($sha -cnotmatch '^[0-9a-f]{40}$') { exit 3 }
  if (-not [IO.Path]::IsPathRooted($source) -or -not [IO.Path]::IsPathRooted($receiptPath)) { exit 3 }
  if (-not (Test-Path -LiteralPath $receiptPath -PathType Leaf)) { exit 3 }
  if (-not (Test-Path -LiteralPath $source -PathType Container)) { exit 3 }
  $receipt = Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json
  if ($receipt.status -cne 'VERIFIED_STAGED' -or $receipt.schema_version -ne 1) { exit 3 }
  if ($receipt.accepted_sha -cne $sha -or $receipt.verification.exact_head -cne $sha) { exit 3 }
  if ([string]$receipt.verification.exit_code -cne '0') { exit 3 }
  if ([string]$receipt.verification.output_sha256 -cnotmatch '^[0-9a-f]{64}$') { exit 3 }
  if ([IO.Path]::GetFullPath($source).TrimEnd('\') -ine [IO.Path]::GetFullPath([string]$receipt.release_repo).TrimEnd('\')) { exit 3 }
  $current = (& git -C $source rev-parse HEAD).Trim()
  if ($LASTEXITCODE -ne 0 -or $current -cne $sha) { exit 3 }
  $tree = (& git -C $source rev-parse 'HEAD^{tree}').Trim()
  if ($LASTEXITCODE -ne 0 -or $tree -cne [string]$receipt.tree) { exit 3 }
  $dirty = & git -C $source status --porcelain --untracked-files=normal
  if ($LASTEXITCODE -ne 0 -or $dirty) { exit 3 }
  $runner = Join-Path $source 'scripts\continuous_flow_supervisor.py'
  if (-not (Test-Path -LiteralPath $runner -PathType Leaf)) { exit 3 }

  # Preserve the incumbent watchdog's corruption veto. Never repair ledgers here.
  $ledger = Join-Path $StateRoot 'hands-off-grant-lifecycle.json'
  if (Test-Path -LiteralPath $ledger -PathType Leaf) {
    $stream = [IO.File]::Open($ledger, 'Open', 'Read', 'ReadWrite')
    try {
      if ($stream.Length -ge 1024 -and $stream.ReadByte() -eq 0) { exit 3 }
    } finally { $stream.Dispose() }
  }

  $existing = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^python' -and $_.CommandLine -like '*continuous_flow_supervisor.py*'
  })
  if ($existing.Count -gt 0) { exit 0 }
  if ($VerifyOnly) { Write-Output "PINNED_SUPERVISOR_RELEASE_VERIFIED $sha"; exit 0 }

  # A second owner check immediately before starting. Runtime kernel singleton
  # still arbitrates authority; this watchdog cannot issue execution grants.
  if (-not (Test-OwnerAllowsRun)) { exit 0 }
  $python = (Get-Command pythonw.exe -ErrorAction Stop).Source
  $workspace = Join-Path (Split-Path -Parent $StateRoot) 'workspaces'
  $env:PYTHONPATH = "$source;$source\src"
  $args = ('"{0}" --repo-root "{1}" --workspace-root "{2}" --state-root "{3}"' -f $runner, $source, $workspace, $StateRoot)
  Start-Process -FilePath $python -ArgumentList $args -WorkingDirectory $source -WindowStyle Hidden
} catch {
  # Fail closed; errors do not authorize a fallback to obsolete origin/main.
  exit 3
}
