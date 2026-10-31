# Desktop control cutover — 2026-10-21

The desktop Take the Wheel path was blocked by an incomplete local-release
cutover: unpinned actor/watchdog tasks, a legacy Startup launcher, and missing
release pin records. The old actor checkout also contained unique untracked
work, so it was preserved and a separate actor runtime clone was provisioned.

The deployed engine release is `d9d9328deaf2977e0570b4dba1f12bd8c7fb1317`.
Its native full `engineering/preflight.py` passed at that exact head before
staging. Both scheduled tasks now select that same local release. The obsolete
Startup command is preserved outside Windows Startup as a rollback artifact.

A live Task Scheduler test revealed administrator-only ACLs inherited from
the staging temporary directory. The task owner's access and Git ownership
were corrected only on the SHA-specific release and runtime directories.
The installer now provisions this access explicitly. Task actions use the
absolute Windows PowerShell executable and hidden windows.

Observed verification:
- 50 release-controller/bootstrap tests passed.
- 14 desktop wiring checks passed; readiness reported `RESUME_READY`.
- Live owner-stop maintenance test: zero engine workers and running engine tasks.
  Remote transport was deliberately retained for the maintenance test.
- Live scheduled resume: exit 0, one pinned supervisor and actor, matching fresh
  `RUNNING_CURRENT` heartbeat, and subsequent worker dispatch from that release.
- 7 Windows launcher/receipt tests passed after the final receipt-write repair.

Give Me Control recognizes the pinned launcher wrappers. Receipt writes now
replace atomically, retry contention, and report failure if persistence fails.
Take the Wheel never treats a task-start request as proof of a live engine.

Development remains local-first. This branch is a backup of the repair; it
has not been merged or promoted through GitHub CI. Machine receipts, task XML,
runtime ledgers, private payloads, and logs remain local and are not committed.
