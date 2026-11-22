# BLACKICE release provenance: non-mutating cutover gate

**Owner objective:** replace the recurring remote-access cost only after a separately controlled, evidence-backed BLACKICE connection works for ordinary ChatGPT and scheduled DoWork, and does not compromise Azathoth or the existing Iron God execution controls.

## Reproducible source-versus-installer evidence

Run from a `project-orchestrator` checkout with Python and authenticated `gh` CLI (public GitHub **read-only GET** requests). Do **not** run the downloaded installer for this check:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python -m project_orchestrator.iron_god_blackice_release_probe
# Optionally check a known tagged release:
python -m project_orchestrator.iron_god_blackice_release_probe --tag v2.40.73
# Only if an installer has already been downloaded and independently approved for inspection:
python -m project_orchestrator.iron_god_blackice_release_probe --local-installer 'C:\path\to\Blackice-2.40.73-Setup.exe'
```

This check obtains the exact published GitHub release tag object and resolves it to the source commit; obtains upstream merged PR #24's exact merge commit; asks GitHub's compare API whether that merge is an ancestor of the release source; and optionally hashes the local installer byte-for-byte against the published GitHub asset SHA-256. The allowed GitHub API routes are fixed and bounded. No arbitrary URL, shell command, OAuth key, owner PIN, Tailscale action, installation, code execution of an installer, or endpoint publication is accepted.

**Observed 2026-11-22 result:** latest release **v2.40.73** (source `c64f9c2c2eb3bc30fd988a3682e1c07fc33ba813`) compares **behind** the PR #24 merge `9044302fae38709e0af557565a8d1b2deafb1724`. It therefore cannot be assumed to contain our merged HTTPS port 10000 support; no safe cutover on this release. PR #23, protected public endpoint setup recognition, was still open on inspection. A digest matching the vendor's public asset is provenance evidence, **not an independent malware or signer trust verdict**. Current Windows installers are reported unsigned by the BLACKICE maintainer. No detected installer block can be attributed to Norton without security logs or vendor evidence.

## Security finding from current upstream source

The BLACKICE ChatGPT connector documents **Connect with full BLACKICE access** and explicitly says there are no per-capability checkboxes. In `lib/chatgpt-oauth.mjs`, the `safeGrant` projection exposes the full `CHATGPT_OAUTH_SCOPES` list, including `files:write` and `commands:run`, to an authenticated grant. In `lib/chatgpt-tool-catalog.mjs`, the externally advertised OAuth `securitySchemes` use the compatibility `read` scope while provider-required scopes are retained internally. BLACKICE's own guide confirms that `read` is **not a read-only tool restriction**. These are source facts, not a claim of an exploit or evidence that OAuth authentication is absent.

**Consequence:** before connecting a hosted assistant to Azathoth, treat that OAuth grant as **remote command-capable**, regardless of a `read` label. The Iron God owner-control, scheduler lease and release policy are separate and cannot be delegated through BLACKICE OAuth. For an initial pilot, prefer a restricted non-production Windows account/workspace or a separate test machine with no sensitive material or production engine write permissions, plus explicit owner review. Do not expose a full-access connector with a generic shell to production merely to demonstrate a nonce/status read.

## Cutover readiness, kept deliberately separate

Source inclusion and matching package digests are **necessary but insufficient**. The following must each have its own independent dated receipt before recommending any cost-saving cutover:

1. **Release identity and host integrity:** an actual released build whose GitHub tag includes PR #24 and any necessary #23 setup patch, matching artifact digest, Windows Authenticode/malware-policy decision and version pin; obtain a safe rollback or uninstall path and preserve existing remote transport.
2. **Authorized transport:** separate, owner-approved Tailscale route ownership; keep pre-existing 443/8443 routes and Norton untouched; no anonymous public shell or silently inherited access.
3. **Actual personal ChatGPT Plus connection:** create the custom MCP plugin through the **current account's** supported UI; complete OAuth/CIMD with matching pending code; verify tool catalog and exact nonce; perform a harmless, independently observed command and read-only file action in a restricted workspace. Creating a plugin or a GET health check does not count.
4. **Permission boundaries:** verify native Windows command and filesystem privileges, credential revocation, the owner-control veto, worker leases, explicit failure receipts and no implicit code-merge authority. Refresh tools if catalog changes.
5. **Hosted scheduled Chat mode:** demonstrate the existing DoWork scheduled run really has BLACKICE's tool attached, and return a concrete task-bound command/file result; an interactive conversation is insufficient.
6. **Reliability and economics:** verify two restart/reconnection cycles, compensating recovery/rollback, realistic command/file/test throughput, and zero unexpected subscription charges. Only then propose ending the paid Remote Desktop Commander subscription, after owner approval.

This probe **always** reports `ready_for_laptop_install=false` and `ready_to_cancel_paid_remote=false` because it cannot authenticate the separate security, account, owner or scheduled-task gates. Never treat `source_gate=INCLUDES_REQUIRED_PR` as production readiness.

Related authoritative records: [BLACKICE upstream PR #24](https://github.com/MoonlightClarity/BLACKICE/pull/24), [upstream protected endpoint PR #23](https://github.com/MoonlightClarity/BLACKICE/pull/23), [closed developer reply #25](https://github.com/MoonlightClarity/BLACKICE/issues/25), and [Iron God implementation issue #747](https://github.com/jweter/project-orchestrator/issues/747). The BLACKICE owner says personal ChatGPT Plus and Free account plugin creation have been observed on other systems, which rebuts a categorical denial of plugin creation, but does not prove this specific owner account's command tool capability or Chat-mode scheduled access. See [OpenAI's current custom MCP documentation](https://developers.openai.com/api/docs/guides/custom-mcp-server) for the published setup flow; use the actual current account UI and callback receipts as the final evidence.
