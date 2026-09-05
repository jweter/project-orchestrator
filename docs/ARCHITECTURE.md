# Architecture

## Purpose

The project orchestrator is a portfolio control plane, not a sixth product implementation repository.

Each product repository owns its own product truth: roadmap, architecture, code, tests, CI, Product Reality, licensing/provenance boundaries, and merge requirements. The orchestrator owns only portfolio-level coordination.

## Authority graph

```text
DoWork scheduler
    |
    v
project-orchestrator
    |
    +-- Knowledge Engine family
    |    +-- core
    |    +-- web
    |    +-- ai
    |
    +-- Rocksmith
    |
    +-- Everward
```

Knowledge Engine is one coordinated dependency family. Rocksmith and Everward are isolated standalone projects.

## Deterministic decision kernel

`project_orchestrator.decision` takes an evidence snapshot and emits the highest-priority authorized actions. It does not fetch GitHub itself and therefore cannot silently invent live state. Collection and mutation remain explicit orchestration responsibilities.

Priority order: P0 critical security/data loss/correctness; P1 repair failing PRs; P2 merge objectively GREEN PRs; P3 resolve conflicts/reviews/uncertainty; P4 finish/wait existing work; P5 authorized roadmap slices; P6 nonblocking debt.

## Fail-closed semantics

Missing required-check evidence is UNCERTAIN. Pending checks are PENDING. Human Product Reality evidence is never inferred. Readiness percentages are prohibited.

## Exponential-development hypothesis

The repository does not assert exponential development. It supplies the control structure needed to measure whether engineering capacity compounds over time. Claims require sustained evidence across comparable windows: rising verified throughput and autonomous completion, falling cycle time/repeat failures/human intervention, and increasing dependency unlocks without quality regression.

## Security

No repository secrets, credentials, private Product Reality payloads, copyrighted private scores/audio, or private generated workspaces belong here. Only redacted/sanitized evidence and repository identifiers may be persisted.

## Live evidence collection

`project_orchestrator.github_evidence` is the I/O boundary for GitHub observation. It collects repository metadata, default-head SHA, open PR details, raw check runs, reviews/comments, recent issues, branches, recent commits, and a sanitized projection of each repository control-plane manifest.

The collector deliberately does **not** decide which check runs are required. Every collected PR therefore carries `required_checks_state: UNKNOWN` until the separate required-check discovery adapter maps current rulesets/branch protection/workflow policy onto those raw checks. This prevents a successful optional workflow from being mistaken for merge authority.

## Review and pagination safety

Collection is exhaustive for list endpoints: pagination continues until a short page is observed and fails closed if the configured safety bound is exceeded. Pull-request Conversation comments and inline review comments are both retained.

Active `CHANGES_REQUESTED` states are normalized per reviewer. Comment semantics remain conservative: when comments exist but their blocking status is not mechanically resolved, `blocking_comments_state` is `UNKNOWN`, which prevents the decision kernel from emitting a GREEN merge action.

## Required-check resolution

`project_orchestrator.required_checks` enriches raw PR evidence with the CI policy that applies to the PR base branch.

Authority is layered deliberately:

1. effective GitHub branch rules and legacy branch-protection required contexts are live authority;
2. current control-plane `verification.mandatory_check_names` are policy evidence;
3. raw check runs prove which named checks actually exist on the exact PR head.

A check required by live branch policy is synthesized as `status: missing` when absent, preventing merge. A manifest-only name that is absent from the current head is **not** treated as permanently required; it produces `UNCERTAIN` because the workflow may have been renamed, retired, or made non-applicable. Required-workflow rules that cannot yet be mapped to concrete check contexts also fail closed as `UNCERTAIN`.

The decision kernel accepts `CONFIRMED_NONE` only when live branch rules were successfully read and the current control plane explicitly declares an empty mandatory-check list. This allows repositories with truly no required CI to proceed without pretending that missing policy evidence is equivalent to no policy.

## Knowledge Engine compatibility planning

`engineering/knowledge-engine-compatibility.json` records only cross-repository boundaries already established by the product repositories' governing documents. It does not invent a new product architecture.

The deterministic planner models three supported dependency paths:

- Core machine-readable `ke` CLI contracts are consumed directly by AI through `knowledge_engine_ai.ke_client`; Web is a downstream consumer of AI.
- Core persisted database schema is consumed read-only by Web through SQLAlchemy reflection.
- AI typed Python contracts are consumed by Web through its pinned `knowledge-engine-ai` dependency.

For a proposed shared-contract change, `project-orchestrator compat-plan` identifies direct and transitive consumers and emits dependency-safe preparation and verification actions. Breaking changes require consumers to be prepared before producer merge and recommend temporary old/new compatibility when practical. Unknown contract kinds fail closed.

The planner also records forbidden boundary shortcuts already documented by the family, including direct Web imports of Core and raw Web subprocess calls to Core that bypass AI's supported `ke_client` boundary. Repository governing documents remain higher authority than this machine-readable projection.

## Compounding evidence store

`project_orchestrator.timeseries` and `engineering/compounding-timeseries.json` are the deterministic record of whether the development loop is actually compounding, answering the question the "Exponential-development hypothesis" section above raises but does not settle.

An **evidence window** is scoped to a project (or the portfolio as a whole) and one of the five product repositories or `jweter/project-orchestrator`, spans an explicit `window_start`/`window_end`, and records a fixed set of development-loop metrics: PR creation-to-green time, failure-to-repair time, merge cycle time, CI failure/repeat-failure counts, preflight failures prevented, roadmap slices completed, Product Reality debt created/resolved, cross-repo dependency incidents, blocked/waiting time, and autonomous vs. Jeremy-required interventions.

Every metric is either `MEASURED` (a numeric value, a fixed per-metric unit, a sample count, and non-empty `evidence_refs` pointing at the reproducible source — a PR URL, run ID, or similar) or explicitly `UNKNOWN`/`INSUFFICIENT_DATA` with a reason. A window can never omit a metric key to imply it silently; the schema requires all thirteen keys be present, so absence of evidence is always visible rather than absent. `window_id` is content-derived, so replaying the same evidence is idempotent and byte-deterministic on write.

`aggregate_windows` rolls windows up by repository, project, or the whole portfolio using a sample-count-weighted mean over only the `MEASURED` entries, and is independent of input order (windows are re-sorted by `window_id` before folding). An aggregate is `UNKNOWN` only if every contributing window is `UNKNOWN` for that metric, and reports how many contributing windows were measured vs. unknown vs. insufficient so a rollup never quietly hides missing coverage. Nothing here asserts compounding; it only makes the trend computable once enough windows accumulate.

`project-orchestrator timeseries-report` (optionally `--since`, `--project`, `--repository`) is the query surface DoWork uses to read recent evidence without touching the raw JSON store directly. `--reinvestment` prints `build_reinvestment_snapshot`: a deterministic, decision-free aggregate projection (portfolio/project/repository rollups) that is the interface the Reinvestment Controller (below) consumes.

## Reinvestment Controller

`project_orchestrator.reinvestment` answers, from `timeseries.build_reinvestment_snapshot` alone: given what the portfolio has actually measured, what engineering investment would most improve future development? It is a recommendation layer, not an executor — every plan it produces sets `requires_jeremy_approval: true` unconditionally in this slice, and it never merges, deletes, bypasses CI, or touches product-repository architecture.

**Candidate categories** (fixed, exhaustive): `recurring_ci_failure_prevention`, `preflight_tooling_improvement`, `failure_repair_automation`, `cross_repo_compatibility_improvement`, `product_reality_debt_reduction`, `roadmap_capability_delivery`, `orchestration_control_plane_improvement`, `evidence_observability_gap`, `jeremy_required_intervention_reduction`. Each category is generated by inspecting a specific, documented subset of the reinvestment snapshot's aggregated metrics at the scope (repository/project/portfolio) where that category is meaningful — a candidate is only ever produced when real evidence triggers it (e.g. a repeat-failure count above zero, net Product Reality debt above zero, fewer recorded evidence windows than the confidence floor).

**Scoring model.** A candidate's `priority_score` is deliberately an unbounded, unitless, ordinal number, not a predicted ROI/duration/probability — those would be fabricated. It sums up to eight fixed *components* (`cycle_time_reduction`, `recurrence_frequency`, `failure_severity`, `affected_scope`, `blocked_development_impact`, `product_reality_impact`, `human_intervention_burden`, `implementation_cost_complexity`), each a bounded (`0`–`10`) monotonic transform of one real measured value (seconds capped at one point per hour; counts capped at two points per occurrence). A component is included **only** when its underlying metric is `MEASURED`; when the metric is `UNKNOWN`/`INSUFFICIENT_DATA` the component itself stays `UNKNOWN` and is left out of the sum — it is never coerced to `0`, which would misrepresent "no evidence" as "no impact". `confidence` is the fraction of the eight components that had real evidence (so no candidate can ever reach `1.0`, since `implementation_cost_complexity` has no deterministic model yet and is always `UNKNOWN`). Only candidates at or above `MIN_CONFIDENCE_FOR_SELECTION` (`0.5`) are eligible to be `selected`; if none qualify, `selected` is `null` and the plan says so rather than guessing.

**Evidence gaps as candidates.** `evidence_observability_gap` and `orchestration_control_plane_improvement` are themselves generated from the *absence* of evidence (portfolio metrics with no `MEASURED` entries; fewer than `MIN_WINDOWS_FOR_CONFIDENCE` recorded windows) — the controller can recommend closing a measurement gap exactly like it recommends fixing a CI failure, per the design requirement that gaps preventing confident decisions are themselves valid reinvestment candidates. `build_plan`'s `evidence_gaps` list additionally always names `implementation_cost_complexity` as a standing, permanent gap.

**Output shape** (`reinvestment-plan` CLI / `reinvestment.build_plan`): `candidates` (sorted by score, then confidence, then id — deterministic regardless of input order), `selected`, `rejected_or_deferred` (every non-selected candidate with a reason), `evidence_gaps`, `safety_boundaries` (the fixed list of governing constraints, echoed for any consumer), and `requires_jeremy_approval`. This is consumable directly by DoWork, a future scheduler report, or human review without further interpretation.

## Sanitized learning memory

`engineering/learning-memory.json` is the durable portfolio-level record of lessons that are both resolved and verified. It is deliberately narrower than raw CI logs, review payloads, or Product Reality evidence.

A persisted lesson records the project/repository, source type/reference, symptom, root cause, fix, verification, regression protection, and residual risk. Entries must have `evidence_state=VERIFIED`; pending theories cannot become durable learning. A content-derived `lesson_id` makes repeated ingestion idempotent.

Validation rejects raw/private payload fields and common secret patterns. The memory is therefore useful for preventing repeated engineering failures without becoming a credential store or a backdoor for private Product Reality artifacts. The validator is part of exact-head preflight.
