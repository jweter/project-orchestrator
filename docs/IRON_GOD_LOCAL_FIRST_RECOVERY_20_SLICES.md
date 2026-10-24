# Iron God — local-first recovery batch: 20 bounded slices

2026-10-24. This is an **isolated local engineering branch**; production
supervisor, owner-control state, Windows scheduled tasks, the installed desktop
shortcuts, and source checkout dirty work were not modified or restarted.

Base SHA: `69cfbf535dd44144f876b4e0d20f5e8e36b1c5a6`.
Branch: `fix/iron-god-full-preflight-20261024`.

The first six slices repair test-host leakage exposed by the previous
full-repository preflight. The next nine stop stale GitHub-dependent actor
promotion, and the last five repair launcher installation and owner-control
boundaries. All changes are subject to native exact-head preflight and review.

| ID | Focus | Verified evidence |
| --- | --- | --- |
| IG-R01 | Make pre-mutation SHA mismatch builder unit test independent of live owner-state | `test_build_once_rejects_grant_when_pre_mutation_head_moved` |
| IG-R02 | Isolate bounded-slice authority test without changing production veto | `test_build_once_requires_bounded_slice_before_claim_or_mutation` |
| IG-R03 | Isolate bounded prompt test, preserve exact grant behavior | `test_build_once_uses_bounded_prompt_after_exact_head_authority` |
| IG-R04 | Isolate captured runner diagnostics test | `test_build_once_retains_runner_output_when_success_makes_no_changes` |
| IG-R05 | Missing authority remains fail-closed before idle probe | `test_missing_owner_authority_fails_closed_before_idle_probe` |
| IG-R06 | Runtime lease identity test isolated from host human-mode | `test_runtime_fence_uses_granted_worker_identity` |
| IG-R07 | Unpinned laptop actor can never fall back to GitHub origin/main | `test_unpinned_actor_refuses_origin_main_fallback` |
| IG-R08 | SHA pin must be exact hexadecimal before Git work | `test_local_release_requires_exact_sha_before_any_git_mutation` |
| IG-R09 | Accepted repo must be absolute | `test_local_source_requires_absolute_path_before_git` |
| IG-R10 | Cannot reset an accepted source through overlapping actor paths | `test_actor_and_source_tree_cannot_overlap` |
| IG-R11 | Untracked files in accepted source block promotion | `test_local_release_with_untracked_source_files_refuses_promotion` |
| IG-R12 | Fetched HEAD must match accepted SHA before reset | `test_fetched_sha_must_equal_local_accepted_sha_before_reset` |
| IG-R13 | Post-reset actor HEAD must match accepted SHA | `test_post_reset_actor_head_must_match_local_release` |
| IG-R14 | Dirty post-reset actor is not verified | `test_local_promotion_post_reset_dirty_actor_is_not_verified` |
| IG-R15 | Real Git proof that actor follows newer accepted local commit, **not stale origin/main** | `test_local_promotion_real_git_moves_actor_to_current_sha_without_origin_main` |
| IG-R16 | PowerShell actor launcher requires config-pinned actor path | `test_launcher_verify_only_uses_pinned_actor_not_legacy_default`, `test_launcher_rejects_wrong_actorroot_override` |
| IG-R17 | PowerShell launcher refuses untracked source and absent actor checkout | `test_launcher_rejects_untracked_code_in_accepted_source`, `test_launcher_rejects_missing_pinned_actor_checkout` |
| IG-R18 | Old GitHub-first installer fails closed; cannot reenable stale main | `test_retired_github_actor_installer_cannot_reactivate_stale_main` |
| IG-R19 | Full-power script fails closed when owner marker absent | `test_full_power_missing_owner_marker_fails_before_any_scheduled_task` |
| IG-R20 | Persistent supervisor launcher refuses to start reboot guardian/supervisor before owner authorization | `test_supervisor_launcher_does_not_spawn_guardian_on_missing_owner_authority`, `test_launcher_owner_gate_is_fail_closed_for_unreadable_or_held_state` |

## Current limits and deployment requirements

- This branch contains fixes and tests, **not a live cutover**. It does not
  install an owner-control service, authenticate the two installed desktop
  shortcuts, or prove production command acknowledgment.
- Existing launcher controls still use the site's established owner state.
  Production marker state has not been read or rewritten as part of a cutover;
  the absence of independently verified authority always blocks execution.
- The Windows scheduled tasks remain stopped/disabled until authorized and
  verified; no task launch, reboot, remote disconnect, or live actor reset was
  conducted for these changes.
- The deprecated GitHub-first installer now refuses activation rather than
  creating a self-updating stale actor. The local accepted release must be
  staged through independently verified exact-head promotion.
- Even a fully green test suite does **not** constitute deployment. A future
  controlled cutover requires approval, protected ownership check, stage and
  rollback receipts, a clean exact-head accepted commit, and a real on-device
  test of both Give Me Control and Take the Wheel.
- The 20 rows are engineering micro-slices, not 20 accepted product milestones,
  and must not be counted as usable autonomous software-development output.
