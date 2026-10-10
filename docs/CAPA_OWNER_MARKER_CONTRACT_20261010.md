# CAPA: owner marker contract divergence (2026-10-10)

## Observed failure signature

The canonical Azathoth `flow-state/human-mode.json` was readable and
contained JSON `"enabled": false`, sourced from `master-owner-control`.
However, the supervisor's `human_mode_active` and the Windows startup
installer interpreted **mere file existence** as owner control. The network
guardian and mailbox fallback used the same presence-only rule. Meanwhile,
the independent PR controller already required a validated boolean false
to authorize work. This disagreement could leave Development ON while the
supervisor and recovery paths stayed suppressed.

The last observed supervisor generation heartbeat was
`2026-10-09T19:08:47Z`; the 2026-10-10 investigation found no matching
live orchestrator process and zero recorded workers. This is evidence of
lost productive liveness, **not** proof that this single contract mismatch
caused every supervisor exit. Prior grant-ledger corruption and launcher
failures remain independently tracked.

## Root cause and corrective action

A historical contract treated marker presence as the lock and required
deletion for handback. The newer owner-control interface persists the
marker and changes the strict `enabled` boolean instead. The two
semantics were never reconciled across Python, PowerShell, startup and
recovery paths.

The corrected contract is: an absent legacy marker or a readable JSON
object with literal `enabled:false` allows development; literal
`enabled:true`, invalid JSON, wrong types, unreadable files, and partial
writes veto development. The legacy `give-me-control` network marker
remains a veto. No code writes to, removes, or toggles the owner marker.

The startup installer now uses the same read-only gate before launching,
and does not kill existing supervisors during installation. The persistent
launcher checks ownership before network guardian or supervisor startup
and again during takeover. Worker and fallback paths use the same strict
semantics.

## Prevention and release gates

Regression fixtures exercise true, false, malformed, string false, BOM,
missing and unreadable markers; network and startup behavior; and the
PowerShell fallback contract. The existing repository-native preflight,
fresh PR CI, review, merge, and runtime deployment requirements remain
mandatory.

**Effectiveness status: UNVERIFIED until merged and deployed.** After
safe deployment, require a new `process_instance_id`, fresh supervisor
heartbeat/dispatch/worker evidence, no owner-control violation, and at
least one successful productive grant-to-PR transition. Compare repeated
owner-gate false positives and eligible-idle minutes across subsequent
runs; do not claim throughput improvement from tests alone.
