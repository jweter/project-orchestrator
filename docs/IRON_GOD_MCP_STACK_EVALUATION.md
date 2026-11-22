# Iron God MCP integration stack — free, verified, local-first

**Owner objective (2026-11-22):** Replace the approximately $20/month *remote-access* dependency with a **$0 incremental recurring-fee** workflow that can deliver real work to Azathoth, while preserving Iron God's separate owner approval, Take the Wheel / Give Me Control, current worker leases, exact-head CI/review, and product preflight. Never buy a paid tier or add an always-on supervisor just to expose more MCP tools.

**Status:** evidence-based architecture and candidate shortlist; **NOT installed, enabled, a public endpoint, a release, or a verified ChatGPT-to-Azathoth connection**. See [roadmap](ROADMAP.md), [MCP coordination](IRON_GOD_MCP_COORDINATION.md), and [guarded offload pilot](IRON_GOD_CHATGPT_OFFLOAD.md). Use this decision record to *reject* redundant adapters, not blindly install all projects.

## Constraint that changes the design

An MCP server supplies tool capabilities; **the client's account and surface determine whether those tools can be called**. OpenAI's [custom server support matrix](https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt) currently places full custom MCP commands/writes on Business and Enterprise/Edu web workspaces; Pro is documented as read/fetch-limited, and Plus is not listed for custom full MCP. A healthy OAuth challenge or Tailscale server **cannot override a client entitlement**. The approved, already-connected GitHub plugin is a supported existing transport for ordinary ChatGPT work. The user's installed Windows **ChatGPT / Codex desktop app** may support *local* stdio MCP on its Codex host through [`~/.codex/config.toml`](https://learn.chatgpt.com/docs/extend/mcp), but this is **not evidence** that iPhone, web ChatGPT, or scheduled Chat-mode work sees that server. Test these surfaces separately.

Azathoth observed state: Windows 11 Home; approximately 15.7 GiB system RAM, low-memory pressure history; Norton 360 active; Tailscale HTTPS **443/8443 already owned by other applications** and **10000 unclaimed** at the time of inspection. The running Iron God mailbox is SQLite-backed and **advisory/read-only to ChatGPT**. Do not seize existing Tailscale routes or allow unbounded new MCP sidecars to defeat memory admission.

## Candidate matrix

| Option / primary upstream | Value for Iron God | Deployment position | Hard constraint or risk | Decision |
| --- | --- | --- | --- | --- |
| Existing [GitHub plugin](https://github.com) + resident GitHub polling | Already connected to hosted ChatGPT; exact-head PRs, patches, CI and review; no new remote-access fee | Hosted ChatGPT -> GitHub -> *existing local owner-controlled intake* | A PR/comment cannot authorize shell execution; check actual rights, SHA, owner grants, worker leases, product preflight | **P0: primary no-fee development transport** |
| Existing [Iron God SQLite/MCP](IRON_GOD_MCP_COORDINATION.md) | Durable status, snapshots, learning references and advisory coordination without another database/supervisor | Local stdio, registered side of existing engine | Advisory mailbox never confers execution rights; no duplicate lease database | **P0: reuse** |
| [DesktopCommanderMCP local server](https://github.com/wonderwhy-er/DesktopCommanderMCP) | Free local filesystem/terminal tools without hosted remote subscription | Local Codex / VS Code / approved local agent, not iOS | Local client only; strong file/command permissions; test actual host before claim | **P1: evaluate existing installation** |
| [Model Context Protocol filesystem reference](https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem) | Scoped read-only source/evidence queries with `--read-only` and `--confine-roots` | Optional local stdio adapter | Paths can expose sensitive files; duplicate of existing controlled file access | **P1: pilot read-only only when demonstrably needed** |
| [Microsoft Playwright MCP](https://github.com/microsoft/playwright-mcp) | Deterministic UI/browser acceptance for Knowledge Engine web apps, local dashboards | Single admitted local worker, isolated profile | Browser sessions/resources, domain scope, login credentials; **not** a general Windows controller | **P2: candidate for native UI tests** |
| [Windows Commander MCP](https://github.com/exalsch/windows-commander-mcp) | Windows UI Automation, screen/clipboard, files and PowerShell | Interactive local stdio client; separate optional capability | .NET package, desktop session; do **not** set `WINDOWS_COMMANDER_UNATTENDED=1` or allow agent to self-approve writes | **P2: controlled UI pilot, not cloud transport** |
| [BLACKICE](https://github.com/MoonlightClarity/BLACKICE) with [owner's port 10000 PR #24](https://github.com/MoonlightClarity/BLACKICE/pull/24) | Integrated Windows remote MCP bridge, OAuth/receipts, separate Tailscale port | Parallel candidate for a supported client (if one is eligible) | Unsupported hosted Plus custom command entitlement; released installer unsigned and local launch returned access denied; upstream approval/release and real OAuth canary outstanding | **Conditional** |
| [ZeroIDE-MCP](https://github.com/Danywalery/ZeroIDE-MCP) | Scoped tokens, approvals, audit, commands/files/screenshots, optional Tailscale | Backup local or other-compatible-client Windows control | Still needs a compatible client; full Windows-user RCE on granted scopes; no Plus bypass | **Conditional, do not install yet** |
| [QuickDesk](https://github.com/barry-ran/QuickDesk) | Open-source remote desktop with stdio/HTTP MCP, direct human UI control | Optional human desktop access or local MCP | Separate remote desktop stack and endpoint; does not change hosted ChatGPT plan restrictions | **Defer** |
| [MCP Proxy](https://github.com/TBXark/mcp-proxy) / [MCP transport proxy](https://github.com/punkpeye/mcp-proxy) | Aggregate/bridge stdio, SSE and Streamable HTTP when required | Private local gateway only after transport gap is demonstrated | Broadens capability exposure and auth surface; must not expose a raw Windows shell to the Internet; changes neither ChatGPT entitlement nor owner grants | **Defer until a supported client is verified** |
| [Official MCP Registry](https://github.com/modelcontextprotocol/registry) | Discover and inspect additional implementations and source provenance | Research/discovery only | Registration proves namespace ownership, not safety, support, maintenance or suitability | **Reference, not a runtime** |

See [MCP Streamable HTTP and security](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports), [MCP OAuth security](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization), and OpenAI's [desktop MCP host guidance](https://learn.chatgpt.com/docs/extend/mcp). Static GitHub stars/marketing statements are **not** verification of installed behavior.

## Deployment architecture, ranked by what it can actually prove

**Track A — no new recurring fee, code first:**

```text
Hosted ChatGPT (already-connected GitHub plugin)
   -> bounded reviewed change + tests at immutable GitHub commit
   -> existing locally authorized Iron God intake / canonical backlog
   -> independent owner grant + worker lease + exact-base/path check
   -> isolated checkout + native preflight + exact-head CI/review
   -> existing authorized merge/release + Product Reality evidence
   -> persistent SQLite learning from independently verified outcome
```

A malicious GitHub issue/comment, tool payload or MCP advisory is **never** execution authorization. No arbitrary command in a PR body; reject stale/replayed requests, unknown repos, altered patches, protected paths and simultaneous workers. Reuse the already developed immutable offload candidate importer from #766/#768 after its exact-head CI, review, merge and release; do not create a new dispatcher for this plan.

**Track B — free local direct MCP:** On the already-installed Windows ChatGPT/Codex desktop application, discover which local stdio MCP servers the **Codex host** can call. Start with the resident *read-only* Iron God snapshot; one whitelisted harmless query; check actual tool receipt. Test that a scheduled Chat-mode cloud task does **not** inherit those local tools unless independently shown; a local desktop test cannot certify cloud connectivity. Keep a separate explicit owner gate for any mutating adapter.

**Track C — optional interactive Windows control:** Test a single Windows UI controller locally, under supervised approval, only if needed for operator UI tasks that GitHub/offload cannot cover. Preserve the existing remote connection through the parallel canary. BLACKICE's 10000 code has 220 passing isolated targeted Node tests and a clean review, but is not installed/running on Azathoth. It cannot be declared a working replacement while full user-plan connectivity remains unproved.

## Mandatory acceptance gates

1. **Provenance:** Record source repository, pinned tag/commit, license, package checksum/signature, dependencies, maintenance, audit findings and uninstall plan. An unsigned executable requires explicit risk assessment; never weaken Norton just to force installation.
2. **No cost / low resource:** Confirm zero new recurring subscriptions, cloud VM or metered APIs. Limit additional services; respect the existing RAM/admission policy and cleanup/restart budget. Compare actual resources with current Iron God baseline.
3. **Security and authority:** Run local-first; bind loopback or authenticated private tailnet; pin allowlisted repos/directories/capabilities; separate read/write; require independent owner session, worker lease, CI/review and audit. Tokens in DPAPI/OS credentials, never GitHub comments or chat. No blanket unattended write grants, wildcard shell tools, or default Funnel exposure.
4. **Real client capability:** Verify the exact ChatGPT **web**, **iOS**, **Windows Codex host**, and **scheduled Chat-mode** surfaces separately. Require a nonce/receipt from the actual calling client, not only an MCP initialize or test fixture.
5. **Regression and cutover:** Validate fail-closed unknown routes, revocation, power loss/reboot, retries, duplicate PR prevention, preserving 443/8443, actual file/command canary, and owner Give Me Control veto. Cut over only after every *required* workflow succeeds; then and only then recommend cancelling the existing paid remote connector.

## Disposition (as of this review)

**Implement Track A first.** It directly serves the owner's highest-value objective—ship verified repository changes—using existing GitHub connectivity, no added Windows remote agent, and existing owner gates. Pilot Track B in the installed desktop Codex host in parallel, read-only. Do not deploy multiple redundant MCP controllers, proxy services or remote desktops. Keep BLACKICE #24 and [Plus compatibility issue #25](https://github.com/MoonlightClarity/BLACKICE/issues/25) open as conditional remote options. Report actual merge/installed/client-canary evidence, never just a plan or passing synthetic tests.
