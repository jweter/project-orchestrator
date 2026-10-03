# Project Helix

## Mission
Turn Azathoth Foundry from a reliable autonomous executor into a measurable, resumable, evidence-driven learning system. Project Helix begins only after Project Iron Foreman proves supervisor self-recovery in production. Iron Foreman makes the control plane survive; Helix makes every unit of autonomous work durable, inspectable, evaluable, and capable of improving future execution without weakening safety gates.

## Architectural thesis
Foundry should operate as a closed evidence loop:

```
intent -> typed work order -> checkpointed execution -> tools/workspace
       -> tests/CI/Product Reality -> outcome trace -> evaluation
       -> verified capability/rule -> future planning
```

Models remain replaceable components. Durable state, typed contracts, execution evidence, authority, evaluation, and recovery are the system.

## Entry gate from Project Iron Foreman
Project Helix MUST NOT become the primary engineering project until Iron Foreman satisfies its production Definition of DONE:
- live stale-supervisor detection proven;
- exact old generation fenced/retired;
- exactly one replacement generation authoritative;
- worker/grant/runtime state reconstructed;
- fresh canonical heartbeat/progress/state;
- no duplicate mutation authority;
- human ownership still overrides automation;
- observation window shows no recurrence.

The Iron Foreman reconstruction model becomes Helix's initial durable execution truth.

## Non-negotiable invariants
1. Never treat model prose as execution truth.
2. Every execution transition has a typed, machine-validated contract.
3. Every claimed completion points to durable evidence.
4. Checkpoints are append-safe and generation/attempt identifiable.
5. Resume from the last proven checkpoint, never inferred intent.
6. Existing CI, Product Reality, security, provenance, licensing, repository, ownership, and human-mode gates remain fail-closed.
7. Evaluation may recommend; it may not silently weaken a gate.
8. Learned rules require verified successful evidence and measurable promotion criteria.
9. Bad rules can be demoted/disabled from outcome evidence.
10. Observability must not become execution authority.
11. External frameworks are adopted only when they beat a smaller native implementation on measured value.
12. Human ownership always wins.

## Phase 0 — Iron Foreman handoff
- [ ] Import Iron Foreman reconstruction evidence as the baseline execution-state model.
- [ ] Define stable execution, attempt, generation, worker, runner, repository, and evidence identifiers.
- [ ] Preserve existing runtime/grant/worktree/GitHub truth rather than creating a competing authority.
- [ ] Record the first end-to-end recovered execution as the Helix reference fixture.

Acceptance: Helix can describe the exact state of work immediately after an Iron Foreman recovery without inventing or duplicating authority.

## Phase 1 — Durable Execution Ledger
Create a canonical append-safe execution ledger with explicit transitions:

`PLANNED -> CLAIMED -> PREPARED -> BUILDING -> TESTING -> PREFLIGHT -> PR_OPEN -> CI -> REVIEW -> MERGED -> RUNTIME_VERIFIED -> COMPLETE`

Terminal/exception states include `FAILED`, `BLOCKED`, `YIELDED`, `SUPERSEDED`, and `ABORTED`.

Each transition records:
- execution/attempt IDs;
- repository + exact base/head SHA;
- planner/supervisor generation;
- worker + runner + model/tool identity;
- timestamps and duration;
- input/output evidence references;
- retry/recovery relationship;
- gate result and reason;
- next legal transitions.

- [ ] Define schema and transition validator.
- [ ] Persist transitions atomically.
- [ ] Make duplicate/idempotent writes safe.
- [ ] Add illegal-transition tests.
- [ ] Reconstruct current execution from ledger + authoritative external truth.
- [ ] Resume from last proven checkpoint after reboot/recovery.

Acceptance: killing and restarting the control plane cannot make Foundry forget which proven stage an execution reached.

## Phase 2 — Typed Agent Contracts
Replace free-form inter-agent claims with validated structures.

Initial contracts:
- `WorkOrder`
- `PlanResult`
- `DispatchDecision`
- `BuildResult`
- `TestResult`
- `ReviewResult`
- `PromotionResult`
- `RuntimeVerificationResult`

- [ ] Require exact repo/SHA/branch/worktree identity where relevant.
- [ ] Require evidence for success claims.
- [ ] Reject missing/contradictory fields fail-closed.
- [ ] Version schemas.
- [ ] Add compatibility/migration tests.
- [ ] Evaluate Pydantic/PydanticAI patterns; adopt dependencies only if they reduce complexity without taking control-plane authority.

Acceptance: “I fixed it” is never actionable; only a valid evidence-backed result can advance state.

## Phase 3 — Unified Execution Tracing
Give every autonomous slice one trace spanning planner to runtime verification.

- [ ] Propagate execution/attempt/trace IDs through planner, dispatcher, worker, tools, tests, GitHub gates, recovery, and promotion.
- [ ] Emit structured spans/events for model calls, tool calls, queue delay, work duration, retries, failures, and gates.
- [ ] Prefer OpenTelemetry-compatible instrumentation.
- [ ] Route to existing Datadog where it provides equivalent value before adding another observability service.
- [ ] Benchmark Langfuse against Datadog/OpenTelemetry for agent-specific trace usability; add only if materially better.
- [ ] Link dashboard execution rows to trace evidence.

Acceptance: a stalled eight-hour interval can be explained from one trace without manually correlating unrelated JSON files.

## Phase 4 — Execution Quality Evaluation
Measure whether autonomous engineering was good, not merely whether a process ran.

Core metrics:
- task success;
- first-pass test success;
- regression rate;
- unnecessary edit/churn;
- retries;
- queue latency;
- execution duration;
- model/tool cost or local compute time;
- human intervention;
- PR acceptance/merge;
- runtime verification;
- recovery count;
- repeated failure fingerprints.

- [ ] Define deterministic metrics first.
- [ ] Add rubric/model evaluation only where deterministic evidence is insufficient.
- [ ] Evaluate DeepEval-style trajectory evaluation against native implementation.
- [ ] Build regression fixtures from real successful and failed Foundry episodes.
- [ ] Prevent evaluator output from bypassing hard gates.

Acceptance: changes to planner/model/runner/recovery strategy can be compared against historical evidence instead of intuition.

## Phase 5 — Capability Compiler v2
Convert verified successful episodes into reusable operational knowledge.

- [ ] Fingerprint recurring failure/task classes.
- [ ] Extract candidate repair/planning rules from successful traces.
- [ ] Attach provenance, evidence, scope, confidence, success count, failure count, and expiry/review metadata.
- [ ] Shadow-test new rules before promotion.
- [ ] Promote only after measurable improvement.
- [ ] Demote rules that regress outcomes.
- [ ] Inject only relevant verified rules into planning/dispatch.
- [ ] Never learn around security, ownership, provenance, licensing, CI, or Product Reality gates.

Acceptance: a recurring failure becomes cheaper/faster to solve over time, with measurable evidence showing why.

## Phase 6 — Planner / Dispatcher Intelligence
Use the ledger and evaluations to improve decisions.

- [ ] Select runner/model based on measured task-class performance.
- [ ] Predict decomposition size and likely gate risk.
- [ ] Use lightweight local decision models for routing/rubric decisions where benchmarked useful.
- [ ] Escalate to stronger/slower models only when expected value justifies it.
- [ ] Preserve deterministic authority for scheduling, leases, ownership, and promotion.
- [ ] Add portfolio-level prioritization using queue age, expected value, risk, and available capacity.

Acceptance: Foundry chooses execution strategy from measured outcomes while hard authority remains deterministic.

## Phase 7 — Temporal Knowledge Layer, only if justified
Do not add memory infrastructure merely because it is fashionable.

- [ ] Measure repeated context-reconstruction cost and stale-fact errors.
- [ ] If material, prototype a temporal knowledge layer for changing repo/runtime/project facts.
- [ ] Compare Graphiti/Cognee patterns against a smaller native evidence index.
- [ ] Preserve source/time/provenance on every remembered fact.
- [ ] Never let retrieved memory override live Git/runtime/CI truth.

Acceptance: add this phase only if benchmarks show persistent context is an actual bottleneck.

## Phase 8 — Production proving
- [ ] Full repository suite GREEN.
- [ ] PR/CI/Product Reality gates GREEN.
- [ ] Canonical actor consumes merged SHA.
- [ ] Reboot during active work and prove checkpoint resume.
- [ ] Kill/recover supervisor and prove Iron Foreman + Helix reconstruction.
- [ ] Inject worker failure and prove bounded retry without duplicate mutation.
- [ ] Demonstrate one traced execution from plan through runtime verification.
- [ ] Demonstrate one verified capability learned, shadow-tested, promoted, reused, and measured.
- [ ] Demonstrate human-mode takeover at every major phase.
- [ ] Observation window shows no duplicate authority, lost work, or gate weakening.

## Explicit framework strategy
Do not install the Reddit stack wholesale.

Borrow/test:
- LangGraph: durable checkpoint/state-machine semantics.
- PydanticAI: typed/validated agent contracts.
- Langfuse: agent-oriented tracing/observability patterns.
- DeepEval: trajectory/evaluation patterns.
- Graphiti/Cognee: temporal memory patterns only after measured need.

Normally avoid replacing existing Foundry control with Mastra/Agno. Browser automation remains a specialized tool, not control-plane authority. Remote sandboxing such as E2B is optional for genuinely untrusted execution; native isolated worktrees remain the default for Azathoth-local development.

## Delivery sequence
1. Iron Foreman production DONE.
2. Helix Phase 0 handoff/reference fixture.
3. Durable Execution Ledger.
4. Typed Agent Contracts.
5. Unified Execution Tracing.
6. Execution Quality Evaluation.
7. Capability Compiler v2.
8. Planner/Dispatcher Intelligence.
9. Temporal Knowledge Layer only if evidence justifies it.
10. Full production proving.

## Definition of DONE
Project Helix is complete when Foundry can take a unit of work from intent through runtime verification with typed contracts and durable checkpoints; survive reboot/supervisor/worker interruption without losing or duplicating work; expose one coherent execution trace; quantitatively evaluate the outcome; convert verified successful patterns into governed reusable capabilities; and demonstrate that those capabilities measurably improve later autonomous work without weakening any hard safety or ownership gate.
