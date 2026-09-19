# Autonomous Troubleshooting and Engineering Practice Playbook

This is operational guidance for autonomous engineering workers. It helps diagnose problems; it does not override repository authority, security/privacy/licensing rules, exact-head gates, Product Reality, or destructive-action boundaries.

## Troubleshooting protocol

1. **Preserve evidence first.** Capture the failing command, stage, exit status, bounded stdout/stderr, repository, branch, exact HEAD, relevant environment facts, and the smallest reproducible symptom. Do not reset or delete uncertain work merely to get clean state.
2. **Classify before changing code.** Distinguish product defect, regression, stale test/contract, environment/tooling failure, dependency/version drift, generated-artifact contamination, concurrency/lease problem, authorization/state drift, flaky/non-deterministic failure, or unknown.
3. **Reproduce narrowly.** Run the smallest deterministic test that demonstrates the failure. Then run the governing/canonical verification before claiming a repair.
4. **Form a falsifiable hypothesis.** State what is believed to be wrong, what evidence supports it, and what observation would disprove it. Avoid broad speculative edits.
5. **Prefer root cause over symptom suppression.** Never delete/relax tests, skip checks, broaden permissions, lower quality thresholds, or hide errors solely to make a gate green.
6. **Make the smallest coherent repair.** Preserve architecture and public behavior unless authoritative roadmap/acceptance requires change.
7. **Add a regression.** A repaired defect should normally gain a deterministic test, validator, fixture, compatibility rule, or failure fingerprint that would have caught it earlier.
8. **Verify in layers.** Targeted reproduction -> affected suite -> canonical preflight -> fresh CI/exact-head gates as applicable.
9. **Retain the lesson.** Correlate the repair with its failure fingerprint. Repeated successful repairs are candidates for deterministic capability promotion.
10. **Fail closed when uncertain.** Escalate genuine ambiguity instead of inventing authority.

## Unexpected failure decision tree

- **Code/test disagreement:** inspect authoritative behavior and recent intentional contract changes. A failing test may be stale; production code may be wrong. Evidence decides.
- **Works locally, fails CI:** compare OS, runtime/dependency versions, paths, locale/timezone, concurrency, permissions, generated files, and command differences. Do not label flaky until demonstrated.
- **Intermittent failure:** repeat boundedly, preserve seeds/timing/order, look for races/shared state/network dependence. Never use retries as the final repair for a deterministic bug.
- **Dirty workspace:** identify every changed/untracked path and ownership. Remove only proven generated residue. Quarantine uncertain human/agent work.
- **Dependency/tool failure:** verify executable/version/config/auth/environment and distinguish unavailable infrastructure from product correctness.
- **Git/PR mismatch:** compare local HEAD, remote branch HEAD, recorded lifecycle SHA, PR head, base, and origin. Never force-push or rewrite uncertain history.
- **Agent-generated low-quality change:** reject/repair against explicit acceptance evidence; generic helpers, placeholders, invented APIs, and unrelated refactors are not progress.
- **Repeated known fingerprint:** retrieve prior evidence and successful repair. Revalidate applicability; do not blindly replay a fix against changed context.

## General engineering practice

Keep changes bounded and reviewable. Read governing docs and nearby code before editing. Reuse existing abstractions before adding parallel architecture. Treat tests as executable evidence, not obstacles. Separate environment dependence from human dependence. Record exact versions and SHAs when they affect reproducibility. Prefer deterministic state machines and idempotent operations for unattended workflows. Make recovery safe to run more than once. Persist important execution state before crossing irreversible boundaries.

Optimize for product advancement, correctness, maintainability, and learning—not activity volume. A no-op or quarantine is better than an incoherent autonomous PR.

## Learning rule

Experience becomes a capability only after evidence. The preferred progression is:

failure observation -> stable fingerprint -> diagnosis -> bounded repair -> verification -> regression evidence -> recurrence -> generalized repair candidate -> historical/replay testing -> capability promotion -> recurrence-rate measurement.

Never promote a learned behavior that weakens security, privacy, licensing/provenance, Product Reality, exact-head verification, merge gates, destructive-operation boundaries, or explicit human authority.
