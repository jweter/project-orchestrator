# n8n portfolio orchestration

This directory contains the **event-driven transport/orchestration layer** for the portfolio control plane. It does not replace `DoWork.txt`, repository roadmaps, exact-head preflight, GitHub Actions, required reviews, or Product Reality.

## Authority boundary

n8n may:

- receive signed GitHub events through the official GitHub Trigger node;
- reduce those events to sanitized routing metadata;
- dispatch the dedicated `orchestrator-observe.yml` workflow in `jweter/project-orchestrator`.

This v1 slice deliberately may **not**:

- merge pull requests;
- edit product source files;
- disable or weaken tests, checks, rulesets, or branch protection;
- mutate repository governance;
- create a competing schedule;
- infer that neutral, skipped, missing, pending, or unknown evidence is green.

`DoWork` remains the single autonomous portfolio scheduler. Product repositories remain authoritative for product implementation and merge gates.

## Files

- `docker-compose.yml` — local-only self-hosted n8n Community runtime.
- `.env.example` — non-secret runtime template.
- `certs/` — optional, git-ignored local CA trust extension for outbound TLS (see `certs/README.md`); needed if a host antivirus/proxy intercepts outbound HTTPS.
- `workflow-manifest.json` — machine-readable policy boundary.
- `workflows/ci-failure-router.json` — completed failed PR check-suite router.
- `workflows/knowledge-engine-impact-router.json` — Knowledge Engine main-change router.
- `../engineering/validate_n8n.py` — fail-closed static validator run by orchestrator preflight.
- `../scripts/setup-n8n.ps1` — Windows bootstrap for the local runtime.

Repository-tracked workflow exports are intentionally `active: false` and contain **no credential references**. Credential binding and activation are runtime operations.

## Local bootstrap

From the repository root in PowerShell:

```powershell
.\scripts\setup-n8n.ps1
```

The script verifies Docker, creates `n8n/.env` with a generated encryption key if needed, starts the pinned local container on `127.0.0.1:5678`, waits for the server readiness endpoint, imports the version-controlled workflows, and leaves them inactive. Stable workflow IDs make repeated setup update the same two routers.

Rerunning bootstrap replaces those routers with the committed inactive definitions, including removing runtime credential bindings. Use `docker compose up -d` from the `n8n` directory for ordinary startup after configuring credentials or activating workflows.

**If a workflow's `id` in the committed JSON ever changes** (for example, shortening it to fit n8n's internal static-data persistence limit — see `N8N_MAX_WORKFLOW_ID_LENGTH` in `src/project_orchestrator/n8n_policy.py`), re-importing creates a *new* workflow row under the new id rather than updating the old one in place. The old id's row is left behind, inactive. There is no `n8n delete:workflow` CLI command in 2.38.2 (`update:workflow --active=false`/`unpublish:workflow` only deactivate it), so after re-importing and rebinding credentials on the new id, deactivate the old one and remove it manually from the editor's Workflows list to avoid two "canonical" copies existing at once. A workflow left running under an id over the persistence limit silently never saves its GitHub Trigger webhook secrets — every delivery gets rejected with "no webhook secret is stored" even though activation appears to succeed and the GitHub-side webhook looks correctly registered.

**Every GitHub Trigger node's `webhookId` is committed explicitly** (a fixed UUID per node) precisely so activation always produces a plain `/webhook/<uuid>/webhook` path. A trigger node with no node-level `webhookId` is still routed by n8n — as `/webhook/<workflowId>/<node name>/webhook`, percent-encoding spaces in the name — but that path was observed to be unreliable (intermittent 404/500) behind a Tailscale Funnel reverse proxy in front of an exact-string nginx allowlist. If you ever remove or regenerate a trigger node without a `webhookId`, add one back before activating publicly.

## Development verification

Local preflight requires Python 3.12 with the development dependencies and Node.js 24. Runtime regression tests execute the JavaScript from the committed workflow JSON with the pinned n8n 2.38.2 GitHub Trigger body/headers/query envelope and direct payload items, and invoke the evidence-intake CLI to ensure missing or non-failure conclusions cannot produce repair candidates. These checks do not require Docker, credentials, or webhook activation.

## GitHub credential

Create the GitHub credential **inside n8n**, not in this repository. Prefer a GitHub App or other credential restricted to the exact portfolio repositories and only the permissions actually needed to create/manage GitHub webhooks and dispatch `orchestrator-observe.yml`.

Never commit GitHub tokens, app private keys, OAuth secrets, n8n credential exports, `n8n/.env`, n8n database/volume contents, or raw private Product Reality payloads.

The official GitHub Trigger requires sufficient repository privileges to create the webhook. If permission is insufficient, treat setup as incomplete; do not broaden access until the failed operation proves which permission is missing.

## Activation requires explicit security review

GitHub cannot deliver webhook events to a localhost-only n8n instance. Activating these GitHub Trigger workflows therefore requires a stable HTTPS webhook URL reachable by GitHub.

That changes the network/security boundary and is **not automated by this repository**. Before exposing n8n through a domain, reverse proxy, tunnel, VPS, or other public ingress:

1. obtain Jeremy's approval for the exposure mechanism;
2. use HTTPS;
3. keep the n8n editor/UI protected and avoid unnecessarily exposing it;
4. configure the n8n production webhook URL correctly;
5. run the n8n security audit;
6. confirm the GitHub credential is least privilege;
7. activate one router first and verify the resulting GitHub webhook before enabling the rest.

Do not activate a workflow merely because its JSON imported successfully.

## Security audit

```powershell
Push-Location .\n8n
docker compose exec n8n n8n audit
Pop-Location
```

Any high-risk finding blocks public activation until resolved.

## Current behavior

The CI Failure Router listens for `check_suite` events across all five product repositories and dispatches only completed terminal PR failures with valid head SHAs. The Knowledge Engine Impact Router listens for main-branch pushes in Core, Web, and AI and records dependency-recheck candidates. Both routes send only sanitized identifiers into the central evidence-intake workflow; neither edits or merges code.

## Next safe expansion

Once the event routers have real operational evidence, a later repair executor can consume a `REPAIR_CANDIDATE`, fetch actual failed logs, classify the first meaningful failure, and dispatch an authorized repair agent. It must preserve the existing chain:

`live evidence -> smallest safe fix -> exact-head preflight -> fresh PR CI -> policy/human merge authority`
