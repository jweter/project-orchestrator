# IRON CONTINUUM — fail-closed microtask restart consistency

Scope: #747 / #710. This is a **canonical-lane safety repair** to the existing
local-first task decomposer, not a new scheduler, retry policy, owner grant,
worker lease, model calibration, SQLite schema, or acceptance authority.

## Reproduced failure

The existing authorized Product lane uses `backlog-state.json` plus the
approved microtask-item store to decide whether t1, t2 and t3 are merged or
eligible. The separate revision-bound `microtask-progress/` checkpoint retains
observed local/merge receipts across interruptions.

If the approved item store or canonical backlog-state record disappears after
one microtask was merged, the historical checkpoint still reports its receipt,
but `child_requests()` historically treated that task as NEW and could prepare
a duplicate local execution request. An old checkpoint must not *grant* status
or spawn work, but it also must not be ignored when selecting an already
recorded accepted task for replay.

## Implemented boundary

`microtask_progress.checkpoint_replay_conflicts()` performs a bounded
read-only comparison of receipts for the **same plan revision** against the
existing canonical approved item/state rows.

- If canonical state and items still reconcile all observed merged/accepted
  attempts, the *existing* decomposer behavior is unchanged.
- If an observed merged attempt loses its canonical item, disappears from
  backlog state, or contradicts the existing lane outcome, the entire affected
  parent plan is withheld from **new** child dispatch pending reconciliation.
- If an observed locally accepted attempt loses its canonical row, dispatch is
  similarly withheld. Subsequent canonical review failure may still follow
  existing revision/attempt caps: a local acceptance is not a merge.
- Malformed or unreadable existing checkpoint receipts fail closed, with
  never more than 1000 receipts inspected per already bounded checkpoint file.
- A *different* active approved plan in the same repository can still produce
  ready work. The veto never grants authority or consumes an attempt.
- A **changed plan revision** gets a different checkpoint filename, so old
  receipts cannot force a new task to appear completed.
- At the real Product dispatch boundary, `filter_replay_conflicted_items()`
  removes existing approved children belonging to a conflicting ACTIVE parent
  from candidate selection, including `-rN` revisions. The refill pass also
  excludes these items from its short-circuit eligibility check. Unrelated
  approved work remains dispatchable; when none exists, the lane reports
  `WAITING_FOR_MICROTASK_RECONCILIATION` instead of a false empty queue.
- `record_checkpoints()` now preserves **malformed** historical evidence
  rather than silently replacing it with an empty snapshot before
  `child_requests()` checks eligibility. A valid checkpoint may update its
  observational subtask status, but the historical receipt list remains and
  can still veto unsafe replay.

No checkpoint is used as a positive authorization, no attempt budget is reset,
no worker is restarted, and no background tasks or owner-control settings
are changed. On a veto, use existing canonical data-recovery and
worker/lease/owner reconciliation before resuming the affected parent. Do not
delete or forge a checkpoint to force a retry.

## Verification evidence

The deterministic `test_iron_continuum_restart_consistency.py` exercises
three dependent microtasks using real decomposer and checkpoint functions:
after t1 and t2 are recorded as merged, it discards in-memory state and reloads
the canonical files, confirms that t3 alone is eligible and that repeated
checkpoint recording is byte-for-byte idempotent. Additional tests prove that
missing state does **not** replay t1, an independent plan remains eligible,
local acceptance permits legitimate capped repair, corrupt/spoofed receipts
fail closed, and changed plan revisions cannot inherit stale authority.

These are **simulated local integration tests**, not real Ollama edits or
native product acceptance. They do not satisfy the issue #710 acceptance canary
by themselves. Real proof must use a controlled installed release under
Take the Wheel, measured memory/lease/policy, and zero paid model tokens.
