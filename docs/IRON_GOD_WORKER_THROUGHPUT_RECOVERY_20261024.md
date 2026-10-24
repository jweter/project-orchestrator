# Iron God — worker throughput repair (2026-10-24)

## Operational symptoms reproduced

Read-only inspection on Azathoth found a live repeating
`PLANNED_SLICE_IDENTITY_MISMATCH` exception for `everward-worker-1`.
The issue-work frontier incorrectly ranked issue #159 as an executable
`TRIAGE` slice without a `repository`, `exact_head`, approved edit scope,
or acceptance tests. The grant issuer correctly rejects that incomplete
authority; blindly relaxing the grant issuer would weaken safety.

The real Everward capability graph does contain a ready, properly scoped
slice: `phase2-low-spec-play-mode-next` at HEAD
`3e6ade3a3968fdafadc0848e651b6f6d731a0644`.

Machine RAM had repeatedly reached CRITICAL cooldown earlier in the run.
In a later read-only sample the Windows memory API reported 4,936 MiB free
out of 16,095 MiB. The existing actor Steward governor protects bridge work,
but the supervisor could still schedule from a six-thread pool without a
fresh physical-memory budget.

## Isolated fixes

1. `planning_dispatch.ready_task_batch` now defers untriaged/unscoped
   issue-frontier entries, and never invents executable authority.
   Explicitly accepted issue slices carry repository, exact HEAD,
   acceptance conditions, allowed paths and forbidden paths into the
   existing grant path. A declared stale issue HEAD remains blocked.
   Deferrals are recorded in `issue-execution-deferrals/<product>.json`.
2. `worker_memory_admission.memory_admission` samples Windows physical
   memory on each supervisor pass and caps *new* AI builder submissions:
   less than 3 GiB free => 0; 3–6 GiB => at most one active worker;
   at least 6 GiB => at most two active workers. Existing workers and
   legitimate owner control are never interrupted. If the memory sample is
   invalid or unavailable, new dispatches fail closed.
3. The production supervisor entrypoint passes the admission budget to
   `parallel_runtime.dispatch_parallel_builders` and persists
   `worker-memory-admission.json` to permit runtime verification.
   Every failure-triggered same-pass refill is subject to a new sample.

## Verification

- **164 targeted and existing scheduler/supervisor tests passed**.
- Ruff checks passed and Git diff was verified clean of whitespace errors.
- A read-only rehearsal using a *temporary copy of Azathoth's actual*
  Everward capability graph, issue frontier and activation selected exactly
  `phase2-low-spec-play-mode-next`, with repository/exact-head identity
  confirmed, while deferring all 12 unscoped issue records including #159.
- The live Windows admission sample at 4,936 MiB free returned
  `LOW_MEMORY_SINGLE_LANE` and zero additional worker budget with one
  existing active lane.

## Deployment gate — not live yet

The changes are committed in isolated worktree
`fix/iron-god-worker-throughput-20261024`, descending from the current
local development lineage. The installed supervisor and actor still use the
previous accepted release; these code fixes are **not yet deployed to them**.

Do not hot-swap files inside an active supervisor/release checkout.
A future exact-HEAD native full preflight and authorized pin-first staged
cutover must verify both release and actor SHA, preserve dirty work,
and prove actual development throughput without evicting live workers.
The ongoing hourly workflow watch is unaffected.

## Further repair candidates

- Reconcile the existing 49 quarantined grants by *distinct* reason and
  owner/liveness proof before retry; do not bulk-release.
- Ensure the source of `PLANNED_SLICE_IDENTITY_MISMATCH` disappears after
  the new local release is installed, without weakening grant checks.
- Improve productive-liveness reporting so a GREEN supervisor process with
  zero merged/verified outcomes is classified as **productive stall**, not
  completed delivery.
- Resume VoiceStudio/Knowledge Engine only after they acquire fully scoped,
  dependency-ready capability graphs and fresh exact-head authority.
