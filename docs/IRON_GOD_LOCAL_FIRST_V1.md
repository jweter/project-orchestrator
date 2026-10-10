# Iron God local-first v1 — authority contract and staged migration

Status: Phase 0 design contract; Phase 1 transactional foundation in shadow mode.
This document accompanies the first bounded implementation slice, IG-010–IG-013.
It **does not** claim the production engine has migrated.

## Scope and source authority (IG-001)

1. Owner TAKE THE WHEEL is the absolute execution veto. Malformed, missing, or
   unreadable owner-state evidence fails closed in **new local-first services**.
2. Product repositories control product architecture, native tests, Product Reality,
   provenance, licensing and actual functionality.
3. GitHub main is the released and independent-CI authority; the local accepted
   baseline is development authority **only after its integration controller is
   implemented, verified and enabled by a controlled migration**.
4. Candidates are worker-owned immutable commits in isolated worktrees.
5. Integration alone may advance the local accepted baseline, after exact-head
   verification and Product Reality where required.
6. Publication transport is separate and has no authority to revoke locally
   accepted code or block unrelated safe slices.

Existing production schedulers, publication controllers, owner gate, and GitHub
checks retain their present authority during this implementation.

## SliceSpec v1 contract (IG-002)

Required fields: `slice_id`, `repository`, `objective`, `capability_id`,
`baseline_sha`, `dependencies`, `acceptance_criteria`,
`allowed_paths`, `forbidden_paths`, `runner_id`, `runtime`,
`resources`, `product_reality_policy`, `expected_artifacts`,
`risk_class`, `verification_policy`, `estimated_conflict_scope`.

An omitted or conflicting field blocks claims; no worker may create authority
from an inferred field or from an expired/stale ownership assertion.

## Milestone and verification contract (IG-003)

A locally accepted milestone will require: `milestone_id`, `repository`,
`candidate_sha`, `parent_accepted_sha`, `accepted_sha`,
`slice_ids`, `verification_receipt`, `product_reality_receipt_or_exemption`,
`accepted_at` and a protected Git milestone ref.

Verification receipts must identify exact head and tiers actually executed.
Unavailable or human-only Product Reality is never synthesized as a PASS.

## Publication contract (IG-004)

Each job will retain: `publication_id`, `repository`, `milestone_id`,
`accepted_sha`, `parent_accepted_sha`, `github_base_sha`,
`verification_receipt`, `product_reality_receipt`, `required_ci`,
`required_review`, `publication_branch`, `attempts`, `state`,
`last_error`, `next_retry_at`.

At most one active publication PR per repository in v1. Later accepted
milestones remain queued and unrelated development continues.

## State transitions (IG-005)

Worker: READY -> CLAIMED -> IMPLEMENTING -> LOCAL_COMMIT -> VERIFYING ->
CANDIDATE -> ACCEPTED or REPAIR_REQUIRED.

Publication: READY_TO_PUBLISH -> PUSHING -> BRANCH_PUBLISHED -> PR_OPEN ->
CI_PENDING -> REVIEW_PENDING -> MERGE_READY -> MERGED -> RELEASE_SYNCED.

Publication deferrals include PUBLICATION_DEFERRED, CI_FAILED,
REVIEW_CHANGES_REQUIRED, BASE_DIVERGED, TRANSPORT_BLOCKED and
MANUAL_POLICY_GATE. Deferral **does not** roll back a local milestone.
Uncertain transitions block mutation, retain evidence and generate CAPA.

## Owner control and crash invariant (IG-006, IG-008)

Every mutating operation in new local-first services must have a durable,
explicit owner-control path and must verify literal `enabled: false`;
missing, malformed, active, or inaccessible markers prevent mutation.
No service may flip the marker, silently delete a lease, or overwrite an
unresolved candidate. No owner-control bypass for post-reboot recovery.

A resource lease is never stolen just because its heartbeat is old; a
separate recovery controller must prove prior owner process identity is
dead, preserve relevant commits, and record a reconciliation transition.
SQLite state is reconstructable from Git refs, commits, receipts and the
append-only event journal; the database itself does not prove product work.

## Storage safety (IG-007)

The storage governor must reserve operating headroom and throttle expensive
new work under pressure. Only recognized, unowned, regenerable generated
data may be collected after durable-checkpoint checks. Never remove
Documents, Pictures, Rocksmith data, user workspaces, unresolved worktrees,
unknown files, or sole copies of candidate/accepted commits.

SQLite: local file, WAL journaling, FULL synchronization for critical writes,
short transaction scopes, explicit versioned migrations, integrity checks,
and independent periodic backup. The journal must resist UPDATE and DELETE.
Every lease claim/release and later acceptance/publication transition must
emit an event **in the same transaction** as its state mutation.

## Metrics baseline (IG-009)

Measure (do not infer) accepted usable product milestones/week, roadmap
capabilities/week, accepted-to-PR and accepted-to-merge latency,
productive worker utilization, idle eligible slots, failed transitions,
stale lease incidents, checkpoint exposure, storage free-space,
repeat failures, and human-required interventions. UNKNOWN stays UNKNOWN.
An orchestrator PR or test count is not a product milestone.

## Controlled migration and build gate

This slice introduces a **shadow-only** `LocalStateStore` with database
migration v1, immutable events, strict owner-control checks and
single-resource lease claims. It is not attached to the existing worker,
integration, release, or GitHub publication controllers.

Before canary activation, review live owner-control deployment and current
worker launch paths, runner manifests/capacity, repository-specific native
preflight gates, Product Reality adapters, storage/recovery boundaries,
and current GitHub publication behavior. The present branch documents their
required interfaces; it does not certify those later deployment gates.

Gate for this slice: isolated worktree based on live GitHub main; deterministic
tests (idempotency, concurrent claims, strict owner veto, immutable journal,
reopen recovery and rollback); exact-head full repository preflight; review,
CI and merge on unchanged GitHub main policy. Never merge on a test-only pass.

## Next slices

IG-014/015/016: state health and transactional snapshot/backup/JSON export;
IG-020–026: protected local Git candidate/accepted/milestone/recovery refs
and orphan-worktree reconciliation; IG-030–036: exact-head integration
verification and atomic local accepted-baseline promotion; IG-040–049:
durable publication jobs/transport and independent retry controller.
