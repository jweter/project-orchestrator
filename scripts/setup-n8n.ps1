$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$N8nDir = Join-Path $RepoRoot "n8n"
$EnvPath = Join-Path $N8nDir ".env"
$EnvExamplePath = Join-Path $N8nDir ".env.example"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker is required. Install/start Docker Desktop, then run this script again."
}

docker version | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Docker is installed but the daemon is not available."
}

if (-not (Test-Path $EnvPath)) {
    Copy-Item $EnvExamplePath $EnvPath
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    }
    finally {
        $rng.Dispose()
    }
    $key = -join ($bytes | ForEach-Object { $_.ToString("x2") })
    (Get-Content $EnvPath -Raw).Replace(
        "REPLACE_WITH_A_RANDOM_64_HEX_CHARACTER_VALUE",
        $key
    ) | Set-Content $EnvPath -NoNewline
    Write-Host "Created n8n/.env with a generated encryption key."
}

# Migrate the legacy public webhook variable used by older local setups.
# Preserve the existing value, but normalize it to the variables consumed by
# docker-compose.yml so upgrades do not silently fall back to localhost.
$envText = Get-Content $EnvPath -Raw
$legacyMatch = [regex]::Match($envText, '(?m)^PORTFOLIO_WEBHOOK_URL=(.*)$')
$webhookMatch = [regex]::Match($envText, '(?m)^WEBHOOK_URL=(.*)$')
if ($legacyMatch.Success -and (-not $webhookMatch.Success -or [string]::IsNullOrWhiteSpace($webhookMatch.Groups[1].Value))) {
    $legacyUrl = $legacyMatch.Groups[1].Value.Trim()
    $normalizedLines = Get-Content $EnvPath | Where-Object {
        $_ -notmatch '^(PORTFOLIO_WEBHOOK_URL|WEBHOOK_URL|N8N_PROXY_HOPS)='
    }
    $normalizedLines += "WEBHOOK_URL=$legacyUrl"
    $normalizedLines += "N8N_PROXY_HOPS=1"
    Set-Content $EnvPath $normalizedLines
    Write-Host "Migrated legacy PORTFOLIO_WEBHOOK_URL to WEBHOOK_URL and N8N_PROXY_HOPS."
}

Push-Location $N8nDir
try {
    docker compose pull
    if ($LASTEXITCODE -ne 0) { throw "docker compose pull failed" }

    docker compose up -d
    if ($LASTEXITCODE -ne 0) { throw "docker compose up failed" }

    # A CLI version check can succeed while the server is still migrating its database.
    # Probe inside the container so a customized host port does not affect readiness.
    $readinessProbe = "fetch('http://127.0.0.1:5678/healthz/readiness', { signal: AbortSignal.timeout(3000) }).then(response => process.exit(response.ok ? 0 : 1)).catch(() => process.exit(1))"
    $ready = $false
    for ($attempt = 1; $attempt -le 60; $attempt++) {
        docker compose exec -T n8n node -e $readinessProbe | Out-Null
        if ($LASTEXITCODE -eq 0) {
            $ready = $true
            break
        }
        Start-Sleep -Seconds 2
    }
    if (-not $ready) {
        throw "n8n server/database did not become ready. Inspect 'docker compose logs n8n' before retrying."
    }

    docker compose exec -T n8n n8n import:workflow --separate --input=/workflows
    if ($LASTEXITCODE -ne 0) { throw "n8n workflow import failed" }

    Write-Host ""
    Write-Host "n8n is running locally at http://127.0.0.1:5678"
    Write-Host "Imported workflows remain inactive."
    Write-Host "Next: complete the owner account setup in the browser, create the least-privilege GitHub credential in n8n, and run:"
    Write-Host "  docker compose exec n8n n8n audit"
    Write-Host ""
    Write-Host "Do not expose or activate GitHub webhooks publicly until the HTTPS ingress/security boundary is approved."
}
finally {
    Pop-Location
}
