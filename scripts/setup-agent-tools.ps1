[CmdletBinding()]
param(
    [switch]$InstallPlaywright,
    [switch]$InstallGitHubOAuth,
    [switch]$InstallCodexPlaywright,
    [switch]$VerifyOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Test-Command {
    param([Parameter(Mandatory = $true)][string]$Name)
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments
    )
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Command exited with code $LASTEXITCODE"
    }
}

if (-not (Test-Command "claude")) {
    throw "Claude Code is not available on PATH. Install/update Claude Code before running this script."
}

Write-Host "== Claude Code =="
Invoke-Checked claude --version

if (Test-Command "node") {
    Write-Host "== Node =="
    Invoke-Checked node --version
} else {
    Write-Warning "Node is not available. Playwright MCP requires Node.js 20+."
}

if (Test-Command "npx") {
    Write-Host "== npx =="
    Invoke-Checked npx --version
} elseif ($InstallPlaywright -or $InstallCodexPlaywright) {
    throw "npx is not available. Install Node.js 20+ before installing Playwright MCP."
}

if (-not $VerifyOnly -and $InstallPlaywright) {
    Write-Host "== Installing Playwright MCP for Claude Code (user scope) =="
    $existing = (& claude mcp list 2>&1 | Out-String)
    if ($existing -match "(?im)^playwright\b") {
        Write-Host "Playwright MCP already appears to be configured; leaving it unchanged."
    } else {
        Invoke-Checked claude mcp add playwright --scope user -- cmd /c npx -y '@playwright/mcp@latest'
    }
}

if (-not $VerifyOnly -and $InstallGitHubOAuth) {
    if (-not (Test-Command "docker")) {
        throw "Docker is not available. Use the hosted GitHub MCP + PAT instructions in docs/LOCAL_AGENT_TOOLING.md instead."
    }
    Write-Host "== Installing official GitHub MCP for Claude Code using Docker OAuth =="
    $existing = (& claude mcp list 2>&1 | Out-String)
    if ($existing -match "(?im)^github\b") {
        Write-Host "GitHub MCP already appears to be configured; leaving it unchanged."
    } else {
        Invoke-Checked claude mcp add github --scope user -e GITHUB_OAUTH_CALLBACK_PORT=8085 -- docker run -i --rm -p 127.0.0.1:8085:8085 -e GITHUB_OAUTH_CALLBACK_PORT ghcr.io/github/github-mcp-server
    }
}

if (-not $VerifyOnly -and $InstallCodexPlaywright) {
    if (-not (Test-Command "codex")) {
        Write-Warning "Codex CLI is not available; skipping Codex Playwright MCP setup."
    } else {
        Write-Host "== Installing Playwright MCP for Codex =="
        Invoke-Checked codex mcp add playwright npx '@playwright/mcp@latest'
    }
}

Write-Host "== Claude MCP configuration =="
Invoke-Checked claude mcp list

if (Test-Command "codex") {
    Write-Host "== Codex MCP configuration =="
    & codex mcp list
}

Write-Host ""
Write-Host "For GitHub hosted MCP + PAT, follow docs/LOCAL_AGENT_TOOLING.md."
Write-Host "No credentials were created or written by this script."
