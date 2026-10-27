# Iron God operational recovery — progress report

Last updated: 2026-10-26.

**Runtime status: OPERATIONAL_VERIFIED (2026-10-26).** Release `815cc6b`
completed one real owner-activated development cycle on Azathoth: grant,
acknowledgment, one worker, handback, independent verification,
`ACCEPTED_LOCAL`, VERIFIED memory, then an acknowledged Give Me Control.
Nothing here issues an owner grant; every activation was the owner's click.

## Lineage

One linear stack on the installed release `339331c2e535607ea480772a8dad8660417d3557`
(full history: `git log 339331c..HEAD`). Canon S2 (9 commits) and the
compatible unreleased release-lineage work are integrated; nothing was
blindly merged. GitHub `main` is not the baseline.

| Phase | Commits (top of each) | Result |
| --- | --- | --- |
| Earlier recovery | `9c9e145`…`f926a6b` | resident actor, supervisor/actor gating, import provenance, Canon S2, one worker |
| 1 Owner controller | `25b7413` | authenticated owner commands, supervisor acknowledgment, revocation, replay protection |
| 1 Authority routing | `47511df` | every consumer decides through the controller; retired files grant nothing |
| 2 Desktop wiring | `27400e2` | Take the Wheel / Give Me Control / watchdog / readiness through the controller |
| 3 Worker migration | `87faae2`, `c07c2bf` | legacy IronGodLocal worker under version control; gate = execution decision |
| 4 Canon lane | `44dd00a`, `87c216b`, `3d05656` | single-worker local cycle with independent verification, memory, resources, lease |
| 6 Unreleased lineage | `5bb091f`…`f754cf4` | throughput, memory admission, preflight timing, staging/promotion (owner-controller), one-click desktop |
| Commissioning exclusivity | `06521e1`, `443f9e5` | actor bridge stays resident; legacy merge watcher, Linear reconciliation and evolution readiness suspended while commissioning is declared |

## Owner-control contract (implemented)

* **Take the Wheel**: an authenticated command (HMAC under a DPAPI key for the
  enrolled owner SID; control directory ACL = owner + SYSTEM) that must come
  from the active console session via the desktop icon chain, is bound to the
  pinned release SHA, uses a strictly increasing generation, and becomes
  effective only when the running supervisor acknowledges it within 180 s.
  The grant is bound to that supervisor process (pid + start time) and dies
  with it; restarts, watchdogs and tasks cannot revive it. Max age 24 h.
* **Give Me Control**: revokes immediately (any owner process may revoke),
  the supervisor acknowledges within its 2 s pacing poll, then the engine is
  stopped with remote access preserved.
* The retired `human-mode.json` is written only as a stop signal for the
  unmigrated SYSTEM guardians in `C:\ProgramData\AzathothControl`; it is read
  nowhere for authority. `iron-god-exclusive.json` is replaced by the
  versioned `engineering/iron-god-commissioning.json`.
* Threat-model limit: code already running as the owner's account can use
  DPAPI as the owner, just as it could click the icon.

## Development cycle (implemented, simulated end-to-end)

Supervisor pass → authority for this supervisor + release → resources
(memory admission, 25 GiB disk) → exclusive `resource:ollama` lease → plan
the next dependency-ready slice into a Canon packet (bounded memory context)
→ isolated worktree of a dedicated local clone at the exact base → ONE worker
(`scripts/iron_god_canon_worker.py`, aider-ollama in a kill-on-close Job
Object, authority re-checked continuously) → handback → supervisor
independently re-verifies the exact head in a fresh detached worktree
(scope, artifacts, commands, hidden acceptance test) → `ACCEPTED_LOCAL` on
`refs/iron-god/accepted/<packet>` or `BLOCKED` → VERIFIED Iron Memory lesson
on acceptance. No merge, push, PR or release. First slice:
`CANON-COMMISSION-001` (Canon ledger summary helper).

Simulated proof (`tests/test_iron_god_canon_lane.py`): real Git, worktrees,
pytest and ruff with a stand-in agent reach ACCEPTED_LOCAL with memory; a
worker whose weak tests pass with wrong code is BLOCKED by the hidden test.
**This is not a live cycle.**

## Findings on Azathoth that need the owner

1. Remote Desktop Commander is paused by SYSTEM guardians whenever the legacy
   marker says owner control (RemoteBridgeGuardian/RemoteBridgeSupervisor).
   Fixing them needs an administrator change to `C:\ProgramData\AzathothControl`.
2. 877 leaked `OpenConsole.exe` processes (created 2026-10-24 20:00–21:00 by
   svchost PID 1792) hold ~1.7 GB RAM; a reboot reclaims it.
3. A Python 3.12 editable install points `project_orchestrator` at
   `orchestrator-github-evidence-resilience`; pinned components are immune;
   twelve installed maintenance scripts depend on it, so it is left in place.

## Installation history

* `f9e6302`: exact-head native preflight GREEN (28/28 checks, 3109 tests),
  staged and promoted with pinned tasks disabled; desktop controls installed
  from the release (shortcut self-test 15/15).
* After installation, review found legacy merge-watcher/Linear/readiness
  activity still reachable during commissioning; fixed in `443f9e5`, which
  supersedes `f9e6302` through the same preflight/stage/promote path.

## Live commissioning (2026-10-26)

| Owner action | Release | Outcome |
| --- | --- | --- |
| Take the Wheel (gen 1) | `b1f3d02` | one worker; `PARKED` ENVIRONMENT_FAILURE: aider console UnicodeEncodeError on cp1252 captured stdout |
| Give Me Control (gen 2) | `b1f3d02` | revocation acknowledged; fixed in `6c76538`/`6526a11` (UTF-8, remediation-gated bounded retry) |
| Take the Wheel (gen 3) | `6526a11` | retry attempt 2; `PARKED` TimeoutExpired: 16-22k token prompts on a CPU-only 7B |
| Give Me Control (gen 4) | `6526a11` | revocation acknowledged; capability ladder run with the engine off |
| Take the Wheel (gen 5) | `815cc6b` | 001 cancelled as superseded; `CANON-COMMISSION-002` **ACCEPTED_LOCAL** at `dde4a48` in 5.5 min |
| Give Me Control (gen 6) | `815cc6b` | revocation acknowledged in 0.24 s; receipt `OWNER_CONTROL_GRANTED` |

Capability ladder (engine off, scratch clone, hidden acceptance tests),
i7-13700H CPU-only: qwen2.5-coder:7b reads ~60 tok/s and writes 6-9 tok/s;
1.5b is fast but misplaced files; qwen3.5:9b is too slow. With a ~900-token
prompt the 7b builds the full commissioning slice in 62-96 s (4/4 runs,
including 2/2 through the exact production worker path). Root causes fixed
in `815cc6b`: prompt named forbidden files (aider pulled them in), shared
pytest temp link access-denied, and an ambiguous ledger shape in slice 001.

## Calibration mode (`f713a7a`)

`python scripts/iron_god_calibration.py [--runs 3]`, run from the installed
release while the owner holds control, measures the exact production worker
path over a versioned three-level ladder (hidden acceptance tests, scope
check, failed-run evidence kept under `canon-workspaces/calibration/evidence`)
and writes `flow-state/iron-god-capability-profile.json`, bound to model and
hardware. It refuses while a grant is live, a Canon worker is active or the
model lease is held, and aborts if Take the Wheel is clicked mid-run. Every
commissioning slice declares `capability_level`; without a matching profile
the lane reports `WAITING_FOR_CALIBRATION` and dispatches nothing.

Measured (isolated state, 3 runs/level): levels 1-3 pass 9/9, median
32/48/86 s. Planning rule from the evidence: state every required behaviour
as an explicit acceptance criterion (a requirement stated once in prose
passed 0/3; as a criterion 3/3).

## Planned slices 003-005 (`cca2138`, `3f88626`)

New-module slices at the calibrated level, each validated through the exact
production worker path from its real base before installation: 003 (ledger
report on the accepted 002 helper, dispatched from 002's accepted head) 3/3,
004 (ledger attention) 3/3, 005 (capability profile report) 2/2.

Slice-writing rules for the local 7B worker, each from failed-run evidence:

1. Every required behaviour is also an explicit acceptance criterion.
2. Derived values and conditionals are given as exact expressions with an
   example (prose versions failed 0/2 and 0/3).
3. The worker's own tests are listed as exact cases (inputs and expected
   values); otherwise it can write a wrong expectation for correct code.
4. Name only the target files; never name other files.
5. Keep each objective and criterion within Canon's 1200-character bound
   (enforced by a test over the whole policy).

## Portfolio rotation (`a3444e4`..`02e8552`, installed disabled)

Owner decision 2026-10-26: every portfolio product repository, random first
pick then round-robin, each turn from GitHub `main`, published by pull
request that must pass the repository's CI before a squash merge
(`portfolio_rotation.merge: auto`; `review` holds green PRs for the owner),
with a shared working memory board. project-orchestrator is excluded.

A product turn plans and builds in two short steps sized to the 7b: one
planning call returns a structured plan (one pure function in a new module of
an existing package, literal examples with reasoning) validated before any
code; the worker writes the tests from the examples; aider builds only the
module; strays are discarded, the repository's formatter and mypy run,
then the repository's canonical preflight in a persistent per-repository
environment. Owner-held trials (`scripts/iron_god_product_trial.py`, never
publishing) measured each change on real repositories overnight.

Readiness on unchanged main (`--preflight-only`): rocksmith,
knowledge-engine-core and knowledge-engine-ai ready; knowledge-engine-web
(mypy) and VoiceStudio (86 backend tests) fail before any change; Everward
has no Python package (Unreal/C++) and is not eligible.

Measured: about 1 in 5 turns passes end to end (2-5 minutes each); the
dominant remaining failure is the planner's self-contradictory examples.
qwen2.5-coder:14b as planner was 3-5x slower with the same failure class.
Gates prove correctness, not value: the first passing slice was a correct
but unused helper.

## Next action

Owner decision on `portfolio_rotation.merge` (auto vs review), then Take the
Wheel on `02e8552`; integrate the local Iron God lineage with GitHub as a
reviewed milestone.
