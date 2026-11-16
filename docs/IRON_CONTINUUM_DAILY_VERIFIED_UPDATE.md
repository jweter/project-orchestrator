# IRON CONTINUUM — Daily verified GitHub update check (#747)

## Owner-approved behavior

A single local Windows Task Scheduler entry named `IronGod-Daily-Verified-Update`
checks `jweter/project-orchestrator` **merged `main`** once per day, by default
at **02:20 Azathoth local time**. This uses the laptop's existing
`IronGod-Update.ps1` verification, staging, promotion, backup, and release
readiness gates; it is not a new, competing updater or supervisor.

The update checker:

1. Reads the pinned release SHA and the exact remote GitHub `refs/heads/main`.
2. If identical, writes `CURRENT` and exits without further work.
3. If different and Iron God has live owner/control/tasks/processes, records
   `UPDATE_PENDING_OWNER_CONTROL` or `UPDATE_PENDING_QUIESCENCE` and exits.
   It **never** claims Give Me Control, stops a worker, disables tasks,
   disconnects a model, resets queues, or restarts Azathoth.
4. If the user **has already** clicked Give Me Control and all owner-held,
   disabled-task and process checks are satisfied, the checker can invoke the
   **existing verified desktop updater**. That updater independently
   rechecks exact merged commit, reviewed PR CI, full native preflight,
   quiescence, release ancestry, staging, backup and promotion. The engine
   remains disabled at the end.
5. After a successful update, records
   `UPDATED_DISABLED_OWNER_RESUME_REQUIRED`. The owner must click their
   existing **Take the Wheel** shortcut before development resumes.

There is no auto owner takeover, paid model use, release-bypass mode,
GitHub PR merge, or unattended process restart. **While Iron God is normally
running, this task will detect and queue the update rather than install it.**
The user can apply the queued update during the next Give Me Control window.
A fully hands-free stop/restart workflow would require a separately
approved maintenance-authority feature with worker draining, clean
checkpointing and controlled resume. This task does not invent that right.

## Deploy on Azathoth

Run once from the source at a vetted exact Git SHA:

```powershell
powershell -NoProfile -NonInteractive -File .\scripts\desktop-control\IronGod-Daily-Verified-Update.ps1 -SelfTest
powershell -NoProfile -NonInteractive -File .\scripts\desktop-control\IronGod-Daily-Verified-Update.ps1 -CheckOnly
powershell -NoProfile -NonInteractive -File .\scripts\desktop-control\Install-IronGod-Daily-UpdateTask.ps1 -Plan
powershell -NoProfile -NonInteractive -File .\scripts\desktop-control\Install-IronGod-Daily-UpdateTask.ps1
```

The installer copies the checker into
`%LOCALAPPDATA%\ExponentialEngine\tools\VerifiedReleaseUpdater\` and
registers one **interactive, limited-user** daily task. It refuses to silently
replace another task or a modified checker. Registration itself **does not
run the update**. The task uses `-MultipleInstances IgnoreNew`,
`-StartWhenAvailable`, and a bounded two-hour run duration.

Report file:
`%LOCALAPPDATA%\ExponentialEngine\flow-state\iron-god-self-update\daily-status.json`.
This is an atomic, advisory status record, **not** an authority grant,
engineering ledger or installation acceptance receipt. The installed pinned
release files remain the authoritative evidence.

## Verification and limitations

- PowerShell syntax parser: both scripts parse.
- Checker `-SelfTest`: five state/gate scenarios pass.
- Checker `-CheckOnly`: compares live pinned SHA to actual GitHub main and
  classifies a running engine as `UPDATE_PENDING_OWNER_CONTROL`.
- Installer `-Plan`: prints the exact task and target path without mutation.
- Portable Python contract tests cover source, gates, isolation, task
  idempotence, absence of forbidden control commands and status schema.

The Windows scheduled-task registration must be verified separately in
the user's real environment. No static test proves an actual production
installation or that a given latest GitHub commit passed FULL preflight.
