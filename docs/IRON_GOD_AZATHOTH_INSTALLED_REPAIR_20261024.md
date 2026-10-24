# Iron God — Installed Azathoth desktop-control repair (2026-10-24)

This report records completed **on-laptop** maintenance separate from the
unpromoted local-first source work. It should prevent future agents from
re-diagnosing or accidentally reversing a fixed release mismatch.

## Previous confirmed failure

- Both Windows task actions targeted immutable release `d9d9328d...`
  while both accepted release pins targeted `2378d314d24f9eff10773596f6898075e018bdee`.
- The original live `Check-Development-ResumeReady.ps1` incorrectly
  returned `RESUME_READY` because it checked launcher filenames rather than
  matching their **exact absolute paths** and working directories.
- Clicking `Give Me Control` from `DesktopControlSilent.vbs` launched
  `Enter-HumanMode.ps1` without `-KeepRemote`; its default path could stop
  Remote Desktop Commander, and its remote-preserving status check erroneously
  treated a preserved remote process as a partial owner handoff.

## Actual changes confirmed

1. Created `Repair-DisabledPinnedTasks.ps1` with read-only default,
   independent owner-held checks, pinned release/receipt/tree/actor validation,
   process-stop confirmation, pre-mutation task XML snapshots and rollback on
   partial failure. Both task actions were repointed from `d9d9328d` to
   `2378d314`, leaving both tasks **Disabled**. The script's post-repair audit
   reported `drift_count=0`.
2. Installed the improved read-only
   `Check-Development-ResumeReady.ps1` with exact task/action/path checks,
   correct `-StateRoot` and `-PinPath` verification, and pinned actor HEAD
   and worktree cleanliness validation. It now reports `RESUME_READY`
   **only** after both task actions have been aligned.
3. Patched the installed silent VBS and owner-control PS1 to pass
   `-KeepRemote`, preserve remote task/bridge processes and avoid declaring
   remote-preserving handoff a failure. Live non-destructive owner self-test
   returned `OWNER_CONTROL_GRANTED`; 14/14 desktop wiring checks passed.
4. Take-the-Wheel's **non-destructive** `-SelfTest` returned exit code 0,
   and an exact before/after comparison confirmed that the owner marker was
   not changed.
5. Archived four unrelated/confusing `Iron God - ...` mode shortcuts,
   leaving `1 - GIVE ME CONTROL.lnk` and `2 - TAKE THE WHEEL.lnk` visible.

## Rollback locations on Azathoth

- Original watchdog/actor task XML and readiness script:
  `%LOCALAPPDATA%\ExponentialEngine\flow-state\task-action-pin-repair-c25da64709a34357b089c3f41bc2f19a\`
- Original owner-mode/VBS scripts:
  `%LOCALAPPDATA%\ExponentialEngine\flow-state\desktop-control-keepremote-hotfix-308372fed5584abbb80533fc11eab576\`
- Extra mode shortcuts:
  `%LOCALAPPDATA%\ExponentialEngine\retired-desktop-shortcuts\20261024-143818\`

These are rollback artifacts, not instructions to restore the old version by
default. The previous scripts could disconnect the repair bridge or invoke a
stale release.

## What remains unverified

- The owner-controlled live click of `Take the Wheel` and the subsequent
  confirmed supervisor/actor generation, runtime acknowledgment and progress.
- A subsequent live click of `Give Me Control` stopping real workers without
  severing Desktop Commander.
- Full native preflight and staged promotion of the newer pin-first development
  branch. Task alignment to `2378d314` is not proof that the newer development
  branch was installed. No worker was launched here, and no owner authorization
  was released.

The test-only/shadow desktop SQLite transport is **not** an actual
command-execution acknowledgment. Do not report those transport tests as
operational worker control.
