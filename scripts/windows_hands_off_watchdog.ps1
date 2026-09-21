param([int]$MaxReceiptAgeSeconds = 300)
$Repo = "C:\Users\jerem\Documents\GitHub\project-orchestrator"
$State = Join-Path $Repo ".hands-off-state"
$Receipt = Join-Path $State "persistent-supervisor-launch.json"
$Log = Join-Path $State "windows-boot-watchdog.log"
function Log($m) { Add-Content -Path $Log -Value ("[{0:o}] {1}" -f (Get-Date),$m) }
$healthy=$false
try {
  $r=Get-Content $Receipt -Raw | ConvertFrom-Json
  $stamp=[DateTimeOffset]::Parse($r.verified_at_utc)
  $age=([DateTimeOffset]::UtcNow-$stamp).TotalSeconds
  $healthy=($r.status -in @("PASS","EXISTING_ACTIVE") -and $age -le $MaxReceiptAgeSeconds)
} catch { $healthy=$false }
if ($healthy) { Log "healthy launch receipt"; exit 0 }
Log "receipt absent/stale; invoking idempotent boot wrapper"
& (Join-Path $Repo "scripts\windows_hands_off_boot.bat")
exit $LASTEXITCODE
