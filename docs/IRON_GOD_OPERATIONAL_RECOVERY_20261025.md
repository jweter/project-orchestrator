# Iron God operational recovery — progress report (2026-10-25)

**Runtime status: DEVELOPMENT-ONLY.** Nothing in this report is deployed,
installed, or activated. No scheduled task, owner-control file, launcher,
controller state, or installed release was modified. No worker was started.
Simulated proofs below are isolated fixtures, not a live cycle.

## Authoritative source

| Item | Value |
| --- | --- |
| Installed release (running pin) | `339331c2e535607ea480772a8dad8660417d3557` (`releases\339331c…`, receipt `VERIFIED_STAGED`) |
| Release-lineage repair base | `fix/iron-god-actor-resident-repair-20261025` |
| Canon S2 head integrated | `21b88d02b7eaeb5dd6ac70b5a812fa6d3b52e59f` (9 commits since merge-base `96508cb`; all Canon branches are ancestors) |
| Latest preflight-GREEN head | `51e575b47da7dbb894cd51505860a3df31d2b17d` (`integrate/iron-god-canon-onto-release-20261025`) |
| Top of stack | `fix/iron-god-single-worker-commissioning-20261025` (this report) |

GitHub `main` is not authoritative: it contains neither the release's 34
commits nor Canon. Unreleased branches ahead of `339331c`
(`fix/iron-god-preflight-safety-20261024` +14, `fix/iron-god-worker-throughput-20261024`
+11, `fix/iron-god-owner-remote-preservation-20261024` +3, `fix/iron-god-pin-first-20261024` +2,
`fix/iron-god-one-click-wheel-20261024` +1) are preserved and not yet integrated,
except the two test-only RAM-isolation commits below.

## Completed slices (stacked, linear)

| # | Commit | Slice |
| --- | --- | --- |
| 1 | `9c9e145` | Resident actor: waits while the owner is active instead of exiting (Take the Wheel startup deadlock) |
| 1a | `b355a46` (cherry-pick of `d0c99b2`) | Test-only: controller proofs isolated from live RAM pressure |
| 1b | `a26249f` (cherry-pick of `8cd80a1`) | Test-only: resident dispatch proof isolated from live RAM pressure |
| 2 | `b210bce` | Single fail-closed execution decision (`execution_authority.py`); actor re-checks it before checkout and every cycle |
| 3 | `9066cd3` | Supervisor dispatch requires the same decision (`authorized_dispatch_products`), recorded in `supervisor-execution-authority.json` |
| 4 | `0d3e650` | Actor runner pins `project_orchestrator` imports to its own checkout (foreign editable install) |
| 5 | `7c82177`…`ff9d721` | Canon S2 integrated by ordered `cherry-pick -x` (9 commits) |
| 6 | `51e575b` | Take the Wheel success requires a permitting decision recorded after the resume began |
| 7 | `f158f5f` | Supervisor commissioned with exactly one worker |

### Canon compatibility matrix

| Canon commit | Files | Release overlap | Resolution |
| --- | --- | --- | --- |
| `9bbfd1f` handoffs/escalation | worker_transport, docs | none | applied |
| `e8efb5a` Iron Memory context | canon_worker_memory, docs | none | applied |
| `45414a2` ledger sealing | worker_transport, docs | none | applied |
| `0813bda` state transitions | worker_transport, tests | none | applied |
| `3138006` Job Object fencing | execution_runtime, windows_process_job | none | applied; fixes release's flaky `test_authority_revocation_kills_descendant_tree` |
| `d0737b9` docs | docs | none | applied |
| `ea7a0c6` preflight diagnostics | engineering/preflight.py | release changed comments | auto-merged; both proof stages and diagnostics retained |
| `8607ca7` builder owner-state isolation | tests | release fixed same 4 tests with a fixture marker | took Canon seam injection (no retired-marker fixture) |
| `21b88d0` LOCALAPPDATA isolation | conftest, tests | release fixed same 2 tests | took Canon seam injection |

No release safeguard was weakened: actor local-release pinning, dirty-actor
protection, exact-head checks and post-reset verification are unchanged.

## Test evidence

| Head | Native preflight | Result |
| --- | --- | --- |
| `a26249f` (release + slice 1) | all gates except `tests` PASS | `tests`: 1 failed / 2854 passed — `test_authority_revocation_kills_descendant_tree` (pre-existing release race; fixed by Canon `3138006`) |
| `51e575b` (integration) | **GREEN**, evidence bound to exact head | controller_proofs 18 passed, resident_dispatch_proof 8 passed, tests 3007 passed, 0 failed |
| `f158f5f` + this report | see final preflight | pending at time of writing |

Runs used Windows Python 3.12.10, short per-worktree basetemps, and an
isolated `LOCALAPPDATA` so no test read live owner-control state.

Simulated-cycle evidence (isolated fixtures, real IronGodLocal worker code,
disposable repositories): `tests/test_iron_god_complete_loop_proof.py`,
`tests/test_iron_god_orchestration_caller*.py`,
`tests/test_iron_god_worker_authorization_boundary.py`,
`tests/test_rdi001_resident_dispatch_e2e_proof.py` — all pass. **This is not a
live authorized cycle.**

## Corrections to earlier diagnosis

- The 10 `OWNER_OR_RESOURCE_VETO` failures on the Canon branch were caused by
  live RAM pressure in `resources()` (< 2 GiB free), not by the missing
  exclusivity marker; those tests write their own fixture markers.
- The live IronGodLocal worker `gate()` does still require the retired
  `iron-god-exclusive.json` (see blockers).

## Current first blocker (owner-level)

**No supported owner-authorization contract exists.** Every installed
consumer (Take the Wheel, watchdog, builder, IronGodLocal worker) still uses
the owner marker the owner has retired; no replacement has been defined
(`docs/IRON_GOD_DESKTOP_CONTROL_CUTOVER_AUDIT.md`, requirement 1, unimplemented).
The new execution decision therefore reports
`OWNER_AUTHORITY_CONTRACT_UNDEFINED` and denies. This is deliberate; the
contract must not be invented.

Remaining dependencies, in order:

1. Owner defines the owner-attestation contract (what signal, who writes it,
   how it is verified/authenticated, how revocation works).
2. Owner decides controller activation: `IronGodLocal\controller-handoff-state.json`
   is `shadow_state_only`, `candidate_execution_authorized=false`; nothing in
   source writes that flag.
3. IronGodLocal worker (`local_continuous_worker.py`) is not under version
   control and its `gate()` requires both retired files. It must be brought
   into the repository (or retired) before it can be repaired.
4. Exclusivity policy: `DoWork.txt` declares the Iron God exclusive override,
   but `iron-god-exclusive.json` was retired 2026-10-18; without it the
   legacy supervisor considers all four products.
5. Editable install: Python 3.12 site-packages points `project_orchestrator`
   at `C:\Users\jerem\GitHub\orchestrator-github-evidence-resilience`. Twelve
   installed maintenance scripts under `ExponentialEngine` import it without a
   path; do not uninstall without reviewing them.
6. Supervisor first pass may exceed Take the Wheel's 120 s window before
   writing its execution decision (recertification can be slow); see
   unreleased `9a98832` "bound controller proof deadline to measured Windows
   runtime" when integrating.
7. Integrate remaining unreleased release-lineage branches, rerun exact-head
   preflight, then stage a release through the existing
   `Stage-VerifiedLocalRelease` / promotion procedure (owner-authorized).

## Exact next engineering action

After the owner defines the attestation contract: implement it as an
`OwnerAttestation` provider for `execution_authority.evaluate_execution_authority`
(the only seam that needs to change), with tests for grant, revocation and
replay, then wire Take the Wheel / Give Me Control to produce and revoke it.
