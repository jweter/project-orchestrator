# Iron Foreman Production Roadblock Handoff — 2026-10-03

## Purpose
Persist the current production-throughput incident and its repair requirements so future autonomous development does not rediscover the same failure classes. DoWork Hourly remains enabled and unchanged.

## Current production truth
The portfolio can discover, implement, test, and run repository-native preflight, but verified work can accumulate before PR/CI/merge. Promotion infrastructure is therefore a P1 liveness concern, not evidence that engineering itself is idle.

Project Iron Gate owns the promotion subsystem: redundant authorized actuators, SHA-bound promotion, circuit breakers, durable promotion queue, asynchronous PR/CI watcher, safe automatic merge/cleanup, and WIP/backpressure. Iron Foreman continues planning, dispatch, execution, recovery, and exact-head verification.

## Evidence and immediate blockers
- Rocksmith `repair/live-review-transport-without-draft` at `51d92ce44df89d27db12c30f0d3c5d677b831f05` is the first Iron Gate PREFLIGHT_GREEN -> PR_OPEN proof fixture.
- A separately GREEN Knowledge Engine PR is the first CI_GREEN -> MERGED proof fixture.
- Everward promotion remains subordinate to exact-head native verification; never bypass its simulation/build gate.
- VoiceStudio S1 must reconcile onto current main before S2; semantic reconciliation and full native preflight are required.
- Project Orchestrator PR #491 merged as `bc6e8366ca2fef16a41b2879191af8139ada1f09`: preserved-work recovery now resolves `engineering/preflight.py` first, with `scripts/preflight.py` compatibility fallback, and fails closed if no verifier exists.
- At merge time the canonical actor was still `5750984ea9fc5af9ed423cb0c388598351bebde8`. A merge is not runtime-complete until a fresh authoritative supervisor generation consumes the merged revision and publishes fresh heartbeat/dispatch/grant/worker evidence.
- Everward worker checkout failed because a zero-byte Git worktree `index.lock` from 2026-09-29 remained after its owner was gone. Safe removal restored checkout to `origin/main`. This must become deterministic Foreman recovery rather than manual cleanup.

## Required Foreman repair: stale Git lock recovery
Implement a bounded fail-closed Git-lock recovery primitive used before worker checkout/reset.

Requirements:
1. Detect the specific lock preventing the requested Git operation and preserve its path/evidence.
2. Never delete a lock merely because checkout failed.
3. Prove the lock is stale: age exceeds a conservative threshold AND no live owning/relevant Git process can be established. If ownership is ambiguous, do not delete it.
4. Remove only the proven-stale lock, record recovery evidence, retry the original operation once, and surface the original Git stderr if retry fails.
5. Do not touch dirty/productive worktrees or terminate productive workers to recover a lock.
6. Regression-test active-lock preservation, stale-lock recovery, ambiguous ownership fail-closed behavior, retry success, and retry failure.
7. Replace generic `base checkout failed` telemetry with a classified failure carrying repository, worktree, target SHA/ref, stderr fingerprint, and lock-recovery result.

Acceptance: an abandoned worktree lock cannot permanently kill an eligible lane, while a live/ambiguous lock is never removed automatically.

## Iron Gate acceptance contract
- EngineeringState and ActuatorState are separate facts.
- Promotion consumes immutable exact-head VerificationEvidence; branch names/prose are insufficient authority.
- SHA drift invalidates prior verification.
- Exhaust all authorized actuators before classifying the engineering operation as unavailable.
- Repeated deterministic actuator failures open a circuit and fail over; do not burn hourly bursts retrying the same rejected mutation.
- Pending CI/review is asynchronous: release the engineering lane and revisit when state changes.
- Enforce per-product WIP limits so promotion failure neither stalls the portfolio nor creates unlimited verified inventory.
- Restart must preserve queued promotions.
- Duplicate scheduler invocations must reconcile against a singleton portfolio lease rather than spawn duplicate factories.

## Runtime acceptance contract
A control-plane repair is complete only after: branch/commit -> PR -> required CI GREEN -> merge -> canonical actor sync -> fresh supervisor `process_instance_id` -> fresh heartbeat -> repaired failure signature absent -> fresh dispatch/grant/selected-runner/live-worker evidence. A stale process, planner intent, or unconsumed merge is not completion.

## Metrics
North star: Verified Product Advancement Rate.
Companion: Verification-to-Merge Latency for exact heads, excluding legitimate CI/review/Product Reality time.
Track actuator failure fingerprints, circuit state, promotion queue age, stale-lock recoveries, worker checkout failures, WIP by engineering state, and Jeremy-required interventions.

## Priority order
1. Finish runtime consumption/proof of `bc6e8366` without killing productive workers.
2. Make stale Git-lock recovery deterministic and regression-protected.
3. Keep product lanes work-conserving while Iron Gate owns promotion repair.
4. Prove Iron Gate on Rocksmith exact head, then on a GREEN Knowledge Engine PR.
5. Diagnose Everward from the first meaningful native verification/build error after worker checkout is healthy.
6. Reconcile VoiceStudio S1 onto current main before S2.

## Non-negotiable constraints
Never weaken preflight, CI, security/privacy, branch protection, provenance, Product Reality, simulation authority, or exact-head verification. Never hand-edit generated dependency locks. Never allow unlimited WIP. Never alter, pause, disable, delete, reschedule, or replace DoWork Hourly as a workaround.
