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

Push-Location $N8nDir
try {
    docker compose pull
    if ($LASTEXITCODE -ne 0) { throw "docker compose pull failed" }

    docker compose up -d
    if ($LASTEXITCODE -ne 0) { throw "docker compose up failed" }

    $ready = $false
    for ($attempt = 1; $attempt -le 10; $attempt++) {
        docker compose exec -T n8n n8n --version | Out-Null
        if ($LASTEXITCODE -eq 0) {
            $ready = $true
            break
        }
        Start-Sleep -Seconds 2
    }
    if (-not $ready) { throw "n8n container did not become ready for CLI import" }

    docker compose exec -T n8n n8n import:workflow --separate --input=/workflows
    if ($LASTEXITCODE -ne 0) { throw "n8n workflow import failed" }

    Write-Host ""
    Write-Host "n8n is running locally at http://127.0.0.1:5678"
    Write-Host "Imported workflows remain inactive."
    Write-Host "Next: create the least-privilege GitHub credential in n8n and run:"
    Write-Host "  docker compose exec n8n n8n audit"
    Write-Host ""
    Write-Host "Do not expose or activate GitHub webhooks publicly until the HTTPS ingress/security boundary is approved."
}
finally {
    Pop-Location
}
