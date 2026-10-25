# Canon S2 — Deterministic builder test owner-state isolation

## Source observations

The prior native full suite reported 13 test failures, including several
builder tests returning `USER_ACTIVE` instead of exercising their intended
grant-head, bounded-slice and no-op behavior. A targeted native baseline
run at commit `ea7a0c6` reproduced **4 failures out of 4** because the
live owner-control predicate was reached before those later checks.

The four tests were correctly testing downstream behavior, but their
owner-control input was not isolated from the machine's live status.
The real S2 acceptance remains RED; this changes only test fixtures.

## Narrow repair

In **only** four test cases in `tests/test_autonomous_slice_builder.py`,
explicitly replace the `owner_control_status` **IO boundary** with
a deterministic `AUTHORIZED` value via pytest monkeypatch. The real
`manual_human_mode_enabled` function, its order before idle/repo/git
and all other production code remain UNMODIFIED. These tests already mock
Git commands, worktrees, model runners, and grant fixtures; no real
worker or controller is launched by them.

A new parameterized regression tests `OWNER_CONTROL_ACTIVE` **and**
`OWNER_CONTROL_UNVERIFIED` at that same status seam and asserts that
`build_once` returns `USER_ACTIVE` before any idle, repository, Git,
worktree or worker probe can be executed. This keeps fail-closed behavior
independently covered. The two branches execute with no live owner-control
file or persistent controller writes.

## Baseline and verification

- On the previous source SHA, four downstream builder tests: **4 FAIL**,
  each blocked by `USER_ACTIVE`.
- On this isolated source candidate, those same four tests: **4 PASS**.
- New fail-closed owner veto tests: **2 PASS**.
- `pytest` focused cases: **6 PASS**.
- Python Ruff, compileall and Git diff checks: PASS.

No protected control files or launchers were modified. No execution
permission was granted; unit-test stubs are not real owner authorization.
S2 remains unaccepted until full native exact-head preflight is GREEN,
and the other earlier owner/control and Windows integration failures
receive their own independent diagnosis. S3 remains unstarted.
