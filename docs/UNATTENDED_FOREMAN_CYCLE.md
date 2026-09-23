# Unattended Foreman Cycle

The unattended cycle is the integration boundary between normal Foreman scheduling and persistent Jev shadow evidence.

`project-orchestrator unattended-cycle evidence.json`

Each invocation:

1. builds the normal deterministic Foreman plan;
2. records meaningful Jev shadow comparisons to the persistent ledger;
3. reconciles prior open Jev records against supplied terminal execution evidence;
4. exposes the unchanged Foreman dispatch queue.

The evidence write occurs before an optional dispatch callback. Jev therefore observes the decision but never becomes the source of dispatch authority.

The CLI intentionally performs planning/evidence only. A long-lived LaptopActor runner may supply its existing dispatch callback when invoking `run_unattended_cycle()`; this module does not create a second actor, scheduler, or ownership database.

Repeated execution with the same stable `run_id` is idempotent for Jev evidence.
