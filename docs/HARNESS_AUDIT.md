# Harness Audit and Context Budget

## Purpose

The harness audit is the Phase B observability layer from `docs/ECC_GAP_ANALYSIS.md`. It measures repository-resident agent instructions, skills, agent definitions, MCP descriptors, and lifecycle hooks without changing them.

It is deliberately **read-only**. It does not decide product direction, does not infer Product Reality, does not delete or rewrite agent configuration, and does not turn context size into merge authority.

Repository-native policy remains authoritative.

## What it detects

The audit reports:

- missing required harness/authority files for repositories that are actually mounted;
- explicitly conflicting authority metadata in the harness inventory;
- exact duplicate context surfaces;
- always-on, task-triggered, and tool-descriptor context byte totals;
- an approximate token count using the configured characters-per-token heuristic;
- single-surface and aggregate context-budget overages;
- literal secret-like values inside repository MCP descriptor JSON;
- unmounted repositories as `UNVERIFIED` rather than guessing their state;
- invalid or mutating policy settings as fail-closed errors.

It does **not** attempt semantic natural-language conflict detection. That would make the auditor itself an unreliable language-model judge. The first version uses only deterministic evidence.

## Policy

`engineering/harness-audit.json` is the versioned policy and inventory.

Current budget thresholds are deliberately advisory:

- always-on repository context: 64 KiB;
- task-triggered/tool context: 256 KiB;
- one context surface: 32 KiB;
- approximate token conversion: 4 characters/token.

Budget overages are `WARNING`, not merge authority. They are intended to identify expensive context surfaces worth investigating. A future threshold may become a hard gate only after measured evidence shows that it improves quality/throughput without creating false pressure to delete useful context.

Safety violations such as a missing required surface in a mounted repo, authority overlap, a mutating audit policy, or literal secret-like MCP credentials are `ERROR` and make the audit RED.

## Status semantics

- `GREEN` — every declared repository supplied to this run was audited and no findings were produced.
- `YELLOW` — advisory findings or deliberately unmounted repositories remain. This is expected in CI because product repositories are not cloned into the orchestrator job.
- `RED` — deterministic safety/configuration failure.

Canonical preflight fails only on RED. A YELLOW harness report remains visible evidence but does not block an otherwise valid orchestrator change.

## Local use

Audit the orchestrator checkout only:

```bash
project-orchestrator-harness-audit
```

Audit a full local portfolio workspace:

```bash
project-orchestrator-harness-audit \
  --repo jweter/project-orchestrator=/path/to/project-orchestrator \
  --repo jweter/knowledge-engine-core=/path/to/knowledge-engine-core \
  --repo jweter/knowledge-engine-web=/path/to/knowledge-engine-web \
  --repo jweter/knowledge-engine-ai=/path/to/knowledge-engine-ai \
  --repo jweter/rocksmith-cdlc-generator=/path/to/rocksmith-cdlc-generator \
  --repo jweter/Project-Everward=/path/to/Project-Everward
```

The output is machine-readable JSON so DoWork/local unattended workers can retain measurements and later correlate them with existing KPI telemetry.

## Context economics

The report separates:

- `always_on_bytes` — instructions loaded as persistent repository context;
- `task_triggered_bytes` — skills/agents/instructions that should be loaded only for relevant work;
- `total_context_bytes`;
- `estimated_tokens`.

The token estimate is intentionally approximate. It is useful for relative trend detection, not billing, model-limit calculations, or capability claims.

Exact duplicate context is reported because duplicate rules consume context while adding no information. Duplicate detection is content-hash based and deterministic.

## Security boundary

The auditor only checks MCP JSON for literal values under secret-like keys such as `token`, `password`, `secret`, or `api_key`. Environment references and explicit placeholder/example values are allowed.

This is not a replacement for a security scanner. It is a harness-specific guard against one high-cost mistake: committing credentials into agent/tool configuration.

## Next evidence gate

Phase B is successful only if repeated use identifies a real source of context, security, or execution friction and a remediation measurably improves the engineering loop without product regression. If it does not find material problems, this lane should stop expanding rather than creating governance for its own sake.
