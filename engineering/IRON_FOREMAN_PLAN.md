# Project Iron Foreman

## Mission
Remove the Foundry supervisor as a single point of failure. A supervisor process that exists but no longer makes progress must be detected, fenced, safely retired, replaced through the authorized launcher, and reconstructed from durable runtime truth without duplicate mutation authority.

## Production failure signature
On 2026-10-03 generation `802b4dd6bbf64aee83e1599b26f620d3` (PID 42724) remained alive according to Windows while `supervisor-generation-heartbeat.json` stopped advancing. Persistent external locking of `supervisor.json` had already demonstrated that PID existence and canonical-state publication are not sufficient health signals.

## Non-negotiable invariants
1. Never equate PID existence with supervisor health.
2. Never reclaim from heartbeat age alone.
3. Never terminate a process unless PID, process-start fingerprint, process instance, and burst are all proven to refer to the intended generation.
4. A degraded heartbeat may prove liveness but never canonical authority.
5. Only canonical supervisor identity may produce launcher PASS.
6. Old/fenced generations must be unable to dispatch, issue grants, harvest, promote, or publish authoritative state after takeover.
7. Existing CI, Product Reality, security, provenance, licensing, repository, and ownership gates remain fail-closed.
8. Human ownership mode always wins.
9. Recovery must use existing authorized launcher/watchdog paths; no schedule changes.
10. A recovery is incomplete until the replacement generation proves fresh heartbeat, progress, canonical state, and reconciled worker/grant reality.

## Phase 1 — Independent health truth
Status: IN PROGRESS

- [x] Add `project_orchestrator.supervisor_health`.
- [x] Classify exact generation identity, heartbeat freshness, and progress freshness.
- [x] Treat fresh `STATE_PUBLICATION_BLOCKED` as degraded but live.
- [x] Classify stale heartbeat + stale progress as `SUPERVISOR_STALLED`.
- [x] Add monotonic `supervisor-progress.json` canary from the supervisor loop.
- [ ] Add explicit loop phase to progress evidence where useful for diagnosis.
- [ ] Add tests for clock skew, missing progress, wrong burst, and process identity changes.

Acceptance: health classification is deterministic, fail-closed, and regression tested against the PID-42724 production signature.

## Phase 2 — Fenced reclamation
Status: NOT STARTED

- [ ] Implement process identity verification using PID + process-start fingerprint.
- [ ] Persist an `iron-foreman-recovery.json` decision before mutation.
- [ ] Require `SUPERVISOR_STALLED` for a bounded confirmation window, not one sample.
- [ ] Re-read identity immediately before retirement (TOCTOU defense).
- [ ] Fence the exact generation before termination.
- [ ] Ensure fenced generation checks its authority before every mutation boundary.
- [ ] Graceful takeover first; bounded terminate only when graceful handoff cannot progress.
- [ ] Never reclaim in human mode.

Acceptance: a simulated stale generation is reclaimed; a healthy, degraded-progressing, identity-mismatched, PID-reused, or human-owned generation is never reclaimed.

## Phase 3 — Guardian actor
Status: NOT STARTED

- [ ] Add a minimal independent Iron Foreman guardian process.
- [ ] Guardian performs health/recovery only; no planning, coding, or repository mutation.
- [ ] Persist guardian heartbeat and last decision.
- [ ] Make guardian startup use the existing authorized Windows recovery/autostart mechanism.
- [ ] Prevent duplicate guardians with its own narrow single-instance authority.
- [ ] Bound polling and recovery rate to prevent restart storms.

Acceptance: Guardian can survive supervisor failure and restore exactly one authoritative supervisor without itself becoming a second control plane.

## Phase 4 — State reconstruction after takeover
Status: NOT STARTED

- [ ] Reconcile runtime leases and dead worker PIDs.
- [ ] Reconcile grant lifecycle and stale CLAIMED/ISSUED states.
- [ ] Reconcile worktrees/workspaces and active mutation ownership.
- [ ] Reconcile GitHub PR/CI/merge truth.
- [ ] Reconcile runner capacity and scarce-resource leases.
- [ ] Publish reconstruction evidence before dispatch resumes.
- [ ] Immediate harvest/refill after reconciliation when eligible capacity exists.

Acceptance: restart does not duplicate work, lose completed work, strand capacity, or resume from stale planner intent.

## Phase 5 — Observability and dashboard
Status: NOT STARTED

- [ ] Expose supervisor health state, heartbeat age, progress age/sequence, generation, burst, and phase.
- [ ] Expose Guardian state and last recovery decision.
- [ ] Record recovery count, recovery latency, reason, and outcome.
- [ ] Distinguish HEALTHY, DEGRADED, STALLED, RECOVERING, and HUMAN_OWNED.

Acceptance: dashboard truth agrees with durable state and live process evidence.

## Phase 6 — Production proving
Status: NOT STARTED

- [ ] Full repository-native test suite GREEN.
- [ ] PR/CI GREEN; merge only after all required gates pass.
- [ ] Canonical actor consumes merged SHA.
- [ ] Controlled stale-supervisor fault injection.
- [ ] Guardian detects the stall without false reclaim.
- [ ] Exact old generation is fenced/retired.
- [ ] One replacement generation starts.
- [ ] Replacement publishes fresh heartbeat + progress + canonical `supervisor.json`.
- [ ] Worker/grant/runtime state reconstructs correctly.
- [ ] No duplicate mutation authority and no recurrence during observation window.

Acceptance: Iron Foreman is not DONE until live Azathoth recovery is proven end-to-end.

## Phase 7 — Role-specific agent intelligence (after reliability)
Status: DEFERRED

- [ ] Keep measured fast coding model as default builder unless benchmarks justify change.
- [ ] Add lightweight decision model for triage/routing/rubric gates where useful.
- [ ] Invoke stronger/slower models selectively for difficult planning/review.
- [ ] Measure quality, latency, retry rate, and successful PR throughput by role/model.

This phase must not delay supervisor self-recovery.

## Current execution order
1. Finish Phase 1 edge-case tests.
2. Build fenced reclamation primitive and tests.
3. Add Guardian actor around that primitive.
4. Wire authorized startup/recovery.
5. Add reconstruction proof.
6. Add dashboard/metrics.
7. Run full suite and PR/CI.
8. Merge.
9. Deploy merged SHA to canonical actor.
10. Fault-inject an alive-but-stalled supervisor and prove complete recovery.

## Definition of DONE
Iron Foreman is complete only when an alive-but-stalled supervisor can be detected and recovered automatically on Azathoth, with exact-generation fencing, one authoritative replacement, reconstructed execution truth, no duplicate mutation authority, and durable evidence proving every gate above.


## Successor project — Project Helix
Project Iron Foreman ends by proving that the Foundry control plane can recover itself safely. Its direct successor is **Project Helix**, defined in `engineering/PROJECT_HELIX_ROADMAP.md`.

Helix begins only after the Iron Foreman production Definition of DONE is satisfied. The handoff is deliberate:
- Iron Foreman provides trustworthy recovery, exact-generation authority, and reconstructed execution truth.
- Helix turns that truth into a durable execution ledger and typed agent contracts.
- Helix then adds unified tracing and quantitative trajectory evaluation.
- Verified successful episodes feed Capability Compiler v2.
- Measured capabilities then improve planner/dispatcher decisions without taking authority away from deterministic safety, ownership, CI, or Product Reality gates.

The first Helix artifact is the evidence bundle from Iron Foreman's final controlled recovery test. That becomes Helix Phase 0's reference reconstruction fixture.

**Next major project after Iron Foreman: Project Helix — Durable Execution, Evidence, and Learning.**
