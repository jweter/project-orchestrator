# Iron God operational recovery — progress report

Last updated: 2026-10-26.

**Runtime status: installed, disabled, awaiting the owner's Take the Wheel.**
The owner's architecture decisions of 2026-10-25 are implemented in source.
Nothing here has issued an owner grant; activation requires the owner's
Take the Wheel.

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

## Next action

Owner activation through Take the Wheel on the latest promoted release.
