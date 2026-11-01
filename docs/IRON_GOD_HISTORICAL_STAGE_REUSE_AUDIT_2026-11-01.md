# Iron God historical-stage reuse and integration audit — November 1, 2026

## Evidence and methodology

Read-only inspection of **Azathoth**, installed release `36fb3aa628a281b95f3ad7f02c4f9ce01f934955`, the preserved local backup repository and the authoritative local `%LOCALAPPDATA%\ExponentialEngine\IronGodLocal\Roadmap.txt`.

- The roadmap has **1,818 lines** and **72 individually named IG-xxx deliverables**, grouped into Phases 0–8, followed by 14 Phase-9 stress scenarios, Phase-10 migration, owner addenda, and three major demonstrations. The phrase “100+ stages” is appropriate for the larger implementation program, but it is not 100+ individually numbered IG IDs; don't distort the count.
- The installed release includes **84 Python files named `iron_god_*.py`** inside `src/project_orchestrator`. Sixty-ish historical Git subjects directly name IG IDs; lack of a matching commit subject does **not** prove absence, since multiple stages were delivered together or under more general commit titles.
- A conservative AST direct-import scan of **282** `src/project_orchestrator` and `scripts` modules found **10 of 84** named IG modules directly transitively reachable from five selected active-worker entrypoints, leaving **74 not found by that scan**. This is a **limited static heuristic**, not a runtime coverage report: dynamic imports, alternative CLIs, legacy workers, and shared non-`iron_god_` modules were not fully resolved. Claude's earlier 20-of-85 report likely uses a different root/module set; reconcile methodology before making activation decisions.
- Historical module/test presence proves existence, not production readiness. This audit did **not** re-run canonical tests, activate owner mode, change the pinned release, inspect GitHub secrets, or modify protected controller state.

## Original roadmap against current implementation

| Original stage | Reusable existing assets | Evidence classification | Recommended treatment |
| --- | --- | --- | --- |
| **0 — Authority and safety** (IG-001–009) | `docs/IRON_GOD_LOCAL_FIRST_V1.md`, `Roadmap.txt`, owner-control and metrics contracts | Design contracts; no discrete IG-ID Git subject required | Reconcile the 2026-11-01 roadmap standing authorization with the current owner/session controller; do not introduce a second authority store. |
| **1 — Durable local state** (IG-010–016) | `local_first_state.py`, `local_state_recovery.py`, `local_dispatch_event_claim.py`, SQLite ledgers | Grouped stage commits #563/#564, functional local state and recovery primitives; old protected controller still shadow | Audit schemas, migrations, journal, transactions, crash replay and state ownership; never point both controllers at mutable state without reconciliation. |
| **2 — Local Git authority** (IG-020–026) | `local_git_refs.py`, `iron_god_candidate_refs.py`, `iron_god_milestone_refs.py`, `iron_god_worktree_lifecycle.py`, `local_orphan_worktree_scan.py` | Per-stage commits and tests exist; multiple distinct ref conventions/stores | Select the exact refs read by `local_acceptance_refill`; prove ref migration compatibility and prevent double writers. |
| **3 — Verification and integration** (IG-030–036) | `iron_god_verification_receipt.py`, `iron_god_integration_worktree.py`, `iron_god_candidate_local_acceptance.py`, `iron_god_orchestration_caller.py` | Most primitives implemented and individually tested; local acceptance loop composed but not active product dispatcher | Canary existing `run_continuous_development_cycle`, retain exact-head/native/Product Reality and immutable candidate verification. |
| **4 — Publication** (IG-040–049) | `iron_god_publication_job.py`, `iron_god_publication_lane.py`, `iron_god_ci_watcher.py`, `iron_god_review_watcher.py`, `iron_god_post_merge_sync.py`, `iron_god_github_adapter.py` | Decision/state logic tested; IG-042 GitHub adapter is explicitly CONTRACT ONLY; newer product lane does real PR/CI/merge | Use the functioning current publisher, graft durable local-to-remote queue/deferral and sync mapping; never replace it with a nonfunctional protocol. |
| **5 — Storage and recovery** (IG-050–058) | `local_storage_pressure.py`, `iron_god_allocation_gate.py`, `iron_god_checkpoint_controller.py`, `iron_god_state_reconstruction.py`, `iron_god_recovery_reconstruction_report.py` | Monitoring, allocation, protected-work and reconstruction components; IG-055 off-device adapter CONTRACT ONLY | Reuse safe guards and existing backups; prove recovery after reboot with real accepted work, branch protection and bounded unpublished exposure. |
| **6 — Capability compiler** (IG-060–067) | `iron_god_capability_graph_parser.py`, `iron_god_roadmap_parser.py`, `iron_god_slice_decomposition.py`, `iron_god_dependency_resolution.py`, `iron_god_slicespec_generator.py` | All eight modules exist and have direct unit-test files; original IG-061 parser targets Iron God's own phase syntax, not arbitrary product prose | Coordinate with #594/#595; use existing supervisor compiler and memory; adapt product roadmaps, no duplicated planning implementation. |
| **7 — Event-driven scheduler** (IG-070–077) | `iron_god_capacity_model.py`, `iron_god_repository_fairness.py`, `iron_god_conflict_aware_scheduling.py`, `iron_god_stalled_worker_recovery.py`, `local_acceptance_refill.py` | Allocation and fairness decisions implemented; IG-074 and IG-076 not separately named commits, but related refill/lease primitives exist | Integrate a single event-driven dispatcher, one claim/lease authority; distinguish free development capacity from blocked publication slots. |
| **8 — Product Reality** (IG-080–086) | `iron_god_product_reality_adapter.py`, four family adapters, `iron_god_product_reality_receipt.py`, `iron_god_product_reality_knowledge_engine_core.py` | Interfaces, evidence classes and receipts present; four original family adapters throw `NotImplementedError`; narrow KE Core hybrid-search real adapter implemented | Build real representative domain verification per product/feature. Never treat a contract-only adapter as PASS or use green unit tests as real-product evidence. |
| **9 — Stress matrix** (14 scenarios) | `tests/test_iron_god_phase9_publication_independence_stress.py` and other protected owner/recovery tests | Scenario fixtures and offline decision-level tests exist; not proof of genuine two-product-cycle outage canary | Real GitHub transport-offline canary, two verified local accepted changes, local next-slice dispatch, outage recovery, replay, owner mode. |
| **10 — Controlled migration** (Shadow → Candidate → Accepted → Publication canary → Portfolio) | `iron_god_migration_readiness.py`, old `IronGodLocal/controller-handoff-state.json`, live product-lane release pinning | Older controller `shadow_state_only`, `candidate_execution_authorized=false`; current product lane running separately | Compare contracts and transition gradually; never enable duplicate authoritative controllers, reset protected refs, or install an unverified release. |

## Critical existing end-to-end controller: DO NOT REBUILD

`src/project_orchestrator/iron_god_orchestration_caller.py` was introduced at commit `f7c66e8` on 2026-10-18, with an explicit docstring describing itself as **the integration layer that connects already-built primitives into one repeating local-first cycle**. Its `run_continuous_development_cycle` composes:

1. `iron_god_milestone_resolution.resolve_latest_committed_milestone`: accepted local state.
2. `local_acceptance_refill.select_next_after_acceptance`: dependency-ready next slice.
3. `iron_god_allocation_gate.decide_allocation`: disk/resource allocation check.
4. `provision_candidate_worktree`: real isolated Git worktree creation.
5. Existing dispatch claim/worker binding and real candidate execution.
6. Exact-head candidate verification and `iron_god_product_reality_verification`.
7. `iron_god_candidate_local_acceptance.accept_verified_candidate_as_next_milestone`: protected ref and SQLite local acceptance.
8. Receipt reconciliation, followed by a new cycle, with an optional `handoff_planner_fn`.

That final planner callback is itself a specific integration seam: without a planner, the composed local loop stops at `NO_DEPENDENCY_READY_SLICE`. Roadmap-to-handoff authoring is therefore a genuine bridge requirement even though the execution loop exists.

**Current live lane:** `src/project_orchestrator/supervisor_runtime.py` invokes `iron_god_product_lane.advance_product_lane`, not `run_continuous_development_cycle`. The old protected `IronGodLocal/controller-handoff-state.json` still declares shadow-only mode with no candidate execution. Two mutating engines cannot be allowed to own the same repository, worker leases, or accepted-ref advancement.

## Proven compatibility risks

1. **Competing Git ref/state conventions.** `iron_god_candidate_local_acceptance.py` itself warns that `iron_god_acceptance.accept_candidate` uses a *different, disconnected ref/state system*. A plausible-looking ref adapter may corrupt the next-slice selector's authority if these are mixed.
2. **Historical contracts are not implementations.** `iron_god_github_adapter.py` and `iron_god_offdevice_checkpoint.py` explicitly provide `NotImplementedAdapter`, not usable transport/backup. The functioning newer GitHub product publisher must be reused.
3. **Product Reality varies by feature.** Four old family-level adapters explicitly throw `NotImplementedError`. The separate KE Core hybrid-search adapter executes actual domain code but covers one specific feature, not general Product Reality for all Knowledge Engine functions.
4. **Proposed planner hook vs actual drafting.** #595 generates roadmap refill requests but still needs a capable draft writer. The existing `handoff_planner_fn` in the historical caller is a real composition seam that must be checked before adding a new planner service.
5. **Offline review is not a GitHub PR.** Use review-ready branches/diffs and durable publication jobs; post-outage external CI and reviews remain necessary. Compare accepted local SHA vs GitHub squash/merge SHA and keep a verified mapping.
6. **Preserve recoverable local-only history.** #571 documents hundreds of formerly local-only commits and protected merge conflicts. Never hard-reset local accepted work to GitHub main, or blindly replay old merges without reconciliation.
7. **No new controller authority from documentation.** Approved roadmaps authorize product objectives. Runtime mutation still requires the real owner-controlled execution session, exact release identity, and existing resource/test/quality fences.

## Practical integration order (avoid parallel architectural rewrites)

**A. Inventory & contract audit (read-only).** Against the current active release *and* in-flight #595, compare accepted-ref namespaces, SQLite schema owners, lease claims, publication state, owner mode, worktree bases, model/worker manifest, and product-specific Product Reality. Mark each function: ACTIVE, AVAILABLE_REUSABLE, CONTRACT_ONLY, SUPERSEDED, DANGEROUS_DUPLICATE, or UNVERIFIED. Show a call trace, not only static counts.

**B. One roadmap authoring bridge.** Complete the #594/#595 work: owner-approved roadmap → bounded, independently testable draft → strict admission → existing dispatch. Reuse shared memory and the original capability compiler; maintain current Level-2 local actor restrictions.

**C. One local acceptance canary.** In an isolated, separately authorized release candidate, adapt the existing `run_continuous_development_cycle` and `local_acceptance_refill` to the current worker handoff **without starting a second controller**. Prove a real product A → accepted local milestone → automatic independent product B using exact-head native preflight and genuine Product Reality. Use one source of owner/lease/ref authority.

**D. Hybrid publication continuity.** Keep the working present GitHub PR/CI/review/merge path when healthy. Add durable accepted-local publication queue and bounded deferral when offline. Verify queued changes publish in order on restoration, handle divergent remote main and squash SHAs, and converge to the same tracked released ref without losing newer accepted local work.

**E. Expand cautiously.** Prove reboot/duplicate-claim/owner takeover, storage pressure, real Product Reality and worker fit for KE Core/Web/AI, Rocksmith, Everward, VoiceStudio. Do not claim 100+ stages operational just because their unit tests exist.

## Source anchors

- Original local architecture: `%LOCALAPPDATA%\ExponentialEngine\IronGodLocal\Roadmap.txt` and `docs/IRON_GOD_LOCAL_FIRST_V1.md`
- Local-first loop and proof: `iron_god_orchestration_caller.py`, `tests/test_iron_god_orchestration_caller.py`, `tests/test_iron_god_complete_loop_proof.py`
- Existing pipeline: `supervisor_runtime.py`, `iron_god_product_lane.py`, `iron_god_backlog.py`
- Original gaps analysis: `IronGodLocal/IRON_GOD_DEFINITION_OF_DONE_GAP_ANALYSIS_20261016.md`
- Original local-first mission: `docs/IRON_GOD_AUTONOMOUS_LOCAL_GIT_FACTORY.md` in historical isolated checkout
- Current roadmap refill: issue #594 and PR #595
- Historical local Git reconciliation: issue #571
- Owner-confirmed hybrid model: `docs/IRON_GOD_HYBRID_OFFLINE_CONTINUITY_2026-11-01.md` and PR #596

**Audit conclusion:** substantial recovered code exists; the missing product is a verified *single-authority* composition and safe migration. Reuse the original loop and its tests rather than build a second engine.
