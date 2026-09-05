# Local agent tooling bootstrap

This document covers the local, user-scoped setup that cannot be completed by repository commits alone. Repository code must never contain GitHub tokens or other credentials.

The preferred architecture is:

- **GitHub MCP** for structured repository/PR/Actions/security context in local coding agents.
- **Playwright MCP** for browser-level Product Reality on Web surfaces.
- Existing repository preflight/CI remains authoritative; MCP does not replace it.
- Additional free tools (Hypothesis, Semgrep CE, Trivy, OpenSSF Scorecard) are adopted only where they close a demonstrated gap and do not create unnecessary CI/runtime cost.

## Windows prerequisites

From PowerShell, verify:

```powershell
claude --version
claude doctor
node --version
npx --version
```

Playwright MCP currently requires Node.js 20 or newer. Native Windows Claude Code should invoke local `npx` MCP servers through `cmd /c`.

## 1. Install Playwright MCP for Claude Code

Install once at **user scope** so every local repository can use it:

```powershell
claude mcp add playwright --scope user -- cmd /c npx -y @playwright/mcp@latest
```

Verify:

```powershell
claude mcp list
claude mcp get playwright
```

The browser downloads automatically on first use. For Knowledge Engine Web, ask Claude to launch the local application, navigate the real Ask/Research flow, exercise the relevant user journey, and capture only sanitized Product Reality evidence.

## 2. Install official GitHub MCP for Claude Code

Use the official `github/github-mcp-server`. There are two recommended authentication approaches.

### Option A — local Docker + GitHub OAuth (preferred when Docker is already installed)

This avoids writing a PAT into repository configuration. Run:

```powershell
claude mcp add github --scope user -e GITHUB_OAUTH_CALLBACK_PORT=8085 -- docker run -i --rm -p 127.0.0.1:8085:8085 -e GITHUB_OAUTH_CALLBACK_PORT ghcr.io/github/github-mcp-server
```

The server opens a GitHub browser login on first use. Verify afterward:

```powershell
claude mcp list
claude mcp get github
```

### Option B — GitHub-hosted remote MCP + PAT

Use this when Docker is not installed. Create a least-privilege GitHub token with access only to the repositories/capabilities the agent actually needs. Do **not** paste it into a repository file.

For the current PowerShell process:

```powershell
$env:GITHUB_PAT = "YOUR_TOKEN"
$githubMcp = @{
  type = "http"
  url = "https://api.githubcopilot.com/mcp"
  headers = @{ Authorization = "Bearer $env:GITHUB_PAT" }
} | ConvertTo-Json -Compress
claude mcp add-json github $githubMcp --scope user
```

Then restart Claude Code and verify:

```powershell
claude mcp list
claude mcp get github
```

Use the least privilege that works. Expand token permissions only after a required tool fails for authorization. Rotate/revoke unused tokens.

## 3. Optional Codex MCP configuration

The same official GitHub remote MCP endpoint can be used by Codex. Keep the token in an environment variable:

```powershell
$env:GITHUB_PAT_TOKEN = "YOUR_TOKEN"
codex mcp add github --url https://api.githubcopilot.com/mcp/ --bearer-token-env-var GITHUB_PAT_TOKEN
```

Playwright MCP can be added to Codex with its standard MCP command:

```powershell
codex mcp add playwright npx "@playwright/mcp@latest"
```

Use `/mcp` in Codex to verify the servers and tools.

## 4. Repository safety rules

Never commit any of these:

- `.env`
- `.mcp.json` when it contains authentication headers/tokens
- PATs, OAuth tokens, GitHub App credentials, browser auth state
- private Product Reality screenshots/traces unless deliberately sanitized and approved for persistence
- private score/audio/source material

This repository ignores `.env` and `.mcp.json` defensively. Prefer user-scoped MCP configuration for cross-repository tools.

## 5. How DoWork uses these tools

MCP and auxiliary tools are capability amplifiers, not authorities. DoWork still follows:

1. live repository evidence;
2. repository governing documents/control plane;
3. exact-head preflight;
4. fresh PR CI;
5. Product Reality gates where applicable.

GitHub MCP can reduce the friction of observing and mutating repository state. Playwright MCP can close browser/UI feedback loops. Neither may convert missing evidence into PASS.

## 6. Other free tools

The portfolio brain tracks additional candidates in `engineering/tooling-capabilities.json`.

- **Hypothesis**: add only with useful property/invariant tests, not as an unused dependency.
- **Semgrep Community Edition**: introduce locally/advisory first where it detects a class of issues existing linters miss.
- **Trivy**: add where container/config/dependency scanning is genuinely missing; do not duplicate existing security lanes without added coverage.
- **Dependabot**: service generated PRs under ordinary DoWork state/CI rules.
- **OpenSSF Scorecard**: periodic advisory reality check; private-repository CI integration is not a default requirement.

The orchestrator may promote a tool into required preflight/CI only after its signal quality, runtime cost, and maintenance burden are demonstrated.
