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
* Only slices whose behavior is fully deliverable as new pure functions decompose. Slices that edit existing code,
  integrate subprocesses, GUIs or engines report `RESIDUAL_NOT_LOCAL` and stay with the stronger builder until
  Level 2 children (directed edits with approved failing tests) are supported by the decomposer.
* Coverage is checked structurally (every criterion has an owner subtask), not semantically.
