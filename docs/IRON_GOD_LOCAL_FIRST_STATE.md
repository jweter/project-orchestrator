# Iron God — Local-First State Foundation (IG-010/IG-012)

Status: isolated local engineering candidate; NOT deployed, published, or integrated.
Parent commit: 9bc3d3c83d36f0a93a20ede86c54f9a0cbd65113
Branch: `iron-god/local-first-state-ig010`

## Operating intent

- Develop each of the six active product repositories on Azathoth.
- Local Git worktrees, exact-head repository-native verification, integration,
  and Product Reality determine *local* milestone eligibility.
- Publish verified major milestones through a separate durable queue.
- GitHub push/PR/CI/review/merge is still required for a remote release.
- A blocked transport must never revert an accepted local milestone or block
  another independent eligible development task.
- Do not treat a successful SQLite insert as proof that acceptance happened.

## Implemented in this candidate

`src/project_orchestrator/local_first_state.py` provides a local SQLite
persistence primitive with WAL, FULL synchronous writes, atomic schema setup,
schema-version rejection, integrity checks, a monotonic event journal, and an
independent publication-state table. It enforces explicit allowed transitions,
idempotent acceptance recording, divergent-replay rejection, and at most one
active publication per repository.

When publication moves from PUSHING to DEFERRED, the accepted milestone remains
intact and further independently verified milestones may be recorded.

State transitions require an exact expected state under BEGIN IMMEDIATE. Lost
races fail closed. This store makes no GitHub requests and has no network
dependency.

## Deliberate limits

- The acceptance-writing API is **internal only**. It is not wired to workers.
- Caller MUST first prove immutable local candidate and milestone Git refs,
  exact-head repository-native preflight, integration against the latest
  protected accepted baseline, and mandatory Product Reality.
- Product Reality NOT_REQUIRED is allowed only when repository-specific
  authoritative policy expressly declares it unnecessary.
- Verification SHA-256 is a receipt content identifier, not independently
  verified by this storage layer.
- The Git integration controller, worker leases, recovery scanning, backup,
  and GitHub publisher are future phases. Do not claim autonomous acceptance,
  safe unattended operation, or automatic release from this candidate.
- User TAKE THE WHEEL must veto all future live actors and controllers
  before any mutating operation. Never remove or toggle human-mode.json
  automatically. Reconcile the source-versus-runtime ownership gate before
  promotion.
- No protected refs, GitHub branches, PRs, schedules, worker configurations,
  or user-owned files have been changed by this state-layer candidate.

## Verification policy

Repository-native verification for project-orchestrator is
`python engineering/preflight.py`; it covers control-plane validation,
security/architecture policies, compile, tests, and CLI checks. Focused tests
also check crash/reopen durability, input validation, atomic journal failures,
publication deferral, idempotency, concurrency, and state-fence rejection.
A changed Git head invalidates prior exact-head evidence.

## Next build gates

1. IG-011: explicit forward-only SQLite migrations and state backup.
2. IG-020/026: protected local Git ref creation and orphan worktree recovery.
3. IG-030/036: verified exact-head integration receipts and native preflight.
4. IG-040/049: publisher transport with retry/backoff and independent CI.
5. IG-050+: storage governor, off-device checkpoints, and owner-control gates.
6. One-repository shadow/canary proof; then staged rollout across all six.

Keep the existing active factory untouched until the new pipeline has proven
an outage test and rollback/owner-control recovery test.
