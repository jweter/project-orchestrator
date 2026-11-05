# Iron God local task decomposition

Purpose: let the engine advance an authorized Level 3-4 roadmap slice on Azathoth with the free local model, without
Claude, ChatGPT or any paid service, by splitting it into Level 1 subtasks the local builder is measured to build.

## Baseline (2026-11-05, measured, not assumed)

| Piece | State |
|---|---|
| Ollama | `qwen2.5-coder:7b` (builder/planner), `qwen2.5-coder:1.5b`, `nomic-embed-text`; CPU only |
| Local builder | Level 1 (one new pure function, >= 2 roadmap acceptance examples) and Level 2 (directed edit with an approved failing test). About 1 in 5 turns passes end to end, 2-5 minutes each |
| Routing | `iron_god_roadmap_refill.route`: a slice above the calibrated level goes to the capped capable builder |
| After a draft | strict admission, product worker, canonical preflight, publication, CI, merge, graph progress already exist and are reused unchanged |
| Missing | nothing turned a Level 3-4 slice into Level 1 work, so every such slice waited for the capped paid builder |

## What was added

`iron_god_task_decomposer.py` (+ `scripts/iron_god_decompose_author.py`):

1. A Level 3+ slice routed to the stronger builder is *planned locally first* (the capable dispatch is held until the
   local attempt has a recorded result). One job at a time, same runtime lease as the drafter, owner takeover never spends an
   attempt, at most two model attempts, a hung job is failed by age.
2. The local model proposes `{subtasks, coverage, residual}`. **Only deterministic validation accepts it**: new
   modules only, inside the slice's allowed paths and outside its forbidden ones, one function per module, literal
   examples that bind to the signature and do not contradict, <= 5 subtasks, dependencies only on earlier subtasks,
   every non-gate acceptance criterion mapped to at least one subtask, every subtask traceable to a criterion, and an
   empty `residual`. A non-empty residual (work a new pure function cannot deliver) means the slice is **not**
   decomposed: the plan is BLOCKED with the exact reason and the slice stays with the stronger builder. Nothing is
   simplified or silently dropped.
3. Each subtask becomes an ordinary Level 1 request `<parent>-tN` (scope narrowed to its module and the slice's test
   paths) and flows through the unchanged drafter, admission, worker, preflight, PR, CI and merge. The lane's own attempt
   limits, failed-source protection and re-authoring apply to each subtask.
4. The parent is held while a plan is ACTIVE (no duplicate work by a second builder). When every subtask is merged the plan
   is DONE and the existing progress step records the parent in the roadmap graph, citing the subtasks' merged PRs.
   If a subtask exhausts its attempts the plan is BLOCKED and the parent returns to the stronger builder.

## Known limits (stated, not hidden)

* The model's examples are not an independent oracle (`oracle: SELF_AUTHORED` is recorded). The unchanged gates remain
  the verification: the worker's re-derivation, canonical preflight, repository CI and review. A decomposition never
  lowers them.
* Only slices fully deliverable as new pure functions or directed edits of existing units decompose. Slices that integrate
  subprocesses, GUIs or engines, or whose proof tests pass today, report `RESIDUAL_NOT_LOCAL` and stay with the stronger builder.
* Coverage is checked structurally (every criterion has an owner subtask), not semantically.

## Level 2 edit subtasks

The existing Level 2 pilot (`IRON_GOD_LEVEL2_CALIBRATION_20261028.md`) measured the directed-edit contract at 9/12 first-pass
and a 50 s median, but its planner (names the units, writes the acceptance test, proves it fails on main) was Claude. A plan
step of `kind: "edit"` now carries that planner work locally:

* the outline names one existing module and at most three existing units; the repository at the exact base is the
  authority (`iron_god_directed_edit.units`), not the model: a missing module or unit rejects the plan with the exact name;
* the model sees the target units' current source and the rest of the module as signatures only, and writes a pytest file that
  must fail now and pass after the change; the test path is assembled by code from the slice's allowed test paths and must
  be a new file;
* the step becomes an ordinary Level 2 request (`directed_edit`: module, targets, test path, test source) and the
  UNCHANGED draft author runs the test at the exact base and accepts it only if it FAILS there for the right reason (an
  assertion, not an import error); the unchanged worker then re-checks that, splices only the named units, and requires the
  same test to pass plus mypy, ruff and canonical preflight. A directed slice is attempted once;
* two edits of one module must be ordered by `depends_on`.

A proof or characterization slice (tests that pass today) cannot be a Level 2 edit, because the gate requires the test to fail
at the base. Such slices still report a residual and stay with the stronger builder until a test-only step exists.

## How the engine works now versus the documented goal (2026-11-05)

| Goal (HANDS_OFF_EVOLUTION, owner-approved roadmap) | Today |
|---|---|
| Owner governs, observes, realigns; never a routine participant | The owner still clicks Give Me Control / Take the Wheel for every install and takeover (intended reserved boundary), but also stands between sessions of a human-supervised Claude |
| OBSERVE -> SELECT -> RESERVE -> BUILD -> VERIFY -> PR/CI -> PROMOTE -> RECORD -> LEARN -> NEXT | Implemented end to end for Level 1 and approved Level 2 slices: refill, drafter, admission, worker, preflight, PR, CI, merge, graph progress |
| Roadmap prose becomes executable slices | A *capable builder* (paid Claude, 4/day) writes new graph slices ("extend-capability-graph"), declares levels, acceptance examples and directed edits |
| Complex slices are built | Level 3-4 slices wait for the capable builder; the local 7B is measured only for Level 1-2 |
| Merged feedback and review findings are repaired | Review-finding repair and engine-defect diagnosis are done by Claude sessions |
| Phases 1-7 of Hands-Off Evolution | All unchecked; the control loop runs, but unattended multi-product advancement is not proven |

Claude is therefore currently required at five points: graph planning, acceptance-example/test authoring for Level 1-2,
Level 3-4 implementation, review-finding repair, and engine-defect diagnosis. This document's decomposer replaces the second
and (partly) third; planning, review repair and diagnosis remain the next replacements, in that order of feasibility.
