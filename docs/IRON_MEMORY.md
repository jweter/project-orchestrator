# Iron Memory v1

## Purpose

Iron Memory is the portfolio's shared, local, model-neutral engineering memory layer.

It does **not** persist arbitrary conversation history and it does **not** create a second source of truth. It materializes only the existing verified, sanitized learning memory so ChatGPT, Claude, Ollama/local agents, LaptopActor, and other authorized workers can retrieve the same evidence-backed engineering lessons.

Current repository/live evidence and repository-local authority always outrank memory.

## Source of truth

Authoritative input: `engineering/state/learning-memory.json`.

The existing learning contract already requires VERIFIED evidence, content-derived IDs, secret/private-payload rejection, and idempotent deduplication. Iron Memory reuses those guarantees rather than weakening them.

## Local shared representation

Run `project-orchestrator shared-memory sync`.

Default local root: `~/.azathoth/shared-memory/`. Override it with `AZATHOTH_SHARED_MEMORY_DIR`.

Materialized files:

- `memory.json` — canonical verified lesson projection.
- `index.json` — hierarchical project -> repository -> source_type -> lesson_id tree.
- `memory.txt` — portable text representation for tools/models that cannot consume JSON directly.

Writes are atomic.

## Retrieval

Example: `project-orchestrator shared-memory query --repository jweter/project-orchestrator --term Windows --term process`.

Retrieval is deterministic and fail-closed: repository/project/source filters are exact; supplied terms must overlap verified lesson content; results include match reasons and stable lesson IDs; no model-generated summaries are written back automatically.

## Trust model

Memory lifecycle in v1 is deliberately narrow:

`OBSERVED -> VERIFIED -> MATERIALIZED -> RETRIEVED -> APPLIED -> MEASURED`

Only VERIFIED records enter the shared store.

Future promotion into reusable capabilities continues through the existing Evolution Engine / Capability Compiler path. A retrieved lesson is advisory evidence, never merge authority and never Product Reality.

## Cross-agent use

Any local agent may read `memory.json`, `index.json`, or `memory.txt` when its execution policy permits local file access.

Agents should query Iron Memory before editing a known subsystem or diagnosing a recurring failure. Applications of a lesson should continue to feed the existing learning-effectiveness measurements so ineffective or stale guidance can be detected instead of silently becoming dogma.

## Explicit non-goals

Iron Memory v1 does not:

- ingest every ChatGPT/Claude/Gemini conversation;
- store secrets or credentials;
- store raw private Product Reality;
- override repository architecture or live GitHub evidence;
- infer correctness from memory;
- make merge decisions;
- invent Product Reality;
- synchronize to a cloud service;
- introduce a paid dependency.

This keeps the useful part of IHMT-style shared memory while preserving the Foundry's evidence and authority boundaries.
