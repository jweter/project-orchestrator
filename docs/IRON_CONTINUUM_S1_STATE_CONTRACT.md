# Iron Continuum S1 — durable engineering state and authority boundaries

Status: **source implementation / isolated Windows verification**. Do **not**
check S1 complete in [#747](https://github.com/jweter/project-orchestrator/issues/747)
until the merged version has been installed by the owner and the real resident
restart, owner-revocation and source-reconciliation canary has been witnessed.

## One authority per fact, not one new database

S1 is an integration of existing persisted sources. The system has **several
purpose-specific authoritative writers**, not a global transaction spanning
SQLite, Git, and JSON. Each source is serialized independently; cross-source
consistency is established by exact identities, immutable observations,
replay guards, and explicit refusal on disagreement. An event, cached status,
MCP message or work checkpoint must never grant permission to execute.

| Fact | Canonical source/writer | Safe durable consumer / reconciliation |
| --- | --- | --- |
| Owner approval of work | Owner-approved roadmap and versioned `engineering/state/iron-god-backlog.json` in the pinned release; admitted runtime roadmap items remain subject to `iron_god_roadmap_refill` validation | `iron_god_backlog.load_backlog`, `iron_god_roadmap_refill.admitted_items`, exact repository head and owner checks; no SQLite projection self-approves |
| Dependency graph | Approved backlog `depends_on` and bounded `iron_god_task_decomposer` plans | `iron_god_microtask_progress` is historical **negative-only replay evidence**; never grants completion |
| Backlog attempts/ownership | `iron-god-products/backlog-state.json`, serialized by `grant_lifecycle.acquire_lifecycle_lock` with `state_io.atomic_json` | `iron_god_backlog.exclusion`; absent ledger may initialize, malformed existing ledger blocks dispatch |
| Worker/owner runtime leases | `runtime_leases.RuntimeLeasePool` durable fenced lease file and authenticated `iron_god_owner_control` | Lease identity and owner decision must be refreshed before every executable operation; no historical snapshot can grant ownership |
| Immutable attempt facts / learned rules | `iron-god-products/development-memory.sqlite3`; `iron_god_development_memory.record_attempt` | WAL/FULL SQLite, transactional one-writer v1→v2 migration, schema drift and future-version refusal, immutable packet replay; rules remain advisory |
| Supervisor history | `observability/development-events.sqlite` and supervisor generation heartbeat; `iron_god_observability.record_supervisor_pass` | Unique event key, append-only triggers, read-only `iron_god_engineering_evidence.event_history`; event ledger is **not live authority** |
| Microtask partial progress | `iron-god-products/microtask-progress/<plan-revision>.json`; `iron_god_microtask_progress.record_checkpoints` | Repository-scoped kernel writer lock, atomic replacement, plan revision identity and replay veto; no other dispatcher/worker created |
| Native tests/preflight | Packet handback, independent verification and native repository preflight; exact commit SHA | Learning and evidence readers distinguish worker assertions from independent tests; baseline exception never excuses missing native gates |
| PR and CI state | Live GitHub pull request and exact-head checks/review threads | `publications.json` and local milestone queues are recoverable projections; authoritative merge comes only from GitHub |
| Accepted milestone and publication queue | `local_first_state.LocalFirstState` SQLite transaction (v2) after independently verified Git ref/native receipt | Prepared intent/commit, CAS publication transitions, journal, restart-recovery; acceptance is not inferred from worker PASS |
| Read-only seven-repo MCP evidence | Existing `iron_god_engineering_evidence.engineering_bundle` | Reads event history, attempts, frontier, microtasks, local publications and live status **without creating a database**; no dispatch, ack or grant |

## New S1 recovery hardening

1. **Development-memory migrations** now serialize schema inspection and
   migration using `BEGIN IMMEDIATE`. An existing deployed v2 database with
   `meta.schema_version='2'` and `user_version=0` remains compatible;
   startup migrates this marker to 2 in the same protected database. New/future,
   contradictory, partial or drifted schemas refuse instead of being silently
   recreated. `PRAGMA foreign_keys=ON`, `journal_mode=WAL`,
   `synchronous=FULL` and read-after-commit integrity check protect the
   existing attempt ledger. Existing facts are never reinserted on migration.
2. **Checkpoint single-writer path** reuses the existing kernel-backed
   lifecycle lock at a repository-specific key, around the entire
   read/reconcile/atomic-write sequence. Links or junctions cannot redirect the
   progress directory. Locks release on process death; snapshots are historical
   only. A failed check can never grant fresh authority.

## Verification matrix and release gates

Isolated Windows tests use disposable SQLite databases, JSON and Git fixtures,
not live engine files. Coverage:

- **Restart and replay:** `test_local_first_state.py`,
  `test_iron_god_development_memory.py`,
  `test_iron_god_microtask_progress.py`.
- **SQLite schema migration, future version, corrupt/partial schema and
  interrupted migration rollback:** `test_iron_god_s1_durability.py`.
- **Concurrent SQLite readers/writers and duplicate packet identity:**
  `test_iron_god_s1_durability.py`, `test_local_first_state.py`.
- **Checkpoint concurrent writes, historical receipt deduplication, stale
  staging:** `test_iron_god_s1_durability.py`.
- **Runtime lease fencing and restart:** `test_runtime_leases.py`.
- **Owner grant revocation, stale command/replay and release binding:**
  `test_iron_god_owner_control.py`.
- **Backlog single-writer transaction and terminal replay:**
  `test_iron_god_backlog_transaction_lock.py`.
- **Supervisor immutable event history and MCP read-only projections:**
  `test_iron_god_observability.py`,
  `test_iron_god_engineering_evidence.py`.

**Promotion separation:** source implemented → focused tests → exact-head Windows
preflight → CI and independent Codex review → guarded merge → integrated main
preflight → owner-controlled installation → live restart/revocation proof.
Until the last step, S1 is **not** marked end-to-end proven; do not claim an
offline multi-product canary, installed release, verified GitHub outcome or
autonomous owner grant based on a fixture.


## S1 follow-up: explicit version fences for other canonical writers

- **Backlog dispatch JSON** stays canonical under its existing kernel lock.
  Readers accept only the installed v1 root schema and a real `items`
  mapping; future, absent, textual, boolean or otherwise incompatible
  `schema_version` values block both worker-outcome and conflict writers.
  No checkpoint projection may reinitialize the malformed existing ledger.
- **Supervisor event SQLite** stays in the existing `observability/` database.
  Its historical unversioned (`user_version=0`) table, immutable triggers
  and unique event identity are checked inside `BEGIN IMMEDIATE`, then
  migrated transactionally to `user_version=1` before any new event is
  accepted. A future schema, column drift, invalid/absent immutable trigger,
  duplicate-identity protection loss or unrelated database refuses further
  writes rather than silently creating a compatible-looking table.
  New ledgers initialize table, triggers and first event in one transaction.
  The supervised telemetry call remains advisory and cannot affect
  authenticated owner decisions, dispatch or product acceptance.
- **Live compatibility smoke**: using SQLite's online backup API, snapshot
  Azathoth's existing unversioned supervisor event database in a temporary
  location, migrate the copy, reopen twice, and verify the identical
  event-row digest, schema v1 and SQLite integrity. The original event
  database and owner-controlled supervisor remain untouched.

Neither source repair changes runtime admission, grants, schedule, model
selection, publication authority, GitHub merge requirements or install state.
