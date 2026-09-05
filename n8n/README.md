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

The script verifies Docker, creates `n8n/.env` with a generated encryption key if needed, starts the pinned local container on `127.0.0.1:5678`, imports the version-controlled workflows, and leaves them inactive.

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
