# Issue #597 - integration proposal: historical local-first primitives behind the product lane

Status: PROPOSAL for review. Nothing in this document changes runtime behavior, the pinned `36fb3aa` release, owner
control or any task. Evidence below was gathered read-only from `main` at `9c78a10` and reproduced in scratch
repositories. It builds on `IRON_GOD_HISTORICAL_STAGE_REUSE_AUDIT_2026-11-01.md` and
`IRON_GOD_HYBRID_OFFLINE_CONTINUITY_2026-11-01.md` and does not repeat them.

## 1. Decision frame

* The **product lane is the only dispatcher** (`supervisor_runtime` -> `iron_god_product_lane.advance_product_lane`).
  The historical controller stays `shadow_state_only`; `run_continuous_development_cycle` is **never** run beside it.
* Historical primitives are reused **behind** the lane, one transition at a time, each with one owner of the mutation.
* No release-level migration happens before the regression tests listed in section 8 exist and are green.

## 2. Verified findings

### 2.1 Accepted-ref namespace conflict (reproduced)

| Writer | Ref | Meaning |
| --- | --- | --- |
| product lane (`iron_god_product_lane.py`, accept step) | `refs/iron-god/accepted/<packet-id>` | one immutable anchor per verified packet |
| `LocalGitRefs.accepted()` / `bootstrap_accepted()` | `refs/iron-god/accepted` | one moving cumulative baseline |
| `LocalGitRefs` kinds, `iron_god_candidate_refs`, `iron_god_milestone_refs` | `refs/iron-god/{candidate,milestone,recovery,publication}/<id>` | per-kind anchors |

Git cannot hold `refs/iron-god/accepted` and `refs/iron-god/accepted/<x>` in one repository, in either order:
`cannot create 'refs/iron-god/accepted' ... 'refs/iron-god/accepted/<x>' exists` and the reverse. There is no
conflict today only because the two never share a repository.

**Proposal:** keep the lane's per-packet anchors (they are live and recorded as `accepted_ref` evidence). Move the
cumulative baseline to a name outside that directory: `refs/iron-god/baseline/accepted`. The historical consumers of
the old name are shadow-only code and tests (no live consumer), so the rename is the smaller change. The regression test
in section 8 fails today (expected failure, strict) and turns green when the rename lands.

### 2.2 State ownership: two ledgers, different shapes

* Lane (authoritative, JSON, atomic): packet ledger (`... INDEPENDENT_VERIFICATION -> ACCEPTED_LOCAL | BLOCKED`),
  `publications.json` (`PUBLISH_PENDING`, `AWAITING_CI`, `CI_GREEN_AWAITING_OWNER`, `AWAITING_MERGE_STATE`,
  `REPAIR_PENDING`/`REPAIRING`, `AWAITING_OWNER_REVIEW`, `OPEN_BLOCKED`, `MERGED`, `BLOCKED`, `CANCELLED`),
  `backlog-state.json`, `rotation.json`, `active.json`, working memory, the new `roadmap-refill.json`.
* Historical (`LocalFirstState`, SQLite schema v2): `milestones` (accepted SHA, verification hash, Product Reality
  `PASSED|NOT_REQUIRED`), `publications` (`READY, PUSHING, PR_OPEN, CI_PENDING, CI_FAILED, REVIEW_PENDING, MERGE_READY,
  MERGED, RELEASE_SYNCED`, plus `DEFERRED`), an append-only `journal`, and two-phase `acceptance_intents`
  (`PREPARED -> COMMITTED`, at most one pending per repository = the crash-recovery hook).

Mapping (lane -> SQLite): `PUBLISH_PENDING` -> `READY`/`PUSHING`; `AWAITING_CI` -> `PR_OPEN`/`CI_PENDING`;
`CI_GREEN_AWAITING_OWNER`/`AWAITING_MERGE_STATE` -> `REVIEW_PENDING`/`MERGE_READY`; `REPAIR_*` -> `CI_FAILED`/
`REVIEW_PENDING`; `MERGED` -> `MERGED`. **No lane equivalent exists for `DEFERRED` or `RELEASE_SYNCED`**, and SQLite has no
`OPEN_BLOCKED`/`BLOCKED`/`CANCELLED`. The two machines are not isomorphic, so a naive mirror would encode lossy guesses.

**Proposal (recommended, Option B - no second ledger):** do not mirror into SQLite. Add the two missing concepts to the
lane's own publication machine - `PUBLISH_DEFERRED` (entered on a classified transport outage, decided by the existing
IG-040..049 decision primitives in `iron_god_publication_job`) and `RELEASE_SYNCED` (local released baseline equals
observed GitHub main) - and rebuild lost lane state from protected refs plus `publications.json` using the existing
`iron_god_state_reconstruction`/recovery report. **Option A (fallback):** SQLite becomes a write-through journal behind
the lane, written at the same transitions, with a divergence detector. A is only worth its dual-ledger risk if the
reviewer wants the existing SQLite recovery tests to apply unchanged.

### 2.3 Leases, claims and single-dispatcher authority

The lane serializes the local model with `RuntimeLeasePool` (one `WORKER_LEASE_ID`), records the running worker in
`active.json`, and refuses dispatch for a repository with an open publication. The historical loop uses its own
`claims_path`/`lease_path` and `dispatch_existing_accepted_event` -> `local_continuous_worker.execute_reserved_dispatch_claim`.
Running both would give two claim systems over one model slot. **Proposal:** the lane's lease is the only dispatch
authority; historical claim code is not called. What is reused is its *pure decisions* (allocation gate, failure
classification, stalled-worker recovery) as inputs to the lane's own dispatch.

### 2.4 Cumulative accepted baseline vs individually verified commits (the real offline blocker)

The lane verifies each packet's head on `origin/main` (`sync_clone` fetches and uses `origin/main` as the base every
time). The historical loop is cumulative: slice N+1's base is the accepted SHA of slice N
(`handoff["exact_base_sha"] == accepted_sha`). While GitHub is reachable the lane's model is correct, because accepted work
reaches `main` within about 16 minutes. During an outage it is wrong: the next packet would build on stale `origin/main`,
duplicating or conflicting with accepted-but-unpublished work. **Proposal:** choose the packet base per repository as
the newest protected accepted anchor that descends from `origin/main` when that repository has unpublished accepted work
(`PUBLISH_DEFERRED`), otherwise `origin/main`. On recovery, publish in order, re-observe `main`, and when a base diverged
rebase in an isolated worktree (no reset, no force), re-verify at the new exact head, and record both the local accepted
SHA and the squash/merge SHA with an explicit mapping (they are different objects).

### 2.5 Protected refs and publication receipts

Lane anchors are written with `update-ref` without owner-fenced creation; `LocalGitRefs` creates anchors once with an
expected-zero old value, an owner-control veto and idempotent same-SHA retry. **Proposal:** the lane writes its anchors
through `LocalGitRefs.protect` (new kind `accepted`) so anchors are immutable and owner-fenced, and receipts
(`accepted_sha`, `verification_sha256`, `merged_sha`, PR URL, CI evidence) are added to the lane's publication record.

### 2.6 Outage, recovery, crash and reboot

Lane state is atomic JSON and survives a crash, but the lane has no outage mode (a failed push or `gh` call leaves the
publication open and retried), no ordered catch-up and no drain check. Historical `acceptance_intents` give two-phase
acceptance but only for the SQLite path. **Proposal:** classify transport failures (reuse
`iron_god_failure_classification`), defer instead of retry-storming, keep unrelated repositories running, bound disk/RAM
exposure with `iron_god_allocation_gate`, and on reconnect reconcile in order. Crash points to test: after the anchor but
before the ledger write (anchor without `ACCEPTED_LOCAL`), after `ACCEPTED_LOCAL` before the publication record,
mid-push, after merge before local record, and reboot with a held lease.

### 2.7 Product Reality adapters (verified by reading the code)

| Adapter | State |
| --- | --- |
| `iron_god_product_reality_knowledge_engine_core` | real: executes domain code, but covers one feature (hybrid search) |
| `iron_god_knowledge_engine_adapter`, `_rocksmith_adapter`, `_everward_adapter`, `_voicestudio_adapter` | contract only: each raises `NotImplementedError` |
| `iron_god_github_adapter`, `iron_god_offdevice_checkpoint` | contract only: raise `NotImplementedError` |

The working GitHub publisher is in the product lane (`gh`), and it must be reused, not the stub. Until a family adapter
exists, a slice's Product Reality state is `NOT_REQUIRED` only where the repository declares no Product Reality lane;
it must never be recorded `PASSED` by a stub.

## 3. Minimal sequence (each step its own PR with tests; none changes the installed release)

1. Rename the cumulative baseline ref and turn the namespace test green (section 8, test 1).
2. Lane writes anchors through `LocalGitRefs.protect(kind="accepted")`; receipts added to the publication record.
3. `PUBLISH_DEFERRED` + `RELEASE_SYNCED` in the lane's publication machine, using the IG-040..049 decisions; ordered
   drain with baseline-aware packet base selection (2.4).
4. Crash/reboot/interrupted-acceptance recovery tests against real git repositories (section 8).
5. Mock outage/restore canary on scratch repositories, then ONE controlled product canary with a real worker and GitHub
   deliberately unreachable, then the six repositories. Only then a release candidate and the owner's separate release
   procedure.

## 4. What this proposal does not authorize

Installing a release, starting or stopping the supervisor, changing owner control or scheduled tasks, activating the
historical shadow controller, weakening any gate, or deleting local work. Those remain the owner's decisions.

## 8. Regression tests

1. `tests/test_iron_god_ref_namespaces.py::test_no_ref_name_is_a_directory_of_another` - strict expected failure until
   the baseline ref is renamed (2.1). `::test_git_refuses_the_two_accepted_conventions_in_one_repository` documents the
   real Git behavior and passes now.
2. (step 2) anchors are immutable and owner-fenced when written by the lane.
3. (step 3) deferral on classified outage, ordered drain, base selection from the newest descendant anchor, squash-SHA
   mapping, and "no gate bypass on CI/review failure".
4. (step 4) each crash point in 2.6 recovers without losing a candidate, double-accepting or duplicating a publication.
