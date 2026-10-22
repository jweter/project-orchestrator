# Iron God desktop control cutover — acceptance gates

The desktop controls must govern **the Iron God engine** and its shared
portfolio dispatcher, not just Everward or any one product worker.

This is a verification contract, **not authority to install or activate** a
control mechanism. The owner controls, platform permissions, protected
controller authorization, leases, approvals, gaming veto, and independently
owned workers remain authoritative. No existing launcher or scheduled task
should be modified merely to satisfy this document.

## Installed-state diagnostic

Run the nonexecuting module on Azathoth:

```powershell
python -m project_orchestrator.iron_god_desktop_readiness --base "$env:LOCALAPPDATA\ExponentialEngine"
```

Invoke from a working environment containing the project_orchestrator package.
Exit code 2 means this **read-only** auditor did not authorize any control
transition; the emitted JSON lists observed deficiencies. In particular, the
audit detects obsolete references by reading the **scripts' source text**, not
the retired state file. It does not click an icon, stop a worker, grant owner
permission, restart a supervisor, or attest to actual process identity.

## Required commissioning evidence

1. **Two owner actions, one engine:** Give Me Control pauses Iron God's
   dispatch and protects active work; Take the Wheel requests resumption of
   the same controller, subject to independently verified authority. Neither
   icon may launch a second supervisor or product-specific substitute.
2. **No retired dependency:** the VBS wrapper, pause/resume scripts, readiness
   gate, watchdog, ModeController, and all active consumers no longer consult
   or mutate the retired owner-control mechanism. Absence of that retired
   state is not a veto and its contents confer no permission.
3. **Authoritative acknowledgment:** a fresh, process-bound controller
   acknowledgment matches a specific owner request. A requested mode, file
   timestamp, successful icon exit code, or heartbeat alone is not proof.
4. **Protected authority remains independent:** owner resume cannot enable
   `candidate_execution_authorized`, switch shadow mode, skip leases, or
   override a platform denial. Explicit gaming veto and safety gates remain.
5. **Release identity:** the watchdog, laptop actor, supervisor, accepted
   release receipt, and running process identities agree on an exact accepted
   commit. No historical or stale release counts.
6. **Portfolio scope:** controller dispatch serves the registered authorized
   Knowledge Engine Core/Web/AI, Rocksmith, Everward, VoiceStudio, and Iron God
   development workstreams without starving products or double-dispatch.
7. **Failure semantics:** rejected or unverifiable pause/resume returns an
   explicit failure; no UI says success unless engine acknowledgment and
   safe observed state confirm it. A blocked attempt does not rewrite an
   obsolete marker to appear authorized.
8. **Independent verification:** tests exercise pause/resume races,
   concurrent/repeated clicks, process restarts, owner takeover during
   startup, gaming, stale pin, offline remote transport, existing Claude
   ownership, active leases, and an injected platform denial. Live cutover
   requires separately authorized approval and observed runtime evidence.

## Current observed failures (2026-10-22)

The installed supervisor is pinned to `d9d9328deaf2977e0570b4dba1f12bd8c7fb1317`.
The desktop mode request reports `acknowledged_by_engine=false` and
`effective_remote_authorization=NONE`. The protected Iron God controller is
`shadow_state_only`, with candidate execution authorization false.
The scripts still contain retired-control references. The live diagnostic
therefore reports `OWNER_CONTROL_UNVERIFIED` and **does not** activate
anything.

Until an independently approved engine-side transition is implemented and
verified, **the icons are not certified working**, even if their .lnk targets
exist or the old supervisor remains running.


## Pinned release capability drift after GitHub merges

The read-only diagnostic additionally inspects **only the source files inside the
installed release path reported by `supervisor-pinned-release.json`** for two
specific capabilities: `worker_failure_diagnostics` (PR #567) and
`desktop_control_readiness` (PR #568). It reports presence individually under
`pinned_release_capabilities`; missing source reports
`PINNED_RELEASE_CAPABILITY_MISSING:<capability>`.

This distinction matters: **a merged GitHub PR is not a deployed Azathoth
release**. An existing supervisor heartbeat, matching local pin, or successful
quality gate is not proof that the installed release contains a merged repair.
The audit does not fetch GitHub, alter any protected refs, or promote a
release. Source presence also is not proof that a running process loaded that
code. Required independent release receipt, process identity, controller
acknowledgment, and authorization checks remain separate.
