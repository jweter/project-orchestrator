# Iron God — Authoritative Roadmap Execution Ledger

Authority: \`Roadmap.txt\` in Project Iron God sources (Phase 0–10;
IG-001 through IG-086; three major milestone demonstrations). This ledger
records verified engineering outputs; it does not replace or redefine that
roadmap.

## Status legend

- \`[x]\`: implementation exists in local candidate, full orchestrator preflight
  has passed on its exact Git commit (does **not** mean production deployed)
- \`[ ]\`: not yet independently accepted or fully verified on exact Git head
- Production cutover: **NOT STARTED**. Seven old Windows worker tasks are
  intentionally disabled for cleanup and controlled transition.
- Major milestones 1/2/3: **NOT DEMONSTRATED**.

## Phase 0 — Authority and safety specification

- [ ] IG-001–009: complete and independently review all authority,
  state-machine, safety, storage, recovery, schema, and metrics contracts.

## Phase 1 — Durable local state

- [x] IG-010: isolated SQLite WAL / FULL durability foundation (candidate).
- [ ] IG-011: forward-only schema migrations.
- [x] IG-012: transactional append-only journal for milestone/publication state
  in the candidate foundation.
- [ ] IG-013–016: leases, health, backup, compatibility exports and crash suite.

## Phase 2 — Local Git authority

- [x] IG-020–024: candidate immutable refs, milestone/recovery refs, accepted
  baseline compare-and-swap and owner-control veto; verified locally.
- [ ] IG-025–026: worktree lifecycle and orphan scanning.

## Phase 3 — Verification and integration

- [ ] IG-030–031: strict exact-head FULL preflight receipt validation, candidate
  ref matching, fail-closed Product Reality policy; needs final exact-head
  repository-native preflight on current candidate.
- [ ] IG-032–033: isolated integration staging and Git conflict detector
  implemented locally; focused two-candidate and owner-veto tests passed.
  Await exact-head FULL preflight before marking verified.
- [ ] IG-034–036: evidence-gated accepted-baseline advancement, repair
  routing, combined regression verification and full Phase 3 demonstration.
  - ~~IG-034A crash-recovery classification~~ — focused verified: 9/9
    real-Git/SQLite tests pass, including missing accepted-ref and owner-marker
    fail-closed cases.
  - ~~IG-034B evidence-gated prepare/CAS/finalize controller~~ — implemented
    and focused verified: 6/6 tests pass, including crash replay after Git CAS,
    receipt-byte pinning, Product Reality refusal, and owner takeover veto.
  - ~~IG-034C combined Phase 3 focused regression~~ — 70/70 tests pass across
    local state, Git refs, exact-head verification, recovery, and acceptance.
  - [ ] IG-034D exact-head FULL repository-native preflight on a committed
    candidate, with required Product Reality evidence where policy requires it.
  - [ ] IG-035–036 repair routing, combined regression/full Phase 3 demo.
  No production accepted-ref advancement, release authority or Product Reality
  proof has been claimed; the older FULL receipt still applies only to committed
  d29ca147ab12d71e368b969b5b89b1547cfa2c80.

## Phase 4 — Publication

- [ ] IG-040–049: local job model/state machine are foundation only. GitHub
  transport, CI/review, safe merge, retry and outage demonstrations remain.

## Phase 5 — Storage and recovery

- [ ] IG-050–058: storage governor, safe GC, checkpoints, off-device recovery
  and crash/reboot reconstruction. Manual cleanup achieved 50+ GB free Oct 10;
  that does not count as implementing an automatic storage governor.

## Phase 6–8 — Capability, scheduler, Product Reality

- [ ] IG-060–067: capability and roadmap compiler.
- [ ] IG-070–077: event-driven scheduling, capacity, repair/refill.
- [ ] IG-080–086: Product Reality adapters and evidence.

## Phase 9–10 — Stress and controlled migration

- [ ] Outage/crash/reboot/storage/owner-control stress matrix.
- [ ] Shadow mode → candidate mode → local acceptance → publication canary →
  multi-repository rollout → retire legacy → full authority.

## Major demonstrations

- [ ] Milestone 1: GitHub outage cannot stop development and two milestones
  publish in order after transport recovers.
- [ ] Milestone 2: accepted slice → PR → CI → safe merge → next slice with no
  user "continue".
- [ ] Milestone 3: all four product families advance autonomously under
  the complete local-first system.

## Safe operating facts

- Main implementation worktree:
  \`C:\Users\jerem\Documents\GitHub\_iron-god-localfirst-20261010\`
- GitHub publishing deliberately deferred until major verified milestones.
- All six product repos retain their own preflight and product requirements.
- Protect owner-control marker and dirty worktrees; retain a local checkpoint
  before any integration, and ensure off-device recovery before depending on
  local-only accepted commits.
- Prevent duplicate \`.venv\` builds and stop heavy work below 25 GiB free.
- Every hourly run should update this ledger with exact commits/evidence and
  identify the next unblocked IG ID. Never cross out unverified claims.
