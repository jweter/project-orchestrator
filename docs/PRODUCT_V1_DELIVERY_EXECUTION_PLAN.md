# Product-first autonomous delivery: the path to verified v1.0

Status: IMPLEMENTATION PLAN / NOT A RUNTIME AUTHORIZATION. Owner direction: 2026-11-08.
Canonical product scope remains the owner-approved roadmap, each product repository's governing architecture,
and `engineering/product-usability-gates.json`. This plan does not claim any product has reached v1.0.

## Definition of success

**Iron God v1.0:** An owner-authorized local engine continuously derives bounded, legitimate work
from approved product roadmaps; assigns it only to independently authorized qualified workers;
preserves ownership and exact-head evidence through interruption; runs native verification and
Product Reality; opens/repairs/merges approved PRs through protected GitHub gates; rotates to
another independent product while waiting; and presents truthful status and interoperable
handoffs without repeated owner `Continue` commands. Owner veto remains immediate.
Successful unit tests or an orchestrator PR alone do not satisfy this outcome.

**Each product v1.0:** A versioned, reproducible release that satisfies the *product repository's*
owner-approved v1.0 scope and genuine user-journey acceptance. The portfolio's existing
`FINISHED_TARGET` state is not automatically a shipped v1.0; include build/package, operator
instructions, safe install or launch, and product-specific Product Reality. If a repository has
not specified its v1.0 scope, record V1_SCOPE_UNVERIFIED and derive a proposed contract for
owner review rather than inventing a finished product.

## The four real product journeys

| Project | Next verified usability gate (existing portfolio contract) | Further v1.0 acceptance |
| --- | --- | --- |
| Knowledge Engine (core, web, AI) | Open it, ask a real research question, receive a useful evidence-backed answer end to end | Versioned, supported offline/online-on-demand workflows with attributable citations, correct uncertainty, documented installation and repeatable cross-repository integration; final scope must be confirmed from its repositories |
| Rocksmith CDLC Generator | Submit a song and produce genuinely playable Rocksmith CDLC with acceptable note and timing alignment | Repeatable end-to-end generation and game-level playability/quality verification using permitted source audio, plus installation, failure handling and a releasable package; final scope remains repository-owned |
| Everward | Launch and play a coherent Unreal vertical slice | Build and play the repository-authoritative one-probe progression through industrial bootstrap, a successor and first departure with stable controls/save/build; additional v1.0 scope requires explicit repository authority |
| VoiceStudio Singing | Supply/record a voice, choose a singing target, export listenable singing audio | Repeatable authorized local singing workflow, pitch/timing acceptance, clear errors, a usable UI/export, model/privacy/license compliance and a versioned install; final scope remains repository-owned |

Never equate a mock, simulated CI fixture, documentation, merged PR, or green unit test
with real playability, listenability, user research value, or installability.

## The only valid reason to schedule a slice

Each proposed slice must name **the concrete v1.0 capability or blocker it advances**.
An executable slice is a dependency-ordered increment to the nearest unmet *real*
usability criterion, not a convenient collection of files or a superficial PR.
Safety, recovery, security, build, documentation and test-only work are eligible
when they demonstrably unlock, protect or verify a product's next gate. Record the
dependency explicitly. Otherwise park the slice as NON_V1_DEVELOPMENT.

For every slice preserve the following durable record, using existing canonical ledgers
rather than a new competing source of execution authority:

1. Project, repository, authoritative source/roadmap ref, v1.0 capability identifier,
   unmet gate and real customer journey step.
2. Specific before/after behavior and measurable improvement to the gate; real blocked
   dependency it removes; evidence that equivalent code does not already exist.
3. Unique slice ID, branch, immutable base SHA and exact candidate SHA when available,
   accepted worktree/ownership/lease/runner identity, dependencies and conflict scope.
4. Allowed and forbidden paths, required qualified builder and capacity, exact native
   test commands, preflight, Product Reality and security/provenance requirements.
5. Objective acceptance criteria with negative/failure cases, expected artifacts,
   user-visible demonstration, and independent verifier.
6. Lifecycle state, last verified result, still-unmet acceptance criteria, failure
   fingerprint, retry policy and exact NEXT ACTION. Uncertain evidence is UNKNOWN.
7. GitHub PR, review and CI identifiers; accepted and merged SHAs after verification;
   a link to the *next* unmet gate rather than a claim that merging is completion.

A successful slice must change verified product capability or eliminate a verified blocker,
with before/after evidence. If its tests pass but the user workflow is still broken, the
record remains INCOMPLETE and names the next product blocker. No fabricated percentages.

## Sequence (dependencies, not seven isolated infrastructure projects)

### D0. Reconcile what exists; establish a demonstrable baseline
- Verify pending PRs #666 (uncertain-launch ownership) and #667 (dashboard) and their
  exact-head CI; repair a failure on its original branch. Do not assume an open PR is merged.
- Inventory each product repo's actual authoritative roadmap, active worktrees, open PRs,
  recent failed native checks, current first-working-build demo, and next unmet v1.0 steps.
- Produce a per-product **V1 capability ladder**: independent product requirements, build
  target, verification environment, ordered blockers and evidence. Preserve unavailable
  evidence as UNKNOWN rather than declaring the product unusable or complete from inference.
- Publish a read-only public-safe summary of work, next blockers and ownership. Never expose
  private user payloads.

### D1. Establish a common Iron God communication gateway (MCP + durable handoffs)
- Build the **separately bounded communication** foundation in
  `docs/IRON_GOD_MCP_COORDINATION.md`: local stdio MCP, SQLite advisory inbox,
  recipient-filtered read, idempotent post, receipt acknowledgment, audit and replay.
- Client tools expose status, product gate, messages, and evidence references. Messages
  can request *review* or *attention* but cannot grant owner authority, issue leases,
  dispatch code, change runtime settings, or claim CI/Product Reality success.
- Claude Code and the local agent use stdio clients initially. ChatGPT requires a supported
  authenticated remote MCP connection or Secure MCP Tunnel, separately configured by
  the owner. No unauthenticated public port.
- Confirm a two-way handoff/recovery demo with two independently launched clients and
  a durable restart; preserve one canonical controller and one execution-claim ledger.

### D2. Demonstrate one REAL end-to-end product slice
- Prefer a short Python-capable path through Knowledge Engine **only if its current
  roadmap, builder calibration, and native test environment permit it**. Otherwise select
  the shortest eligible true product capability from another project.
- Prove roadmap -> admissible SliceSpec -> qualified builder -> changed product behavior ->
  native tests -> independent exact-head preflight -> Product Reality -> branch/PR ->
  fresh CI and reviews -> governed merge -> next backlog capability.
- If a builder repeatedly produces contradictory tests or unusable output, fix the
  first evidenced cause, then retry the *same* product acceptance. Do not count attempts
  or unrelated orchestrator patches as product delivery.

### D3. Prove interruption and continuation on that same product flow
- Fault-inject at worker launch, halfway through code, after commit, before local
  verification, during PR CI, and after merge before progress reconciliation.
- Restore from durable exact-head, branch, lease, unfinished criteria, message/ack
  cursors and next action. Verify no duplicate owner, repeated unsafe action,
  lost accepted commit or skipped quality gate. Unknown process identity fails closed.
- Deliver Iron Foreman exact-generation recovery with progress/liveness, owner-veto
  preservation, bounded restarts, restored leases/grants and one authoritative supervisor.
- Re-run D2 with recovery active; no synthetic replacement for the actual product result.

### D4. Advance all four product families fairly toward v1.0
- Recompute the next capability frontier after each merge or material blocker.
- Maintain per-product progress and starvation protection; CI/Unreal/audio/tool waits
  must release idle capacity to other eligible independent work.
- Promote real FIRST_WORKING_BUILD -> USABLE_ALPHA -> USER_TESTING -> BETA ->
  FINISHED_TARGET as each repository's evidence permits. Avoid unqualified model
  downgrades and do not fabricate C++/audio verification.

### D5. Release and prove v1.0
- For each product: documented v1.0 requirements, accepted end-user demonstration,
  native and integration test evidence at the released SHA, Product Reality receipts,
  installable/publishable artifact if authorized, user documentation, error/recovery
  behavior, and owner acceptance if the product requires it.
- For Iron God: observed unattended product throughput, exact-head publication, no
  owner-control violations, successful stalled-supervisor recovery, no duplicate
  ownership, and dashboard status matching actual runtime, PR and release evidence.
- Stop when the specified version 1.0 target is proven or an explicit owner decision
  is needed; do not expand the 1.0 scope silently.

## Operating policy and review cadence

At every turn select the **highest-value dependency-ready, qualified product slice**
that removes a concrete blocker to v1.0. Continue other safe projects while a PR or
native verification runs. CI/preflight failures are actionable repair states, not
successful delivery. Change infrastructure only for a demonstrated v1.0 blocker or
safety requirement. Report: product/gate, next measurable behavior, verified merged
head and test/product evidence, blocker, owner/lease, next action, communication
handoff, and whether owner action is *actually* required.

The GitHub development workflow must not update the resident release pin, reset active
worktrees, disable tasks, release owner veto, or claim the laptop has been upgraded.
Resident deployment is a separate controlled owner/Claude Code procedure.

## Completion criteria for THIS plan

Plan is implemented only when: (1) MCP communication and SQLite replay are verified
across supported clients, (2) at least one genuinely useful product capability travels
through the full protected delivery chain without manual developer/integrator intervention,
(3) the same chain survives controlled crash/restart without duplicate authority,
(4) all four product trajectories are traceable to repository-approved v1.0 exit criteria,
(5) the dashboard reflects authoritative truth, and (6) the agreed v1.0 releases and
real user journeys pass their own acceptance. Documenting this plan is not completion.
