# Iron God six-part engineering evidence integration — Stage 1

**Scope:** issue #710 follow-on to PR #739. A stacked feature branch; not
an installed release, an alternate execution controller, or product acceptance.

## Reuse-first audit

Before introducing new schemas or daemons, the existing source was inspected:

- Durable state, events, lease claims, snapshots and online backup:
  `local_state.py`, `local_state_recovery.py`.
- Product backlog, dependency/exclusion/attempt caps and recovery diversion:
  `iron_god_backlog.py`, `iron_god_stall_recovery.py`.
- Local-only calibrated/model RAM routing:
  `iron_god_model_routing.py`, `iron_god_calibration.py`.
- Worker attempts and evidence-backed learning rules:
  `iron_god_development_memory.py`.
- PR state/CI/repair/merge gating:
  `iron_god_product_lane.py`, `iron_god_merge_readiness.py`.
- SQLite advisory coordination and local stdio MCP:
  `mcp_coordination.py`, `iron_god_mcp_server.py`.
- #739's immutable supervisor continuation events and diagnostic freshness:
  `iron_god_observability.py`.

**No parallel grant/lease/task authority database is created.** The new
integration `iron_god_engineering_evidence.py` is a bounded **read-only**
projection across those canonical sources. All seven registered repositories
are included; a scoped query never assumes that another repository is idle.

## Six targeted integration seams

| Component | Implemented in this stage | Remains gated / later stage |
| --- | --- | --- |
| 1. SQLite state | Reads existing immutable event ledger by supervisor generation/sequence, exact release SHA, repo and packet; historical checkpoint survives process reboot | No automatic rehydration of leases or tasks; live owner and worker identity recheck required |
| 2. MCP | New read-only `get_engineering_evidence(repository, limit)` local stdio tool; bounded, filtered, no execution or acknowledgment | No public listener/remote tunnel or privileged MCP actions |
| 3. Work continuation | Joins fresh Product lane, Canon, observed eligibility reasons, roadmap counts and deterministic recovery advice; stale eligibility yields **zero** runnable candidates | Existing owner controller and Product lane decide dispatch; this view is advisory, not a new dispatcher |
| 4. Memory & learning | Reads pre-existing `development-memory.sqlite3` attempt fingerprints and independently derived rule metadata; no prompts, replies, tests or raw lesson text indexed | Cross-repository skill promotion and new verified graph edges require explicit provenance contracts |
| 5. PR to product | Correlates existing local publication records with packet, PR number, exact head and local merge SHA; clearly separates local report from GitHub CI and native acceptance | Independent live GitHub/review, release receipt and product reality must be verified before promotion |
| 6. Local resources | Reuses fresh RAM admission and on-disk *measured* Ollama capability profiles; the canonical router provides a conservative advisory choice | Runtime memory-pressure, owner grant, native toolchain, CPU and disk must still be evaluated at dispatch |

## Behavior and safety

- Reads do **not** initialize SQLite files, acknowledge MCP messages, start a
  supervisor, load Ollama, open a port, spend cloud tokens, or grant work.
- History is always `historical_only`; an observed checkpoint is not a
  recoverable live lease or a replay authorization.
- Eligibility IDs are returned only from fresh recorded Product-lane evidence.
  No attempt limits are extended, no exhausted plan is revived, and
  `eligible_as_observed` is not an independent owner grant.
- Only approved enum-like metadata, known repository IDs, Git SHAs and opaque
  packet IDs are returned. Worker prompts/replies, diagnostics, raw PR bodies,
  and untrusted free-form rules are excluded.
- Product `MERGED`, worker `PASS` and CI `GREEN` are **distinct**:
  this projection cannot claim any of them independently; native Product
  Reality is always `UNVERIFIED` until a separate authorized verifier acts.
- The MCP tool uses only the explicit pre-existing `state_root` and fixed
  file paths. It cannot take caller-provided filesystem paths or execute code.
- Before each substantive expansion, use the native preflight, CI and
  independent review gates; do not install before owner-approved controlled release.

## Verification

```powershell
python -m pytest -q tests/test_iron_god_engineering_evidence.py tests/test_iron_god_observability.py tests/test_mcp_coordination.py tests/test_iron_god_mcp_protocol.py
python -m pytest -q tests/test_continuous_flow_supervisor.py tests/test_supervisor_throughput_runtime.py
python -m ruff check src/project_orchestrator/iron_god_engineering_evidence.py tests/test_iron_god_engineering_evidence.py
python engineering/preflight.py
```

**Remaining deliverables:** complete automatic authorized continuation,
single-writer SQLite consolidation/migration only after an ownership audit,
scoped knowledge retrieval and verified skill promotion, GitHub exact-head
receipt reconciliation, native build and product acceptance for four products,
and full resource telemetry on the installed machine. These cannot be claimed
complete merely because an evidence view exists.
