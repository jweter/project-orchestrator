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


## Canon worker-slice retrieval (candidate, October 25, 2026)

Source module: `project_orchestrator.canon_worker_memory`.

`prepare_worker_memory_handoff(packet, root=...)` accepts a fully
validated canonical single-run worker packet, then reads the **existing**
verified Iron Memory projection through `query_all_memory`. It never writes
a new store and never copies unreviewed transient notes into verified memory.

The returned `canon.worker-memory.v1` context is:
- tied to the packet ID, repository and exact 40-character base SHA;
- limited to relevant VERIFIED lessons for the same repository;
- bounded to four lessons and 6,000 characters at most;
- tagged ADVISORY_ONLY, with execution, merge and Product Reality privileges
  explicitly absent;
- identified by SHA-256 for deterministic change detection (not an
  authorization signature);
- explicit about `NO_MATCH` versus `MEMORY_UNAVAILABLE`.

`render_worker_memory_handoff(packet, context)` checks the expected packet
identity and digest before returning a prompt appendix. Historical lessons are
rendered as JSON data with an explicit non-authority warning: current
repository vision, roadmap, AGENTS.md, exact Git state, authorization,
leases, native tests and Product Reality evidence always win.

**Integration boundary:** This module is a verified **source candidate**,
not a deployed supervisor capability. The next independently authorized
integration slice will bind retrieval to supervisor-approved, already-issued
canonical work packets and expose context to the worker prompt, then record
whether the advice was actually used and helped. Do not treat a successful
memory query as proof that a worker has read it or that its changes passed.

A missing, malformed or untrusted memory store produces an explicit
unavailable verdict, not fabricated evidence. New lessons still enter
the canonical store **only after independent verification**, following
the existing Evolution learning-memory contract. No process, owner-control
file, worker, scheduler or execution grant is changed by retrieval.
