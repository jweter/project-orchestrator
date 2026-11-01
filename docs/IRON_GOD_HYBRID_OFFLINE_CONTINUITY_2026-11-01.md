# Iron God — hybrid local development and GitHub publication authority

**Owner decision: 2026-11-01.** This is the current operating intent for Project Iron God, clarifying (rather than replacing) the original `IronGodLocal/Roadmap.txt` Phases 0–10 and the owner-approved portfolio roadmaps. It does **not** authorize altering a running release or weakening any engineering, safety, or GitHub protection gate.

## Objective

Iron God must develop continuously on Azathoth, publish to GitHub normally, and recover safely from GitHub outages, access blocks, delayed CI, and PR/review failures without stopping independent work. The desired long-run state is that GitHub's released `main` and the locally tracked released baseline converge. A local accepted development baseline may temporarily be ahead, and must remain distinct from what GitHub has actually merged.

**Normal path (GitHub healthy):**

1. Interpret the owner-approved roadmap; use existing capability/issue planning, shared memory, deduplication, dependency resolution, and bounded, capability-matched workers.
2. Create an isolated local worktree/branch and immutable local candidate commit; run exact-head native tests, independent verification, and required real Product Reality checks.
3. Record accepted local development evidence and protect the accepted commit/ref. Keep acceptance separate from claims that a release has occurred.
4. Enqueue a durable publication job, push an appropriately scoped GitHub branch, open a PR, and satisfy fresh CI, review and merge policy.
5. Record the actual GitHub merge/release SHA and synchronization receipt. Apply the merged state to the locally tracked **released** baseline; retain any newer, unpublished accepted local work and its provenance.
6. Select/dispatch the next independently eligible slice without waiting for GitHub, subject to existing resource and safety controls.

**Degraded path (GitHub unavailable, forbidden, blocked, or slow):**

1. Categorize the failure: transient transport outage, authentication/permission failure, rate limit, CI/test failure, review or branch-protection gate, repository divergence, or intentionally forbidden action. Do not treat them all as a retryable network outage.
2. Save the local candidate, review-ready branch, bounded patch/diff, exact-head verification receipt, dependency/parent SHAs, Product Reality evidence, and durable publication queue entry. The local equivalent of a PR is a **review-ready local branch plus evidence bundle**, not a real GitHub PR.
3. Continue safe, independent roadmap work using available authorized capacity. Do not block all repositories because one publication is pending, but do not exceed bounded unpublished/off-device-backup exposure.
4. Do not let a known failing test, unresolved required review, permissions restriction, or ambiguous conflict automatically count as a release. Keep only legitimately accepted development milestones in the accepted-local chain; retain and repair nonaccepted candidates separately.
5. Retry only when the class of failure is safely retryable, under explicit bounded backoff/resource limits. No force pushes, authorization bypass, reviewer bypass, or unbounded queue growth.

**Recovery and convergence:**

1. Re-observe live GitHub default-branch identity, outstanding PRs, review threads, branch protection, and CI. Reconstruct the queue from durable local state, Git refs, verified receipts, and the append-only event journal after any reboot.
2. Compare the common ancestor, remote release SHA, queued accepted SHA, current local accepted baseline, current local released baseline, and dependency order. Treat rewritten/squashed GitHub merge SHAs as different identities even where content is equivalent.
3. Publish in dependency-safe order, one active publication PR per repo unless an independently reviewed policy permits otherwise. Reconcile base divergence in isolated worktrees; protect old accepted refs and evidence; rerun exact-head tests and Product Reality when the integration changes. Never hard-reset an authoritative accepted local ref to remote main, silently discard commits, or force-push.
4. After independent GitHub CI/review/merge, record the actual merged SHA and a release-to-acceptance mapping. Advance the local **released** ref to the verified GitHub main commit. When the queue is drained, assert synchronization only if the observed remote and locally tracked released refs match exactly and there are no unexplained pending differences. If newer accepted-local work exists, report it separately as unpublished rather than claiming everything is identical.
5. If the same code is integrated via a squash/rebase merge, verify content/diff correspondence and preserve the SHA mapping; do not claim the original local accepted commit has magically become the GitHub merge SHA.

## Reuse the existing Iron God implementation; do not design a competing controller

The installed `36fb3aa` release and preserved Git history already contain most intended mechanics:

| Roadmap grouping | Relevant existing implementation | Integration priority |
| --- | --- | --- |
| IG-010–016 durable SQLite/journal/leases | `local_first_state.py`, `local_dispatch_event_claim.py`, `IronGodLocal/*.sqlite` | Reconcile schema/event ownership before any shared writes |
| IG-020–026 Git refs/worktrees | `local_git_refs.py`, `iron_god_candidate_refs.py`, `iron_god_worktree_lifecycle.py` | Use the same protected refs as the actual acceptance selector, not competing ref stores |
| IG-030–036 acceptance/verification | `iron_god_candidate_local_acceptance.py`, `iron_god_orchestration_caller.py`, `iron_god_combined_regression.py` | Prove real product local acceptance with exact-head evidence and no self-certified pass |
| IG-040–049 publication | `iron_god_publication_job.py`, `iron_god_publication_lane.py`, `iron_god_transport_deferral.py`, `iron_god_post_merge_sync.py` | Reuse production GitHub PR/CI/merge behavior; original `iron_god_github_adapter.py` is **contract-only** |
| IG-050–058 recovery | `iron_god_checkpoint_controller.py`, `iron_god_state_reconstruction.py`, `iron_god_protected_work_audit.py` | Reconcile existing checkpoint/backup mechanisms; original `iron_god_offdevice_checkpoint.py` is **contract-only** |
| IG-060–067 planning | `iron_god_capability_graph_parser.py`, `iron_god_slice_decomposition.py`, `iron_god_slicespec_generator.py`, existing supervisor compiler | Coordinate with #594/#595; approved roadmaps authorize goals without routine per-slice owner approval |
| IG-070–077 dispatch | `iron_god_capacity_model.py`, `iron_god_repository_fairness.py`, `iron_god_orchestration_caller.py`, current product lane | One controller/lease authority; prevent duplicated schedulers and workers |
| IG-080–086 Product Reality | `iron_god_product_reality_adapter.py` and family adapters; one real KE Core hybrid-search adapter | Most original family adapters are **contract-only** and must not report real checks as passed |

There is already a composed `run_continuous_development_cycle` in `iron_god_orchestration_caller.py` (commit `f7c66e8`) which calls an existing local acceptance/refill path. The active installed supervisor currently calls `iron_god_product_lane.advance_product_lane` instead. Audit contracts and prove a safe adapter/transition; **never run both mutating controllers against the same repository/leases/ref store**.

The original plan names 72 `IG-xxx` deliverables (IG-001 through IG-086 with intentional gaps), plus publication independence stress and migration demonstrations. The installed source includes 84 `iron_god_*.py` modules; existence or isolated tests are not evidence of active production reachability or real Product Reality. Follow the complete original stage roadmap and its acceptance criteria rather than treating all modules as ready for direct activation.

## Acceptance tests for the hybrid architecture

- GitHub healthy: real roadmap-derived local slice -> independently accepted local commit -> GitHub PR/CI/review/merge -> locally tracked released SHA equals observed GitHub main.
- Outage: two independent real product slices accepted locally, with queued publication receipts, without GitHub or chat prompting the second; no unsafe unbacked backlog buildup.
- Recovery: GitHub returns, queued changes published in order with fresh CI and required review; exact merged identities and acceptance mappings retained; local released/main convergence independently verified.
- Divergence: GitHub independently advances, or squash merges rewrite commit SHA; reconcile without reset, force push, lost local commits or fabricated identity claims.
- Non-retryable block: access/review/CI failure stops only dependent publication and records a real blocker, rather than converting to a bypass.
- Owner takeover, crash, reboot, stale worker lease, duplicate claim and resource pressure all fail closed or recover without corrupting accepted work.
- Multi-product canary: the six registered product repositories get appropriate native verifier/builders; unrelated development continues while another repo awaits GitHub.

## Deployment and status

**Decision documented, NOT installed or production-proven.** Continue using the pinned running release until an isolated branch passes complete native preflight, GitHub CI/review, independent Product Reality where required, release/owner-control handoff, and rollback readiness. Coordinate with in-flight PR #595 and issues #594 and #571 instead of duplicating planning or overwriting protected local state.
