# Project Iron God — desktop-control twenty-slice development batch

**2026-10-24. Staged local engineering, not deployed/authorized production cutover.**
Source: isolated Git worktree based on request-transport commit
`91560ff69e22c45035235d09c9c8819f9012bb10`.
The working `project-orchestrator` checkout and all unrelated dirty branches were preserved.

These are **20 bounded protocol/verification slices**, not 20 accepted user-product
milestones from the entire Iron God roadmap. They solve the testable transport
and shadow-consumer layer. Runtime installation and owner-controlled end-to-end
acceptance **remain incomplete**.

| Slice | New bounded capability or safety repair | Evidence |
|---|---|---|
| DC-01 | Read newest request from existing SQLite | `test_s01_read_latest_not_misrepresent_any_history` |
| DC-02 | Never select a superseded request for intake | `test_s02_newest_click_exclusively_eligible_for_shadow_consumption` |
| DC-03 | Distinguish pending from authorized execution | `test_s03_fresh_pending_request_never_authorizes_execution` |
| DC-04 | Refuse expired and future requests | `test_s04_expired_and_future_requests_fail_closed` |
| DC-05 | Preserve observed-not-authorized terminal semantics | `test_s05_observed_request_does_not_turn_into_applied_ack` |
| DC-06 | Bound request-history retrieval | `test_s06_recent_request_listing_bounded_and_ordered` |
| DC-07 | Separate action-history diagnostics from next-click authority | `test_s07_diagnostic_action_filter_does_not_override_newest_owner_click` |
| DC-08 | Expose pending/observed queue counts | `test_s08_count_pending_observed_and_total_without_authority` |
| DC-09 | Fail closed on absent or malformed transport schema | `test_s09_missing_and_malformed_schema_cannot_show_ready` |
| DC-10 | Warn on backlog without scheduling work | `test_s10_queue_diagnostics_report_backpressure_without_control` |
| DC-11 | Reject unverified owner-control evidence | `test_s11_owner_authority_unverified_refuses_to_observe` |
| DC-12 | Refuse shadow reader in execution-authorized candidate mode | `test_s12_active_candidate_mode_can_never_use_shadow_observer` |
| DC-13 | Reject mismatched supervisor/actor release pins | `test_s13_release_pin_mismatch_never_observes_request` |
| DC-14 | Reject stale supervisor heartbeat | `test_s14_stale_heartbeat_is_not_an_acknowledgement` |
| DC-15 | Record only durable, non-authorizing observation | `test_s15_shadow_observation_persists_identity_but_never_completes` |
| DC-16 | Require an independent OS process-incarnation probe | `test_s16_missing_os_process_probe_fails_closed` |
| DC-17 | Reject reused PID / changed creation fingerprint | `test_s17_reused_pid_or_changed_os_fingerprint_rejected` |
| DC-18 | Require fresh independent owner-ownership recheck | `test_s18_independent_owner_recheck_required` |
| DC-19 | Recheck shadow-controller restriction before observation | `test_s19_authority_rechecked_after_initial_snapshot` |
| DC-20 | Verify real release HEAD matches pinned SHA | `test_s20_actual_release_head_fenced_to_pinned_sha` |

## Current running machine (read-only observation)

- No current `continuous_flow_supervisor` or `laptop_actor_bootstrap`
  processes were seen in the last inspection.
- Scheduled `ExponentialEngine-ContinuousFlow-Watchdog` and
  `ExponentialEngine-LaptopActor` remained disabled.
- `IronGodLocal/controller-handoff-state.json` remained
  `shadow_state_only`, `candidate_execution_authorized=false`.
- The original `iron-god-dispatch-claims.sqlite` contains **no installed
  `iron_god_desktop_requests` table**. A `mode=ro` CLI inspection returned
  `TABLE_MISSING`, exit code 3, `execution_authorized=false`, and left
  the database's last-modified time unchanged.
- The new CLI's read-only `mode=ro` behavior and missing-table refusal are
  exercised against disposable files in `test_iron_god_desktop_inbox.py`.

## Acceptance and blocked integration

1. **No production database schema deployment.** The transport schema has
   been tested against a separate SQLite backup, not installed on the laptop's
   operational ledger.
2. **No live supervisor consumption.** `shadow_consume_once` is a staged
   library function; its independent witnesses must be bound to actually
   trusted host/owner APIs before it can observe live commands.
3. **No command completion.** The only durable states remain
   `REQUESTED_NOT_AUTHORIZED` and `OBSERVED_NOT_AUTHORIZED`. The desktop
   proof modules still require separately authorized `APPLIED`/`PAUSED`
   controller acknowledgment and verified runtime transition.
4. **No installed shortcut changes, old-marker reads, task starts or stops.**
   The previously identified retired marker and Remote Desktop Commander stop
   paths remain unresolved in deployed scripts.
5. **No production cutover** until the owner-control authority is resolved
   through the documented permitted interface, the runtime is proven to
   invoke the current local release, and end-to-end real-button testing passes.
6. **Native full preflight** is a separate acceptance gate. Focused tests and
   Ruff are not substitutes for exact-head full preflight.

This batch improves safe implementation readiness but is **not** evidence that
the Iron God autonomous factory is running. Do not count these development
slices as accepted product delivery milestones.
