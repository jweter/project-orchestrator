# project-orchestrator

Portfolio-level control plane for Jeremy Weter's autonomous engineering loop.

This repository is the single portfolio brain above five product repositories:

- Knowledge Engine: `jweter/knowledge-engine-core`, `jweter/knowledge-engine-web`, `jweter/knowledge-engine-ai`
- Rocksmith Song DLC Generator: `jweter/rocksmith-cdlc-generator`
- Everward: `jweter/Project-Everward`

It does **not** own product implementation. Product repositories remain authoritative for roadmaps, architecture, tests, CI, Product Reality, and merge gates. This repository owns portfolio inventory, priority policy, promotion rules, cross-project scheduling state, and evidence-derived compounding measurement.

## Core loop

`observe -> triage -> repair -> rank -> reserve -> preflight -> PR/CI -> merge -> Product Reality -> learn -> measure -> reinvest`

The loop is fail-closed. Missing evidence is `UNKNOWN`; failed or pending gates never become green by inference.

## Local use

```bash
python -m pip install -e ".[dev]"
python engineering/preflight.py
project-orchestrator validate
project-orchestrator plan examples/portfolio-snapshot.json
GITHUB_TOKEN=... project-orchestrator collect --resolve-required-checks --output live-evidence.json
project-orchestrator compat-plan examples/knowledge-engine-change.json
```

See `DoWork.txt`, `engineering/control-plane.json`, `docs/ARCHITECTURE.md`, and `docs/ROADMAP.md`.

## Live evidence

`project-orchestrator collect` reads the canonical inventory and gathers raw live GitHub evidence for all five product repositories. Tokens are read only from `GITHUB_TOKEN` or `GH_TOKEN` and are never written into the output. Check runs are collected but remain explicitly unclassified as required/optional until the required-check discovery layer is applied.

## Required CI policy

Use `--resolve-required-checks` to enrich raw evidence with current effective branch rules, legacy branch protection, and observed control-plane mandatory-check names. Missing or stale evidence fails closed as `UNKNOWN`, `UNCERTAIN`, or `MISSING`; the tool never guesses a green gate.

## Knowledge Engine compatibility

`compat-plan` evaluates proposed Core/AI/Web shared-contract changes against the documented family boundaries and returns direct consumers, transitive consumers, and dependency-safe verification/merge actions. It never authorizes a breaking change by itself; live repository policy, preflight, CI, and each product repository's governing documents remain authoritative.

## n8n event orchestration

`n8n/` adds a self-hosted, event-driven orchestration layer without replacing GitHub Actions or the DoWork scheduler.

The committed workflows watch PR check-suite failures across the five product repositories and main-branch changes across Knowledge Engine Core/Web/AI, reduce events to sanitized identifiers, and dispatch only the read-only/sanitizing `orchestrator-observe.yml` intake workflow. They remain inactive and credential-free in Git.

The n8n policy validator is part of exact-head preflight and rejects broader GitHub mutations, embedded credentials, unsafe node types, and a competing schedule.

To prepare the local runtime on Windows:

```powershell
.\scripts\setup-n8n.ps1
```

This only creates a local `127.0.0.1` runtime and imports inactive workflows. GitHub webhook activation requires a user-approved HTTPS ingress and least-privilege runtime credential. See `n8n/README.md` and `docs/N8N_ORCHESTRATION.md`.


## Jev shadow decision fabric

The control plane includes an experimental, fail-closed Jev integration for bounded candidate judgment after deterministic eligibility has established the legal action set. It is shadow-only: it cannot execute work, merge, bypass gates, or infer Product Reality. With multiple legal candidates, `python -m project_orchestrator.jev_shadow decision.json` emits the sanitized shadow request without network activity. Real TypeSafe calls are double-gated by `--live`, `JEV_SHADOW_LIVE=1`, and an environment-only `TYPESAFE_API_KEY`. See `docs/JEV_SHADOW_DECISION_FABRIC.md`.
