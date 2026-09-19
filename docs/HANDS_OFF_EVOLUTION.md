# Project Hands-Off Evolution

## North star

Create a development environment in which Jeremy establishes product intent, authoritative guiding documents, roadmaps, acceptance criteria, and guardrails, then enables the Evolution Engine to carry ordinary engineering from unfinished roadmap work toward verified completion without Jeremy being a required participant in the routine loop.

The desired operating model is **govern -> observe -> realign**, not **prompt -> implement -> test -> prompt again**.

Jeremy remains product owner and reserved-decision authority. The engineering system owns routine selection, implementation, deterministic testing, repair, verification, PR preparation, safe promotion under existing merge authority, learning, and continuation.

## Target interaction model

Jeremy should normally be able to:

1. define or revise product direction;
2. approve the repository's governing roadmap, architecture, acceptance criteria, and guardrails;
3. turn on Hands-Off Evolution for that product;
4. leave the execution loop;
5. inspect repository state, telemetry, Product Reality debt, and roadmap progress when desired;
6. realign the roadmap or guardrails when product intent changes; and
7. intervene only at an explicit reserved human boundary.

A manual "continue", routine test run, log collection, branch repair, PR babysitting, or next-task selection is a defect in this target state unless repository policy explicitly reserves that action for a human.

## Product onboarding contract

A product is eligible for Hands-Off Evolution only when the following are explicit and machine-discoverable enough to fail closed:

- authoritative product vision and completion criteria;
- roadmap with unfinished authorized work;
- architecture and repository boundaries;
- security/privacy policy;
- licensing/provenance restrictions;
- deterministic verification and exact-head preflight;
- CI and merge gates;
- Product Reality definition and what cannot be inferred automatically;
- destructive/irreversible action boundaries;
- paid-service and credential boundaries;
- repository-local agent instructions;
- known environment-dependent verification routes;
- escalation rules.

Missing authority does not grant freedom. It produces a bounded UNKNOWN or escalation.

## Evolution runtime

Once enabled, each product continuously executes:

OBSERVE -> SELECT -> RESERVE -> BUILD/REPAIR -> VERIFY -> PR/CI -> PROMOTE -> RECORD -> LEARN -> PREVENT/REUSE -> SELECT NEXT.

The runtime must:

- consume live repository and sanitized worker evidence before planning;
- prefer P0-P4 existing work before new roadmap work;
- treat zero open PRs as a build trigger;
- maintain independent product liveness;
- use repository-native/local workers for Windows, Ollama, Unreal, packaged-app, or other environment-dependent gates;
- create governed branches before ordinary mutations;
- bind verification to exact repository/branch/head identity;
- fail closed on missing, stale, conflicting, or materially uncertain evidence;
- never fabricate Product Reality;
- convert deterministic failures into bounded repairs and regression protection;
- feed verified lessons into reusable capabilities;
- continue automatically after successful merge/verification until authoritative completion or a real reserved boundary is reached.

## Human boundary

Jeremy is not routine middleware.

Escalation is reserved for:

- fundamental product-direction decisions;
- materially ambiguous requirements with different product outcomes;
- major architecture outside existing authority;
- security/privacy boundary changes;
- license changes;
- paid or meaningful recurring cost;
- credentials/secrets that require Jeremy;
- destructive migrations or irreversible actions;
- release/publishing authorization;
- Product Reality that genuinely requires human judgment.

Everything else should be automated or treated as an engineering capability gap to remove.

## Observability without participation

Hands-off does not mean opaque. The system must make it easy to inspect:

- authoritative default heads and active PRs;
- current roadmap slice and promotion state;
- exact verification/CI evidence;
- worker dispatch/results;
- Product Reality debt;
- blockers and reserved human decisions;
- Product Advancement Rate;
- repeat failures and failure-to-repair time;
- reusable capabilities created/reused;
- Jeremy-required intervention count;
- why a lane is idle, if it is idle.

Repository state is the source of truth. Dashboards and reports are projections, never competing truth.

## Realignment model

Jeremy may change direction by editing/approving authoritative product documents. The engine must re-read authority before selecting subsequent work, reconcile active branches against the new direction, preserve unique work, and stop/discard nothing destructively without authority.

Realignment should not require rebuilding the autonomous system. Product intent is configuration/authority; the Evolution Engine is the reusable execution mechanism.

## Guardrails

Hands-Off Evolution never means unconstrained self-modification.

It must preserve:

- repository-local architecture and product authority;
- exact-head preflight and fresh CI;
- review/material-finding gates;
- security/privacy/licensing/provenance policy;
- branch ownership and conflict safety;
- Product Reality separation;
- least privilege;
- bounded resource use;
- no secret/private payload persistence;
- no force-push/discard of shared unique work;
- no release or irreversible action without explicit authority.

A failed autonomy mechanism blocks only the affected automation lane. It should generate a P1 orchestration repair input, not silently convert Jeremy into the worker.

## Project phases

### Phase 1 — Authority readiness
Create a deterministic readiness audit for each product/repository that proves the required guiding documents, roadmap, architecture, verification, guardrails, Product Reality policy, and escalation boundaries exist and are internally coherent.

The machine-readable readiness contract begins at `engineering/hands-off-evolution.json`. It is intentionally fail-closed: a product cannot be activated merely because an agent assumes a missing policy. The next implementation slice is an executable auditor that resolves these requirements against each live product repository and emits PASS/FAIL/UNKNOWN evidence without granting new authority.

### Phase 2 — Evolution activation
Define a per-product enablement manifest/state. Activation must be explicit, auditable, reversible, and incapable of weakening repository-local gates.

### Phase 3 — Persistent execution
Make the Evolution Engine a durable supervised process with crash recovery, bounded history, per-product liveness accounting, leases, exact-head identity, and worker/result reconciliation.

### Phase 4 — Automatic continuation
Prove that merge/terminal verification automatically releases the lane and selects the next authorized unfinished slice without a Jeremy prompt.

### Phase 5 — Closed-loop repair and learning
Route deterministic failures into repair branches, verification, regression prevention, reusable capabilities, and later capability retrieval. Measure whether retained capabilities actually reduce repeat failures or human intervention.

### Phase 6 — Portfolio proof
Demonstrate repeated unattended roadmap advancement for Knowledge Engine, Rocksmith, and Everward across multiple consecutive cycles, including at least one worker-dependent verification path per applicable product.

### Phase 7 — Human-loop retirement
Identify every remaining routine Jeremy touch. Automate deterministic touches. Keep only reserved decisions and genuine Product Reality. A recurring manual "continue" is P1 liveness debt.

## Success criteria

Project Hands-Off Evolution is operationally successful when:

- each enabled product can advance across multiple roadmap slices without manual continuation;
- zero-open-PR states autonomously select new authorized work;
- deterministic failures autonomously enter bounded repair;
- exact-head verification and fresh CI remain mandatory;
- environment-dependent gates run through unattended workers rather than Jeremy;
- verified merges trigger subsequent work automatically;
- Jeremy can inspect and realign without being required for ordinary flow;
- human intervention is limited to documented reserved boundaries;
- measured quality does not regress as intervention falls;
- Product Reality remains explicit rather than inferred;
- repository completion criteria, not autonomy infrastructure, determine when development stops.

## Anti-goal

The goal is not an agent that writes endless code. The goal is a governed engineering organization in software: product direction and constraints are explicit; execution is continuous; evidence controls promotion; failures improve the system; and humans supervise direction rather than push every button.

## Relationship to existing design

This project is the umbrella north star for:

- `AUTONOMOUS_COMPLETION_PATH.md`;
- `EVOLUTION_ENGINE.md`;
- `CONTINUOUS_ENGINEERING_FLOW.md`;
- unattended verification and Laptop Actor work;
- compounding capability/learning work;
- Product Advancement Rate and liveness enforcement.

Those documents define mechanisms. **Project Hands-Off Evolution defines the desired operating state.**
