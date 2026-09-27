# Steward

Steward is the machine-health governor for Project Hands-Off Evolution.

The execution chain is:

`Foreman -> Steward -> LaptopActor -> repository-native verification -> GitHub`

Foreman decides which product work is eligible. Steward decides how much of that work the laptop can safely afford to execute now. LaptopActor performs the bounded work. Steward never changes product priority or declares a product blocked because the machine is temporarily unhealthy.

## Operating bands

- GREEN: normal execution; up to four independent product lanes may run.
- YELLOW: reduced concurrency; avoid unnecessary heavy-tool overlap.
- ORANGE: one LIGHT/STANDARD lane; HEAVY work is deferred.
- RED: no new engineering jobs; preserve state and recover machine capacity.
- CRITICAL: fail closed and protect workspaces/data.
Steward evaluates RAM pressure, absolute free RAM, CPU load, every fixed disk's free space and used percentage, AC/battery state, known heavy processes, and temperature when reliable telemetry exists. Missing temperature data remains an evidence gap; Steward never guesses it.

Two independent ORANGE-or-worse pressure dimensions escalate the aggregate band by one level. This catches cases such as high RAM plus nearly-full disks even when CPU is idle.

## Thermal policy

The target is sustained operation below roughly 80 C when temperature telemetry is available. Steward begins throttling before 90 C and treats 90 C+ as RED. Physical controls remain important: keep underside vents clear/elevated, keep ambient temperature reasonable, and periodically clean intake/exhaust vents.

## Disk policy

80% used begins YELLOW pressure. 90% begins ORANGE. Absolute free-space thresholds also apply, because a percentage alone can hide dangerous low-free-space conditions. Cleanup is deliberately narrow: only actor-owned `.log`, `.jsonl`, and stale `.tmp` files inside known automation directories may be removed automatically.
Steward does not automatically purge product workspaces, Git repositories, npm/pip caches, commercial/private evidence, or unrecognized user files. Package-cache cleanup requires repository-specific evidence and authorization because a cache may be part of reproducible or offline verification.

## Daily reboot boundary

The existing 02:00 local reboot remains the reset boundary. Steward does not require elevated Task Scheduler access to protect the machine: LaptopActor performs bounded actor-owned hygiene at most once per 20 hours, so log/temp control continues even when the reboot task cannot be edited.

When the current session is permitted to update the existing reboot task, Steward wraps it with pre-reboot maintenance before invoking the same forced reboot. When Windows denies that mutation, the installer preserves the existing 02:00 reboot unchanged and records the limitation instead of failing installation.

At logon, Steward validates LaptopActor lock ownership, removes only a proven-stale actor lock, performs bounded log/temp rotation, clears the maintenance marker, and allows the scheduled workers to resume. If Task Scheduler registration is denied, startup sanity falls back to a normal per-user Startup-folder command. The maintenance marker makes LaptopActor fail closed if startup ordering races.
## Fairness

When health reduces available capacity below three lanes, Steward rotates admission using the last admitted product. Machine pressure therefore cannot permanently starve Knowledge Engine, Rocksmith, or Everward.

A health deferral is not product failure. It is represented as `DEFERRED_MACHINE_HEALTH`, and Foreman keeps the candidate eligible for a later admission cycle.

## CLI

`project-orchestrator steward-assess snapshot.json`

`project-orchestrator foreman-plan evidence.json` accepts optional `machine_health` and `steward_state` fields. With machine health supplied, Foreman's `candidate_dispatch_queue` is passed through Steward and only the health-admitted subset becomes `dispatch_queue`.

The governing contract is `engineering/steward.json`.
