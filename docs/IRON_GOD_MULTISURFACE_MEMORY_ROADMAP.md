# Project Iron God — Multi-Surface Evidence, Memory, and Learning Roadmap

**Version:** 0.1 (initial extended plan, 2026-10-19)  
**Status:** PROPOSED / NOT IMPLEMENTED BY THIS DOCUMENT  
**Scope:** Iron God / ExponentialEngine control plane, shared evidence services, and all owner-registered development repositories.  
**Primary delivery metric:** independently verified, accepted product progress — not supervisor cycles, model messages, or memory records.

## 0. Purpose, authority, and sequencing

Extend the existing local-first development system with a **typed evidence and retrieval plane** comprising:

1. **KB** — source documents, roadmap text, code documentation, and searchable engineering notes.
2. **DB** — structured executions, leases, milestones, tests, failures, schedules, and quantitative outcomes.
3. **Graph** — repository/capability/slice/file/test/incident dependencies and versioned relationships.
4. **Skills** — versioned, evidence-backed, reusable diagnostic/engineering procedures.
5. **Memory** — scoped, durable, verified observations, decisions, and learned lessons.
6. **Event journal (cross-cutting)** — append-only, idempotent source events and materialization receipts; not a sixth competing authority store.

**North star:** a supervisor encountering a known failure can retrieve the smallest sufficient, correctly scoped evidence; select a genuinely new, authorized repair rather than replaying a failed task; independently verify the outcome; and retain only evidence-backed reusable knowledge.

This roadmap is an **addendum** to, not a replacement for:
- `docs/ROADMAP.md` — portfolio-wide project goals.
- `engineering/PROJECT_HELIX_ROADMAP.md` — durable execution ledger, typed contracts, tracing, evaluation, capability compiler, and measurable learning.
- `engineering/IRON_FOREMAN_PLAN.md` — trusted supervisor generation, recovery, and exclusive process authority.
- `docs/IRON_MEMORY.md` — existing local shared-memory projection.
- `docs/VNEXT_LEARNING_ENGINE.md`, `docs/REPOSITORY_SKILLS.md` — governed learning and procedures.
- `docs/IRON_GOD_LOCAL_FIRST_STATE.md` and `docs/EXECUTION_AUTHORITY.md` — accepted local baselines and execution permissions.
- Local Iron God `IronGodLocal/Roadmap.txt` (Phase 0–10): original local-first acceptance/migration authority.

**Precedence:** current owner instruction, repository-specific product authority, `DoWork.txt`, machine-enforced control-plane authority, and existing execution policies continue to govern. This roadmap does not enable any execution mode, override an owner gate, authorize new workers, publish code, weaken protected refs, or supersede Iron Foreman / Helix entry criteria.

**Important sequencing rule:** adding sophisticated memory while the production actor still reverts patches or loops over nonproductive work is counterproductive. The initial slices address evidence correctness and the proven execution blockers. Memory improvements are optional/advisory until prerequisite reliability is demonstrated. Roadmap planning may proceed now; production cutover does not.

## 1. Snapshot, inventory baseline, and actual limitations

Evidence sampled read-only on Azathoth on 2026-10-19 evening (2026-10-20 UTC); it is a dated baseline, **not a permanently current status**:

- The original Windows supervisor/guardian was alive and continued issuing grants, but the actor Git checkout pointed to `4cb36ce9`; verified local source fixes `989a6f9` and `96508cb` existed in the main repository and were **not loaded** into that actor.
- `scripts/laptop_actor_bootstrap.py` fetched `origin/main` and ran `git reset --hard` in the actor checkout; the remote revision remained older than local development. Revisions must be reconciled before any new subsystem is production-authoritative.
- In the previous 24h snapshot, 177 grants were recorded: 135 `NO_OP`, 34 builder failures, and 8 other/deferred/in-flight outcomes; **no verified successful development grant** in that window. 171 of 177 targeted VoiceStudio.
- VoiceStudio Aider/Ollama attempts repeatedly scanned ~3,083 files, sometimes logged Windows console/repo-map warnings, and answered with acknowledgments instead of edits. These are observations, not a license to change safety settings.
- Everward emitted `WORKER_EXCEPTION` / `EXECUTION_AUTHORITY_REVOKED`; preserve this independent authorization boundary.
- Knowledge Engine Core/Web/AI had `CAPABILITY_GRAPH_DECLARATION_REQUIRED`, a separate capability-authority/readiness issue.
- A separate `iron-god-slice-path-authority-20261019` worktree was undergoing native tests. Do not overwrite, reset, or claim its changes as this roadmap's work.
- The main orchestrator checkout already had unrelated tracked/untracked work. Do not clean/reset/stage or overwrite someone else's changes.

Treat each observation as **OBSERVED_AT(timestamp)**, not an everlasting truth. Refresh the status before selecting executable work.

## 2. Existing systems to reuse (verify before building)

| Proposed surface | Existing foundations | Initial implementation choice |
|---|---|---|
| KB | repository docs, `docs/ROADMAP.md`, Knowledge Engine search capability | Deterministic scoped lexical/FTS index first; evaluate hybrid search reuse only by benchmark |
| DB | `LocalFirstState` and IronGodLocal SQLite, dispatch/grant ledgers, telemetry | Existing SQLite/WAL + read-only projections; do not create a second accepted-state or lease authority |
| Graph | capability graph JSON, portfolio registry, roadmap dependency and Git relation data | Typed edge projection into SQLite first; bounded traversal; no graph server required |
| Skills | `engineering/repository-skills.json`, `skill_extraction.py`, verified history | Reuse current advisory skill contract; scope/trigger/evidence/expiry gates |
| Memory | `engineering/state/learning-memory.json`, `shared_memory.py`, retained-learning rules | Existing sanitized VERIFIED lessons; explicit temporal/scoping extensions through migrations |
| Events | existing event journals, grant lifecycle, KPI transitions, Helix planned ledger | Reference original durable events; add a projection/outbox only when the canonical event authority is established |

**Inventory requirements:** record every store's owner, schema, scope, freshness, mutation authority, writers, readers, retained size, tests, migration policy, replay guarantees, and overlap. Unknown is `UNVERIFIED`, not `ABSENT`. Resolve duplicate concepts by adapting the existing implementation, not by launching parallel stores.

## 3. Target architecture

```text
  Registered project direction + live Git/CI/worker/runtime truth
                    | immutable source refs + typed receipts
                    v
          Canonical event / evidence record
                | idempotent projections
   +------------+-----------+-----------+-----------+----------+
   |            |           |           |           |          |
  KB           DB         Graph       Skills      Memory     Audit
 documents    states      edges      playbooks    lessons    receipts
   +------------+-----------+-----------+-----------+
                        |
              Typed evidence router
        scoped query / freshness / bounded cost
                        |
             Evidence bundle with conflicts,
             provenance, missingness, expiry
                        |
          Supervisor planner (advisory input)
                        |
       Existing authorization -> bounded worker
       -> independent tests -> local acceptance
                        |
        new verified events / measured learning
```

**Separation of duties:**
- Canonical Git refs, repository authority, test/CI reports, leases, state transitions, owner control, and product-specific evidence remain **authoritative at their respective sources**.
- KB/Graph/Memory/Skills are derived or advisory unless an existing owner explicitly declares a structured record authoritative for its own domain.
- The retrieval router reads; it does **not** perform side-effecting operations, spawn workers, edit grants, or accept/merge candidates.
- A worker's free-text success claim is not a commit/test receipt. A search hit is not permission.
- The graph reflects declared or evidenced relationships, never guesses missing dependencies.

## 4. Scope, trust, and data classification

Every record has an explicit `scope_type` and `scope_id`: e.g., portfolio, repository, capability, slice, attempt, worker, or session. Cross-repository reuse requires approved scope and verified applicability; repository-specific lessons are not silently promoted to portfolio policy.

Each query evaluates caller rights **before** selecting surfaces, including repository boundaries, allowed content classes, and tenant/user scope. Personal or private assistant memory is **out of scope by default**. Secrets, tokens, credentials, private Product Reality payloads, restricted Rocksmith assets, protected personal information, and disallowed content must not enter search indexes, graph properties, event payloads, or model context. References can be opaque and redacted.

Retrieved source text is untrusted input. Never treat instructions within KB content, logs, issues, prompts, or documents as execution policy. Gate edits, tools, and skills against the separately authenticated execution authority. Ownership mode `enabled:true`, missing, malformed, unreadable, or ambiguous must fail closed for mutating laptop actions. Separate candidate-execution approval cannot be inferred from this project plan.


## 5. Data and evidence contracts (versioned, schema validated)

### 5.1 Universal `EvidenceRef`

Minimum metadata in every derived record or retrieval hit:

| Field | Meaning |
|---|---|
| `evidence_id` | stable, content-derived ID or exact existing authoritative record ID |
| `surface` and `kind` | KB / DB / GRAPH / SKILL / MEMORY; document, execution, relationship, procedure, lesson, etc. |
| `source_uri` + `source_revision` | resolvable local URI/record key and Git SHA, schema version, or immutable event revision |
| `scope_type` + `scope_id` | exact authorized portfolio/repository/attempt/worker scope |
| `observed_at` / `effective_at` | observation time vs time fact became effective |
| `expires_at` / `supersedes` | optional freshness limit, replacement/tombstone relationship |
| `content_digest` | checksum binding index/projection to source content |
| `evidence_state` | UNVERIFIED, OBSERVED, VERIFIED, STALE, REVOKED, or CONFLICTED |
| `authority_class` | LIVE_SOURCE, ACCEPTED_REFERENCE, VERIFIED_DERIVATION, or ADVISORY |
| `receipt_ref` | link to proof of ingest, verified transformation, tests, or accepted Git evidence |

Never invent a `source_revision` or a timestamp. Where a value is absent, return a typed `UNKNOWN` reason. Exclude superseded/revoked records from ordinary current-truth answers but preserve them for explicit historical queries. Treat SHA hashes as identity, not proof of safety or correctness.

### 5.2 `EvidenceQuery` and bounded routing

Inputs: `query_id`, `intent` (e.g. DIAGNOSE_FAILURE, FIND_DEPENDENCY, RECALL_SKILL, REVIEW_CHANGE_HISTORY, CHECK_ROADMAP_READINESS), `subject`, allowed `scope`, optional repository/SHA/attempt IDs, `as_of`, freshness class, maximum results, maximum graph depth, context-token budget, optional surface hints, and requester capability.

Outputs: `EvidenceBundle` containing typed hits, source revision/digest, rank and match reason, freshness, explicit missingness/conflicts, time of retrieval, estimated context cost, and `advisory_next_actions` with supporting evidence IDs. Only the existing control-plane authorization layer may turn proposed actions into actual dispatch.

**Router rules:**
1. `RUN_STATE`, lease, exact Git head, owner-control, test-gate and permission questions use authoritative DB/live probes first; memory may explain but cannot decide.
2. `HOW_TO_FIX` and conventions: verified Skills + narrow KB + relevant Memory; include DB failure-history fingerprint if available.
3. `DEPENDENCY` / `BLAST_RADIUS`: Graph + exact source references; refuse inferred dependency traversal if an edge lacks declared/verified provenance.
4. `DID_IT_WORK`: DB outcomes + verifiable receipts + Git/test proof, never model text alone.
5. `WHY_WAS_THIS_CHOSEN`: event/decision receipts + documented requirements + time-bounded relevant evidence.
6. Resolve contradictions by source authority, freshness, and scope; if still inconsistent, produce `CONFLICTED` and escalate or re-observe rather than choosing a convenient answer.
7. Stop once sufficient evidence is found, within retrieval budgets; do not automatically fan out to all five surfaces for every request.

### 5.3 `EngineeringEvent` (reuse the canonical event journal)

Required: `event_id`, deterministic `idempotency_key`, `event_type`, `recorded_at`, `occurred_at`, `actor_generation`, `repository`, `slice_id`, `attempt_id`, `source_ref`, `source_revision`, `payload_digest`, classification/scope, and `schema_version`. Optional `previous_event_id` supports replay sequence linkage. An event can be OBSERVED without yet being independently VERIFIED.

Candidate types: `SLICE_SELECTED`, `GRANT_ISSUED`, `WORKER_STARTED`, `WORKER_EXITED`, `NO_DIFF_DETECTED`, `TEST_FAILED`, `TEST_PASSED`, `VERIFICATION_ACCEPTED`, `REPAIR_SELECTED`, `OWNER_DECISION_REQUIRED`, `LESSON_VERIFIED`, `SKILL_PROMOTED`, `SKILL_DEMOTED`.

Append-only/idempotent journal writes and monotonic sequence constraints must be enforced at the **existing store owner**. A derived index tracks last processed event/revision and maintains its own projection receipt. Do not have several worker processes each independently append uncoordinated JSON to one ledger. Do not copy the grant-lifecycle store into a second competing mutable grant table.

### 5.4 `GraphEdge` and relationship semantics

`{subject_id, predicate, object_id, repository, source_evidence_id, exact_revision, valid_from, valid_to, confidence_class, schema_version}`.

Allowed initial predicates (enumerated, versioned): `REPOSITORY_HAS_CAPABILITY`, `CAPABILITY_HAS_SLICE`, `SLICE_DEPENDS_ON`, `SLICE_MODIFIES_PATH`, `PATH_COVERED_BY_TEST`, `ATTEMPT_TARGETS_SLICE`, `ATTEMPT_FAILED_WITH`, `REPAIR_ADDRESSES_FINGERPRINT`, `SKILL_SUPPORTED_BY`, `MILESTONE_ACCEPTED_AT`, `REPOSITORY_CONSUMES`.

Default `confidence_class` is DECLARED or VERIFIED_EXTRACTED. No LLM-inferred edges may enter the authoritative graph projection. Inferences, if ever needed, are temporary `HYPOTHESIS` candidates until separately validated. Enforce per-query node/edge/depth limits (initial maximum depth 3); cycles, removed refs, and ambiguous code ownership must be handled deterministically.

### 5.5 `SkillRecord` and `MemoryFact`

Reuse the existing `engineering/repository-skills.json` contract and `learning-memory.json` schema where compatible. Extend only through explicit versioned migration:
- Skill: trigger/path/task class, applicable repo scope, explicit allowed instructions, prior verified lesson/evidence IDs, tests to run, contraindications, expiry/review date, effectiveness, and advisory/disabled state.
- Memory: statement/fingerprint, scope, provenance, verified state, effective/superseded time, retention policy, applicability limits, and last measured reuse.
- Decision memory: record **who decided, when, with which evidence, and what alternative was rejected**; distinguish OWNER_DECISION from learned heuristic and ephemeral model suggestion.
- Historical receipts remain immutable even if a lesson or skill is demoted.

A Skill describes a procedure. It never adds a tool capability, filesystem permission, execution grant, merge right, or exception to human ownership.

## 6. Persistence, replay, privacy, and recovery

**Local-first defaults:** existing SQLite with WAL, schema versioning, explicit migrations, transactionally recorded offsets, bounded FTS5 if SQLite build supports it, source-controlled docs/skills, and existing local JSON/JSONL only where already canonical. No required cloud service, vector DB, graph server, or new always-on process for v1.

**Indexing/ingestion path:**
1. Read an approved, identified source from an authorized path or validated event receipt.
2. Classify and redact according to repository/content policy **before** indexing.
3. Create stable evidence ID and digest, attach scope/revision/timestamps.
4. Emit one owner-authorized event/receipt (or reference existing canonical one).
5. Update typed projections transactionally; checkpoint ingestion cursor after durable projection.
6. Repeated input with identical ID/digest produces no duplicate; changed revision creates a linked successor.
7. On crash, rebuild projections solely from source and canonical events; never promote projection records into their own authority.

**Synchronization/freshness:** explicit refresh on Git head change, roadmap edit, accepted milestone, code-test change, verified grant completion, skill demotion, and process recovery. Live questions require a current probe or must return `STALE` / `UNKNOWN`. A cached completion cannot certify a different Git SHA.

**Storage/retention:** config-driven TTL per class; retain small typed metadata and receipts longer than raw verbose logs; cap graph fan-out, chunk sizes, vector cache, and intermediate model context. Observe Azathoth's free space and existing storage governor. Preserve required audit events and protected Git refs during compaction; do not delete unverified/dirty worktree evidence.

**Security and integrity tests:** malformed events, forged/synthetic success, prompt injection in retrieved docs, leaked secret patterns, cross-repo scope confusion, stale SHAs, revoked skills, replay after crash, SQL lock contention, concurrent readers/writers, and missing source files. One tampered source digest must invalidate its derived hits without corrupting accepted-state history.

**Off-device recovery:** only sanitized, approved, encrypted/authorized checkpoints per the existing local-first recovery policy. Do not assume an unpushed local-only accepted commit survives disk failure. Backups and schema upgrades require integrity checks, rollback rehearsal, and owner authorization where mandated.


## 7. Phased delivery plan

Each item below is PLANNED until exact-head tests and accepted deployment evidence prove otherwise. The work is subordinate to existing repository ownership and execution policies. Memory cannot become a substitute for production reliability.

### Phase MSM-0 — Reliability prerequisites (P0)

- [ ] MSM-000: Record the current actor and supervisor versions, owner-control state, outstanding workers, local accepted Git refs, authoritative roadmaps, and baseline measurements before changing execution software.
- [ ] MSM-001: Resolve the demonstrated local-first deployment mismatch: the actor bootstrap resets to an older origin/main revision instead of consuming an approved, tested local baseline. Preserve recoverability, worker state, and ownership controls.
- [ ] MSM-002: Prove repeated NO_OP handling is active in the *running* dispatcher; a committed fix that was not loaded is not sufficient.
- [ ] MSM-003: Establish a bounded worker productivity canary with a real source diff and independent native tests. Differentiate code failures from execution-authorization denials.
- [ ] MSM-004: Identify missing capability declarations and roadmap decisions across all registered repositories; record specific ROADMAP_GAP requests without blocking independent eligible work.
- [ ] MSM-005: Measure verified product slices per day, NO_OP rates, repeated failures, human interventions, and misattributed or stale outcomes.

**Exit:** approved local code survives actor refresh; the running scheduler suppresses proven exhausted retries; a permitted isolated worker demonstrates real verified code progress. Any unmet gate leaves later memory features advisory or offline-only.

### Phase MSM-1 — Source-of-truth inventory (P1)

- [ ] MSM-010: Inventory SQLite tables, JSON/JSONL journals, Git refs, Knowledge Engine retrieval, roadmap files, dependency declarations, shared memory, skills, and caches.
- [ ] MSM-011: Assign each concept one authoritative source, one writer, supported readers, schema/revision, provenance, privacy scope, retention, and migration tests.
- [ ] MSM-012: Map existing Iron Memory, Skills, Helix and Iron Foreman capabilities to the proposed surfaces; prefer extending these over creating parallel authorities.
- [ ] MSM-013: Publish a machine-readable surface registry with query interfaces, freshness requirements and evidence classifications.

**Exit:** a verified source inventory and registry describe each source without duplicating grant, lease, or accepted-baseline authority.

### Phase MSM-2 — Typed evidence and event projections (P1)

- [ ] MSM-020: Define validated versions of EvidenceRef, EngineeringEvent, and EvidenceBundle; include exact source IDs, SHA/revision, temporal semantics, evidence state, and access scope.
- [ ] MSM-021: Build a read-only adapter to existing durable execution/grant records; projections must not independently issue grants or mutate authoritative lifecycle tables.
- [ ] MSM-022: Preserve transactional ingestion offsets, digest validation, idempotency keys, source receipts, and explicit evidence conflicts.
- [ ] MSM-023: Construct replay fixtures from historical, sanitized VoiceStudio NO_OP and Everward failure records. Distinguish model claims from independent outcome evidence.
- [ ] MSM-024: Test duplicate arrivals, restart, partial writes, mismatched Git heads, corrupted records, and revoked inputs.

**Exit:** a replay of past attempts yields stable, attributable statuses and relationships with no duplicate outcomes and no change in execution permissions.

### Phase MSM-3 — KB and DB retrieval (P1)

- [ ] MSM-030: Implement authorized, bounded read-only DB queries for failures, tests, accepted milestones, exact attempts, and live-state references.
- [ ] MSM-031: Implement a scoped KB adapter for repository roadmaps, engineering docs, and verified source explanations; initially prefer exact paths, keywords, and SQLite FTS5 where available.
- [ ] MSM-032: Attach source span, digest, Git/document revision, retrieval timestamp, classification, and freshness to each hit.
- [ ] MSM-033: Handle document reindexing, source removal, stale commits, redaction, and prohibited cross-repository access.
- [ ] MSM-034: Compare evidence accuracy and retrieval latency with direct SQL/file lookup on at least 20 labelled diagnostic queries before considering extra indexing infrastructure.

**Exit:** the system can answer which acceptance criterion a historical slice targeted and how many independently recorded failures it had, with grounded KB and DB evidence.

### Phase MSM-4 — Dependency graph and impact analysis (P2)

- [ ] MSM-040: Project only declared or verified links between repositories, capabilities, slices, paths, tests, incidents, repairs and accepted milestones.
- [ ] MSM-041: Maintain versioned evidence-linked edges with validity intervals; support changes, deletions, cycles, and ambiguous ownership.
- [ ] MSM-042: Implement deterministic traversal limits for depth, breadth, graph nodes and response cost; report unknown relationships explicitly.
- [ ] MSM-043: Prove dependency and affected-test explanations on a real documented capability; compare with existing JSON graph traversal before adopting another graph database.

**Exit:** the graph produces reproducible, evidence-backed dependency paths, never hypothetical edges masquerading as execution authority.

### Phase MSM-5 — Skills, memory, and governed reuse (P2)

- [ ] MSM-050: Extend the existing verified Iron Memory and repository-skills contracts rather than storing arbitrary raw agent conversations.
- [ ] MSM-051: Record scope, source lesson IDs, verified repair evidence, relevance triggers, counterexamples, expiry, and explicit limitations on each reusable skill.
- [ ] MSM-052: Distinguish owner decisions from observed conditions, tentative diagnoses, verified lessons, and superseded information.
- [ ] MSM-053: Provide demotion, disablement, invalidation, and measurement pathways for procedures that later prove inaccurate or ineffective.
- [ ] MSM-054: Test advisory-only behavior and prevent repository-specific skills from silently acquiring cross-repository authority.

**Exit:** a diagnosed failure retrieves a relevant historical skill with verifiable source evidence; missing applicability produces no skill, and all actions still use ordinary execution gates.

### Phase MSM-6 — Multi-surface retrieval router (P2)

- [ ] MSM-060: Implement a typed, read-only router selecting the smallest sufficient set of KB, DB, Graph, Skills, and Memory according to task intent, scope and freshness.
- [ ] MSM-061: Support intents including DIAGNOSE_FAILURE, FIND_DEPENDENCIES, EXPLAIN_HISTORY, SELECT_VERIFIED_SKILL, CHECK_ROADMAP_READINESS, and PROVE_ACCEPTANCE.
- [ ] MSM-062: Combine typed evidence without flattening provenance, timestamps, revisions, authority classes or contradictory source states.
- [ ] MSM-063: Add bounded retrieval budgets for query time, context tokens, result count, and graph traversal; prefer cheap exact lookups before expensive search.
- [ ] MSM-064: Add diagnostics showing which sources were consulted, why each was relevant, where uncertainty remains, and why a proposed next step is advisory.

**Exit:** representative engineering queries achieve better correctness/context efficiency than the current method without introducing a second planner or execution controller.

### Phase MSM-7 — Real failure-diagnosis vertical slice (P1 once prerequisites pass)

- [ ] MSM-070: Reproduce a historical VoiceStudio S1 NO_OP case from immutable/sanitized fixtures with its exact source revision and acceptance criteria.
- [ ] MSM-071: Retrieve terminal grant outcomes from DB; the S1 requirements from KB; verified dependency/test relationships from Graph; applicable procedures from Skills and Memory.
- [ ] MSM-072: Correlate the repeated failure fingerprint; produce a bounded diagnosis and a different next repair task instead of blindly redispatching the same exhausted slice.
- [ ] MSM-073: Prove the ordinary scheduler refuses an unchanged exhausted attempt while independent authorized work remains eligible; keep any execution-authority veto intact.
- [ ] MSM-074: Demonstrate an authorized, isolated worker edit and independently passing tests, followed by a truthful durable outcome.
- [ ] MSM-075: Compare failure attribution, duplicated dispatch count, useful code yield, diagnostic latency and operator intervention before and after the change.

**Exit:** a complete observed-and-verified DIAGNOSE -> PLAN -> PERMITTED BUILD -> TEST -> RECORD -> NEXT cycle is repeatable and no stale/contradictory evidence becomes authority.

### Phase MSM-8 — Portfolio-wide adoption and continuous planning (P2)

- [ ] MSM-080: Enable the tested evidence router for each independently authorized product family, with repository-local documentation and verification rules preserved.
- [ ] MSM-081: Audit all registered repository roadmaps for missing next slices, acceptance criteria, dependency readiness, and ambiguous owner-only decisions. Emit explicit ROADMAP_GAP entries.
- [ ] MSM-082: Support evidence-grounded cross-repository change impact without assuming cross-repository editing permission.
- [ ] MSM-083: Add fair prioritization informed by failed-history fingerprints and verified progress, while preserving anti-starvation and existing owner/lease gates.
- [ ] MSM-084: Provide a per-repository dashboard showing ready work, missing direction, exhausted retries, verified accepted results, stale evidence and next recommended slices.

**Exit:** multiple authorized repositories receive accurate, independently scoped diagnoses and direction reports; one blocked repo does not monopolize dispatch or prevent progress elsewhere.

### Phase MSM-9 — Recovery, measurement, and rollout (P2)

- [ ] MSM-090: Prove backup/replay after interrupted indexing, supervisor restart, disk pressure, corrupt records, and source revision changes.
- [ ] MSM-091: Test wrong-repository injection, stale documentation, poisoned retrieval text, secret filtering, cross-scope exposure, revoked skill use, and falsified success receipts.
- [ ] MSM-092: Compare against the Phase MSM-0 baseline: verified product acceptances/day, repeated NO_OP rate, time-to-diagnosis, independent verification quality, operator interventions, and context/retrieval costs.
- [ ] MSM-093: Roll out read-only advisory retrieval first, then approved planner integration by canary; use measured gates and explicit rollback conditions.
- [ ] MSM-094: Produce an auditable production handoff with schema versions, tested migration/replay, provenance, disaster recovery procedure and operational owner.

**Exit:** the system demonstrates sustained, measurable improvement in verified development decisions without added execution authority, quality regressions, or unacceptable resource overhead.

## 8. Immediate first build slices and dependencies

1. **MSM-000 — Baseline audit:** read-only, bounded and independent of production deployment changes. Produce a timestamped inventory of source systems and live limitations.
2. **MSM-001 — Reliable actor revision:** align approved local-first promotion with the existing bootstrap; protect dirty worktrees and active workers, verify no rollback and prove a recovery path.
3. **MSM-002 — Retry gate deployed:** show the currently running actor actually rejects exhausted VoiceStudio grants (not merely that another checkout passes tests).
4. **MSM-010 — Memory surface registry:** reuse proven existing modules, note missing semantics, and identify authoritative ownership without changing production stores.
5. **MSM-020 / MSM-030 — Typed read-only evidence prototype:** process one sanitized historical failure fixture, then one real scoped ledger query.

**Dependency rule:** the inventory and offline schema fixtures may proceed in parallel with reliability repair. No new memory subsystem is activated as a production dispatcher, permissioning service or authoritative state owner until the preceding safety and recovery gates are passed.

## 9. Test matrix, metrics, and acceptance rules

| Gate | Required positive evidence | Required negative test |
|---|---|---|
| Provenance | Every hit resolves to a source path/record, digest, scope and exact revision | Stale, changed, missing or forged source is rejected or marked UNKNOWN |
| DB truth | Outcomes match canonical grant/verification records | Free-text agent success without diff/tests never implies completion |
| KB | Document query returns correct section and revision | Removed/superseded document cannot masquerade as current |
| Graph | Traverse declared/verified dependency edges within limits | Cycles, inferred edges and excessive fan-out cannot expand authority |
| Skills | Applicable verified procedure cites real evidence | Wrong repository, disabled or expired skill is not selected |
| Memory | Verified lessons persist, can be superseded, and have scoped reuse receipts | Unverified model statement cannot enter VERIFIED memory |
| Recovery | Projections replay deterministically after crash/reboot | Duplicate events and damaged projections do not duplicate grant/acceptance |
| Privacy | Allowed sanitized source indexes under permitted scopes | Private, secret, restricted or unapproved sources are excluded |
| Product output | Exact-head native checks and Product Reality where required | RED, pending, contradictory or unsupported checks cannot mark ACCEPTED |
| Owner control | Human-mode and execution vetoes still govern writes | Retrieval/lesson/graph record cannot grant permissions |

Baseline and endline metrics: accepted product-code milestones per 24 h/7 d, percentage of workers producing a real diff, NO_OP attempts per fingerprint, repeated dispatch after budget exhaustion, mean/median time to repair, product-family coverage, regression escapes, retrieval precision-at-k, source citation correctness, stale-hit rate, p50/p95 retrieval latency, index size, CPU/memory use, and operator interventions. Mark unavailable measurements UNKNOWN rather than estimating.

## 10. Operations, reporting, and ownership

- **Engineering owner:** existing Iron God control-plane repository and approved local-first acceptance process. Any new memory adapter remains advisory until reviewed and independently verified.
- **Repository owners:** control which roadmaps, documentation, product artifacts and relationships are authorized for indexing, learning, and cross-repository reuse.
- **Scheduled progress:** each DoWork Hourly iteration may inspect phase readiness, report new ROADMAP_GAP decisions and develop a single eligible bounded slice through available, explicitly permitted interfaces. This document does not itself create or modify scheduled tasks.
- **Reporting:** cite the exact repository, revision, changed files, native checks, verification receipts, blocked reasons, and new learning if any. Track milestones as PLANNED, IMPLEMENTED_UNVERIFIED, VERIFIED_CANDIDATE, LOCALLY_ACCEPTED, DEPLOYED, or NOT_AUTHORIZED rather than treating source-file existence as completion.
- **Runbook:** preserve unique work and authoritative stores before migrations; use reviewed forward-only migrations, backups, integrity checks, versioned rollback of derived indexes, and observed production canaries.
- **Scope exclusions:** do not build a general personal-memory collector, ingest arbitrary conversations, add always-on cloud dependencies, duplicate a supervisor, or authorize unattended launchers as part of memory work.

## 11. Final Definition of Done

Iron God can answer, with current independently checkable evidence: what should be built next, what was already accepted, what failed and why, which components depend on a change, which verified procedure is applicable, whether the result passed its real tests, and what actionable next slice is allowed. It survives recovery without losing provenance or duplicating work, distinguishes uncertain evidence from fact, and demonstrates measured improvement in accepted software outcomes across authorized repositories. **A memory index, model response, busy supervisor or passing isolated mock is not completion evidence.**
