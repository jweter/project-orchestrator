# Windows cold-boot recovery

Hands-Off must recover after a real Windows restart without Jeremy manually
starting the engineering supervisor.

The Windows entrypoint is `scripts/windows_hands_off_autostart.py`. It installs
a Task Scheduler **ONLOGON** task named `ProjectHandsOffSupervisor`. The task
does not invoke the supervisor directly; it invokes the fail-closed
`launch_persistent_supervisor.py` handshake.

This deliberately separates two claims:

1. **Windows attempted startup** — Task Scheduler evidence.
2. **Hands-Off actually owns a live burst** — `.hands-off-state/persistent-supervisor-launch.json`
   must report `PASS` or a canonically bound `EXISTING_ACTIVE`.

A successful task launch without an authoritative supervisor heartbeat is a
failure, never a healthy state.

## Commission on Azathoth

Run after Remote Desktop Commander is restored:

```bat
python scripts\windows_hands_off_autostart.py install ^
  --repo-root C:\Users\jerem\Documents\GitHub\project-orchestrator ^
  --workspace-root C:\Users\jerem\Documents\GitHub ^
  --state-root C:\Users\jerem\Documents\GitHub\project-orchestrator\.hands-off-state
```

Then inspect:

```bat
python scripts\windows_hands_off_autostart.py inspect
```

## Cold-boot acceptance

A real reboot is PASS only when all are true:

- Task Scheduler reports the task present and its last launch successful.
- A new physical `process_instance_id` exists, or canonical EXISTING_ACTIVE
  evidence proves the already-running owner.
- The launch receipt is PASS/EXISTING_ACTIVE.
- `supervisor.json` agrees on process and burst identity.
- continuation lineage either resumes with valid takeover proof or begins a
  valid new burst.
- USER_ACTIVE remains non-bypassable.
- after genuine idle, the bounded reacquisition loop executes without a manual
  Continue.
- an eligible product capability is selected and advances through exact-head
  verification/PR/CI, or a durable authoritative blocker is recorded.

Remote Desktop Commander is an administration/recovery channel. Its absence
must not be what stops the local Hands-Off supervisor after the scheduled task
has been commissioned.
