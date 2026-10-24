# Iron God desktop control cutover — read-only source audit

This is **diagnostic evidence**, not an authorized runtime cutover, owner-control
change, or instruction to bypass previous platform denials.

## Evidence from Azathoth (2026-10-24)

The non-executing audit of eight known installed control scripts reported:

| Installed script | Retired control-state filename reference | Other finding |
| --- | --- | --- |
| `DesktopControlSilent.vbs` | line 13 | both desktop icons still invoke this wrapper |
| `Enter-HumanMode.ps1` | line 13 | lines 85, 87, 90 can stop/disable Remote Desktop Commander |
| `Take-The-Wheel.ps1` | lines 15, 53 | still uses legacy readiness and startup |
| `Watch-ContinuousFlow.ps1` | line 2 | legacy watchdog checks |
| `Check-Development-ResumeReady.ps1` | line 16 | requires retired control state |
| `ModeController/ModeCore.ps1` | line 5 | advisory mode controller not wired to engine |
| `ModeController/Set-IronGodMode.ps1` | none directly | consumes `ModeCore.ps1` |
| `ModeController/Test-IronGodMode.ps1` | none directly | consumes `ModeCore.ps1` |

The audit **did not read the retired state file**, invoke either desktop
shortcut, change a control script, alter scheduled tasks, read/write protected
authorization fields, stop remote transport, or start workers.

The authoritative IronGodLocal controller was observed as
`mode=shadow_state_only` and `candidate_execution_authorized=false`.
Both `ExponentialEngine-ContinuousFlow-Watchdog` and
`ExponentialEngine-LaptopActor` were disabled. No active supervisor/actor
process was found in the associated process observation.

## Re-run read-only source audit

From this repository/worktree, with local `PYTHONPATH=src`:

```powershell
python -m project_orchestrator.iron_god_control_consumer_audit --engine-root "$env:LOCALAPPDATA\ExponentialEngine" --controller-state "$env:LOCALAPPDATA\ExponentialEngine\IronGodLocal\controller-handoff-state.json"
```

Exit code 3 means a blocker was found; 2 means the known script sources were
scanned but **cutover remains uncommissioned**. Neither means that execution is
authorized. No production installation or special privileges are required for
source inspection.

## Production cutover acceptance requirements (unimplemented)

1. Identify the currently supported **authoritative owner-control service**.
   The retired filename cannot become a fallback, replacement, or veto.
2. Establish independent, reviewed and permitted producer/consumer integration
   for both actual shortcuts. Commands need unique IDs, per-command engine
   acknowledgments, exact supervisor generation and release identity, and a
   current owner-control/authorization recheck.
3. **Give Me Control:** demonstrate prompt pause/worker drain with genuine
   acknowledgment while preserving remote-control transport.
4. **Take the Wheel:** validate current, accepted local release, exact source
   identity and independent controller authorization before any actor starts.
   Refuse shadow mode, unverified owner authority, Rocksmith gaming veto,
   stale receipts, duplicate supervisor instances, or unproven leases.
5. Validate guardian/ProgramData/startup and any other consumers not covered
   by the eight-script audit. No proof of complete consumer inventory exists.
6. Exercise button-to-engine **end-to-end** acceptance using a separately
   authorized interface. Neither new read-only evidence function authorizes
   execution; these routines cannot replace a real controller acknowledgment.
7. Use repository-native exact-head preflight and deployment promotion before
   replacing installed code. No runtime control was modified in this slice.

Do not update the protected launchers or authorization gate via an alternate
path to evade a prior denial. Use the platform-supported authorization process
for any later production cutover.


## 2026-10-24: durable desktop request transport staged (not deployed)

`project_orchestrator.iron_god_desktop_requests` now implements an
**actual SQLite-backed request transport** for the two planned actions:
`GIVE_ME_CONTROL` and `TAKE_THE_WHEEL`. It accepts an already-existing
SQLite connection, adds one opt-in table to that same store, generates a
distinct command ID, durably records intent, and supports a fenced observation
that binds the request to one supervisor generation and exact release SHA.

This is more than a text validator: a second Windows Python process can
read the real persisted record. A newer owner click supersedes an older
unexecuted request; a delayed observer cannot revive a previous
`TAKE_THE_WHEEL` request after a later `GIVE_ME_CONTROL` request.
Replay, expired requests, conflicting generation, invalid SHA, and malformed
timestamps are rejected transactionally. The module's status fields are
`REQUESTED_NOT_AUTHORIZED` and `OBSERVED_NOT_AUTHORIZED`; **neither
means that the supervisor acted, workers stopped, or any start was authorized**.

The staged CLI is executable as:

```powershell
python -m project_orchestrator.iron_god_desktop_requests --database <existing-claims.sqlite> --prepare
python -m project_orchestrator.iron_god_desktop_requests --database <existing-claims.sqlite> --action GIVE_ME_CONTROL
python -m project_orchestrator.iron_god_desktop_requests --database <existing-claims.sqlite> --inspect <command-id>
```

**These are interface examples, not instructions to run them on the live
protected database.** `--prepare` requires an existing database containing
`acceptance_dispatch_claims`; it refuses to create a database.
`--action` and `--inspect` return exit code **5** to ensure a desktop
success notification cannot interpret a queued request as completed control.
The prototype never reads the retired control state, changes the controller
mode or candidate-execution flag, starts a worker, changes a scheduled task,
or stops Remote Desktop Commander.

What is **not** done: no owner-click authentication, no live shortcut wired to
the transport, no active supervisor reading requests, no actual pause/resume
acknowledgment, no release staging and exact-head full preflight, and no
human-controlled end-to-end cutover. Independent owner authority and live
process/lease verification still need to be integrated with the existing
`iron_god_desktop_control_proof` contracts before the status can be elevated
from request/observation to genuine completion.
