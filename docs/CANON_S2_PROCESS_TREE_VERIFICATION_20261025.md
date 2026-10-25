# Canon S2 — Windows supervised-process fencing

Date: 2026-10-25
Project: jweter/project-orchestrator / Iron God
Branch: fix/iron-canon-s2-process-tree-20261025
Base: 0813bda1466325c9c31a1c4bb32fe3272504465f

## Confirmed defect and baseline evidence

The existing `test_authority_revocation_kills_descendant_tree` FAILED
when run alone on the untouched S1 worktree at `45414a2` and again on
the S2 head `0813bda`. A Python grandchild wrote its sentinel after
supervisor authority was revoked. This is an independently reproduced
runtime correctness defect, not introduced by Canon S2.

Windows `taskkill /T /F` on the immediate Popen root is not sufficient
to guarantee that a grandchild cannot escape revocation, especially when
process startup, descendants and root exit overlap.

## Isolated source fix

- `windows_process_job.py` implements a Windows Job Object configured with
  `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`; its close stops only descendants
  of the process that this supervisor itself launched.
- `execution_runtime.py` starts supervised Windows processes with
  `CREATE_SUSPENDED`, attaches them to the new job and resumes the primary
  thread using Windows Toolhelp/OpenThread/ResumeThread APIs.
- A failure to attach/resume never allows that still-suspended process to
  execute; it is terminated by its *owned process handle* and the runtime
  raises `EXECUTION_PROCESS_TREE_FENCE_UNAVAILABLE`.
- Owner authorization checks continue throughout execution; revocation
  closes the Job Object and calls the existing tree terminator.
- Normal process exit closes the Job Object too, so child processes cannot
  outlive the supervisor's execution request.

No unrelated process is enumerated or terminated by this patch. No
owner-control, scheduler, launcher, controller mode, or installed runtime
was modified. This is an isolated development branch only.

## Native verification

- Original `tests/test_process_tree_fencing.py`: **2 PASS** on candidate.
- New job fencing tests (failure-to-attach, normal parent exit, five
  independent revocations): **7 PASS**.
- Combined Windows execution/runtime adjacent suite: **38 PASS**.
- Python Ruff lint: PASS.

## Remaining S2 acceptance blockers

The previously recorded native full preflight for `0813bda` was RED
(13 FAIL, 2827 PASS). Eleven other failure modes were not proven to be
related to the newly repaired process-tree boundary.

One `autonomous_slice_builder` failure also reproduces on unchanged
S1 code: `test_build_once_rejects_grant_when_pre_mutation_head_moved`
returns `USER_ACTIVE` rather than the expected head mismatch during
owner control. This proves it is not an S2 regression, but it does NOT
grant permission to disable the owner gate or rewrite tests to bypass
live safeguards.

S2 remains unaccepted until all full native preflight gates pass on a
verified exact candidate head, owner and repository authority are resolved,
and relevant independent reviews are satisfied. Do not proceed to S3
on the basis of runtime-focused tests alone.


## Additional exact-head and baseline results

- At source head `31380064df9a095bf657e5ebeacd91562d52ee26`,
  the combined Canon S2 / memory / worker transport / Windows runtime
  regression suite returned **177 PASS, exit 0**; the Git head did not
  change during execution, and the tracked worktree was clean.
- The isolated `test_failed_git_removal_preserves_worktree_and_branch`
  that had failed in the earlier 2,840-test run passed when independently
  rerun. It remains an **unclassified full-suite interaction/flakiness**
  until reproduced and diagnosed; do not simply delete its failure.
- On the untouched S1 baseline `45414a2`, three individual builder
  tests reproduced their RED results. The head-mismatch test returned
  `USER_ACTIVE` rather than `EXECUTION_GRANT_HEAD_MISMATCH`; the
  bounded-prompt test returned `USER_ACTIVE` rather than `NO_OP`;
  the bounded-slice test never reached its expected Git checkout.
  All are gated by current owner control. This establishes they are
  pre-existing owner-state-dependent failures, **not** permission to
  turn owner mode off or relax any production check.
- A separate orchestrator test,
  `test_stops_cleanly_with_no_dependency_ready_slice_and_no_planner`,
  passed in that same untouched S1 baseline sample.

**Critical acceptance distinction:** the S2 state-machine change plus this
Windows process-tree safety repair is a source-verified development candidate.
The official full native preflight is still RED on the earlier S2 head,
and no full-green native preflight has been established for the current
candidate. Do not merge, deploy or start workers on this evidence alone.
