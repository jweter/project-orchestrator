# Stage a clean exact-head local Iron God release and isolated actor checkout.
# Only GREEN full native preflight for this exact SHA permits staging.
# Does not modify any owner marker, accepted pin, shortcut or scheduled task.
param(
  [Parameter(Mandatory=$true)][string]$SourceRepo,
  [string]$Base = "$env:LOCALAPPDATA\ExponentialEngine",
  [switch]$Apply
)
$ErrorActionPreference='Stop'
function RunGit([string]$repo, [string[]]$gitArgs) {
  $result=@(& git -C $repo @gitArgs)
  if ($LASTEXITCODE -ne 0) { throw ('GIT_COMMAND_FAILED: '+($gitArgs -join ' ')) }
  return $result
}
function CheckRepo([string]$root,[string]$expectedHead,[string]$expectedTree) {
  $head=[string](RunGit $root @('rev-parse','HEAD'))
  $tree=[string](RunGit $root @('rev-parse','HEAD^{tree}'))
  $dirty=@(RunGit $root @('status','--porcelain','--untracked-files=normal'))
  if ($head.Trim() -cne $expectedHead -or $tree.Trim() -cne $expectedTree -or $dirty.Count -gt 0) {
    throw ('RELEASE_HEAD_TREE_OR_WORKTREE_UNVERIFIED: '+$root)
  }
}
if (-not [IO.Path]::IsPathRooted($SourceRepo) -or -not [IO.Path]::IsPathRooted($Base)) {
  throw 'ABSOLUTE_SOURCE_AND_BASE_REQUIRED'
}
$source=(Get-Item -LiteralPath $SourceRepo -ErrorAction Stop).FullName
$state=Join-Path $Base 'flow-state'
# Owner authority comes only from the authoritative owner controller,
# evaluated with the controller code of the release being staged (the
# current pin may predate it). Staging requires that no grant is live.
. (Join-Path $source 'scripts\desktop-control\IronGodOwnerControl.ps1')
function Test-OwnerHeld { (Get-IronGodOwnerStatus $state (Join-Path $source 'src')) -cne 'AUTHORIZED' }
if (-not (Test-OwnerHeld)) {
  throw 'OWNER_CONTROL_MUST_REMAIN_HELD_DURING_STAGING'
}
$tasks=@(Get-ScheduledTask -TaskName 'ExponentialEngine-ContinuousFlow-Watchdog','ExponentialEngine-LaptopActor' -ErrorAction Stop)
if ($tasks.Count -ne 2 -or @($tasks|Where-Object{$_.State -ne 'Disabled'}).Count -ne 0) {
  throw 'BOTH_ENGINE_TASKS_MUST_BE_DISABLED'
}
if ($tasks[0].Principal.UserId -ine $tasks[1].Principal.UserId -or
    -not $tasks[0].Principal.UserId) { throw 'TASK_PRINCIPAL_MISMATCH' }
$active=@(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
  $_.Name -match '^pythonw?\.exe$' -and
  $_.CommandLine -match 'continuous_flow_supervisor|laptop_actor_bootstrap'
})
if ($active.Count) { throw 'ENGINE_MUST_BE_STOPPED_TO_STAGE' }
$evidencePath=Join-Path $source 'preflight-evidence.json'
if (-not (Test-Path -LiteralPath $evidencePath -PathType Leaf)) {
  throw 'EXACT_HEAD_NATIVE_PREFLIGHT_EVIDENCE_REQUIRED'
}
$evidence=Get-Content -LiteralPath $evidencePath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
$sha=[string](RunGit $source @('rev-parse','HEAD'))
$tree=[string](RunGit $source @('rev-parse','HEAD^{tree}'))
if ($sha -cnotmatch '^[0-9a-f]{40}$' -or $tree -cnotmatch '^[0-9a-f]{40}$') {
  throw 'EXACT_LOCAL_HEAD_UNVERIFIED'
}
CheckRepo $source $sha $tree
if ($evidence.status -cne 'GREEN' -or $evidence.head_sha -cne $sha -or
    @($evidence.results).Count -lt 20 -or
    @($evidence.results|Where-Object {$_.returncode -ne 0 -or $_.timed_out -eq $true -or $_.interrupted -eq $true}).Count) {
  throw 'LATEST_EXACT_HEAD_NATIVE_PREFLIGHT_NOT_GREEN'
}
$testResult=@($evidence.results|Where-Object{$_.name -eq 'tests'})
if ($testResult.Count -ne 1 -or [string]$testResult[0].stdout -notmatch '[0-9]+ passed') {
  throw 'FULL_NATIVE_TEST_RESULT_REQUIRED'
}
$priorPin=Get-Content (Join-Path $state 'supervisor-pinned-release.json') -Raw | ConvertFrom-Json
if ($priorPin.accepted_sha -cnotmatch '^[0-9a-f]{40}$') { throw 'PREVIOUS_PIN_UNVERIFIED' }
& git -C $source merge-base --is-ancestor ([string]$priorPin.accepted_sha) $sha 2>$null
if ($LASTEXITCODE -ne 0) { throw 'NEW_LOCAL_RELEASE_MUST_DESCEND_FROM_CURRENT_PIN' }
$release=Join-Path (Join-Path $Base 'releases') $sha
$actor=Join-Path (Join-Path $Base 'runtime-actors') $sha
$receiptDir=Join-Path $Base 'release-receipts'
$receiptPath=Join-Path $receiptDir ($sha+'.json')
if ((Test-Path -LiteralPath $release) -or (Test-Path -LiteralPath $actor) -or
    (Test-Path -LiteralPath $receiptPath)) {
  throw 'STAGING_TARGET_ALREADY_EXISTS_RECONCILIATION_REQUIRED'
}
# Bind the receipt to both the JSON preflight evidence AND the collected
# stdout/stderr from its actual exact-head checks. They are different hashes.
$capturedOutput=(@($evidence.results|ForEach-Object {
  [string]$_.stdout + [string]$_.stderr
}) -join "`n")
$outputBytes=[Text.UTF8Encoding]::new($false).GetBytes($capturedOutput)
$hasher=[Security.Cryptography.SHA256]::Create()
try {
  $outputDigest=([BitConverter]::ToString($hasher.ComputeHash($outputBytes))).Replace('-','').ToLowerInvariant()
} finally { $hasher.Dispose() }
$plan=[ordered]@{
  status='VERIFIED_LOCAL_RELEASE_STAGE_PLAN'
  sha=$sha
  source_repo=$source
  release_repo=$release
  actor_repo=$actor
  receipt_path=$receiptPath
  evidence_sha256=(Get-FileHash -LiteralPath $evidencePath -Algorithm SHA256).Hash.ToLowerInvariant()
  output_sha256=$outputDigest
  previous_pin=[string]$priorPin.accepted_sha
  task_principal=[string]$tasks[0].Principal.UserId
  modifies_live_task_or_pin=$false
}
if (-not $Apply) { $plan|ConvertTo-Json -Depth 5; exit 0 }
if (-not (Test-OwnerHeld)) { throw 'OWNER_CHANGED_BEFORE_LOCAL_RELEASE_STAGE' }
foreach($folder in @((Split-Path -Parent $release),(Split-Path -Parent $actor),$receiptDir)) {
  New-Item -ItemType Directory -Force -Path $folder | Out-Null
}
& git clone --quiet --no-local --no-checkout $source $release
if ($LASTEXITCODE -ne 0) { throw 'SOURCE_RELEASE_CLONE_FAILED' }
& git -C $release checkout --quiet --detach $sha
if ($LASTEXITCODE -ne 0) { throw 'RELEASE_DETACH_FAILED' }
CheckRepo $release $sha $tree
& git clone --quiet --no-local --no-checkout $release $actor
if ($LASTEXITCODE -ne 0) { throw 'ISOLATED_ACTOR_CLONE_FAILED' }
& git -C $actor checkout --quiet --detach $sha
if ($LASTEXITCODE -ne 0) { throw 'ACTOR_DETACH_FAILED' }
CheckRepo $actor $sha $tree
$access=Join-Path $source 'scripts\set_local_runtime_access.ps1'
if (-not (Test-Path -LiteralPath $access -PathType Leaf)) {
  throw 'RUNTIME_ACCESS_PROVISIONING_SCRIPT_MISSING'
}
& powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $access -RepoRoot $release -RuntimeRoot $actor -TaskUser $tasks[0].Principal.UserId | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'RUNTIME_ACCESS_PROVISIONING_FAILED' }
CheckRepo $release $sha $tree
CheckRepo $actor $sha $tree
if (-not (Test-OwnerHeld)) { throw 'OWNER_CHANGED_DURING_STAGING_NO_RECEIPT_WRITTEN' }
$receipt=[ordered]@{
  schema_version=1;status='VERIFIED_STAGED'
  authority='STAGING_ONLY_NO_RUNTIME_DEPLOYMENT'
  accepted_sha=$sha;source_repo=$source;release_repo=$release;tree=$tree
  staged_at_utc=[DateTimeOffset]::UtcNow.ToString('o')
  verification=[ordered]@{
    command='python engineering/preflight.py'
    exact_head=$sha;exit_code='0'
    output_sha256=$plan.output_sha256
    evidence_file_sha256=$plan.evidence_sha256
    verified_at_utc=[string]$evidence.generated_at
  }
}
$tmp=Join-Path $receiptDir ($sha+'.'+[guid]::NewGuid().ToString('N')+'.tmp')
try {
  [IO.File]::WriteAllText($tmp,($receipt|ConvertTo-Json -Depth 8),[Text.UTF8Encoding]::new($false))
  [IO.File]::Move($tmp,$receiptPath)
} finally { if(Test-Path -LiteralPath $tmp){Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue} }
[pscustomobject]@{status='VERIFIED_STAGED_NOT_DEPLOYED';sha=$sha;release_repo=$release;actor_repo=$actor;receipt=$receiptPath;owner_control_unchanged=$true;tasks_unchanged=$true}|ConvertTo-Json -Depth 4
