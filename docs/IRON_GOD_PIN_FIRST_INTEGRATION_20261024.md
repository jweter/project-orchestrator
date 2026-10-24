# Iron God — October 24 pin-first integration

## Authoritative ancestry

The previously prepared 20-slice worktree was based on `96508cb`, but
Azathoth's latest validated pinned release is
`2378d314d24f9eff10773596f6898075e018bdee`.
The two histories share `96508cb`; the pinned branch had **15 commits**
missing from the first recovery branch. Therefore the older branch must
**never** be promoted directly as a new accepted release.

New integration branch: `fix/iron-god-pin-first-20261024`, created at
`2378d314`. Existing local dirty checkouts, the pinned release itself, and
all installed task and owner-control state remain unchanged.

## Integrated changes

- Existing verified release ancestry preserved, including the earlier
  actor pre-reset dirty-work veto, recovery safety checks and independently
  isolated builder regression tests.
- The six previous full-preflight builder assertions are already isolated by
  pinned commit `f9c9cb1` using explicit test-only owner-state fixtures.
  Rather than overwrite this newer implementation with an older monkeypatch
  variant, the redundant test-only commit `43b9635` was **not** applied.
- Added local-first actor pin requirements: SHA and absolute source validation;
  source/actor overlap veto; reject dirty/untracked accepted source; verify
  fetched exact SHA; reject dirty actor **before** reset; verify reset HEAD
  and post-reset cleanliness; never fall back to old GitHub `origin/main`.
- Corrected pinned PowerShell actor launcher so its configured actor checkout
  is authoritative; explicit mismatched overrides fail, accepted source
  untracked work blocks promotion, and the previously installed runtime
  exact-head/clean checks are **preserved**.
- Retired the GitHub-first standalone actor installer so it cannot silently
  reinstall a stale self-updating actor.
- Full-power script now treats a missing owner marker as unverified, not as
  permission to start tasks.
- Persistent supervisor launcher rechecks owner control before creating
  state or spawning its network/reboot guardian.
- Re-integrated prior local work from the earlier desktop-control transport
  branches, preserving staged request history, non-authorizing shadow
  observation, request-correlated pause/resume evidence, safety/source audits,
  and focused regression tests.
- Re-integrated fail-closed non-running supervisor heartbeat verdict.

## Test and cutover boundaries

Focused Windows regressions covering actor bootstrap, real Git local-first
promotion, PowerShell verify-only launcher, persistent supervisor, human
control, builder grants, desktop request transport, shadow intake and
supervisor health: **238 passed** at the integration point. Repository-native
full exact-head preflight is a separate acceptance requirement.

Read-only Windows inspection found both scheduled task actions pointing to
release `d9d9328d` while the authoritative release/actor pins point to
`2378d314`. The validated pinned checkout and its release receipt agree.
This **task-action/pin divergence** is a separate production blocker and
was not modified in this development batch.

Neither the desktop icons nor protected controller mode were rewired.
`candidate_execution_authorized=false` and `shadow_state_only` were
preserved. No autonomous supervisor/actor was started. Remote Desktop
Commander was not stopped. No installed production release was overwritten.

**To progress beyond staged code:** native full preflight -> independently
verified acceptance/staging -> permitted owner-control cutover and protected
task-action update -> exact-head, process-generation and command-acknowledged
real-button tests. Passing Python tests alone is not production acceptance.

## Scope caveat

The 20 planned changes were implemented in a separate development branch.
Several are duplicated by newer pinned implementation and were not copied
over because replacing newer safety code would be a regression. This
pin-first branch integrates the nonredundant fixes plus prior unreleased
desktop transport; it must be evaluated independently before deployment.
