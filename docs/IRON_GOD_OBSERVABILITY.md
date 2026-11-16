# Iron God live observability: MCP + SQLite (issue #710)

## Scope and authority

These additions **observe** the running engine. They never authorize a worker,
change owner control, create a task, claim a lease, alter model routing, merge
a PR, or install a release. Current grants, the release pin, and native
product acceptance remain governed by their existing owners.

During commissioning, the supervisor's legacy `authorized_products: []`
and legacy worker pool of zero are **expected**; the independent Canon and
Product lanes can still be dispatching work. Reports must prefer their fresh
evidence, never infer productivity from heartbeat alone.

## Read-only MCP interface

The existing local stdio MCP server exposes:

- `get_engine_snapshot`: unchanged minimal compatibility snapshot.
- `get_development_diagnostics(history_limit=8)`: fresh, allowlisted
  supervisor/Canon/Product status; worker-pool and memory observations;
  per-repository eligibility exclusions; approved roadmap counts;
  current-vs-stale evidence timestamps; bounded recent SQLite events.
  Missing or mismatched generation data yields `UNVERIFIED`.
- `list_messages`, `post_message`, `ack_message`: existing advisory
  communication. Mutation tools remain opt-in and never grant execution.

The resident advisory responder continues to accept
`engine_status_v1` with reference `protocol:engine-status-v1`. It also
answers the **exact** `engine_status_v2` question with reference
`protocol:engine-status-v2`, sending a bounded evidence-only JSON status
before acknowledging the query. Handoffs, mismatched references and commands
are not consumed by the status protocol. A response is **not** authorization.

## Append-only SQLite event ledger

Installed supervisor iterations append a deduplicated observation, after their
continuation sequence has been durably published, to:

`<state-root>/observability/development-events.sqlite`

The event identity is SHA-256 of supervisor instance plus continuation
sequence. The schema uses `development_events` and immutable UPDATE/DELETE
triggers. Retries with the same sequence are idempotent. No ledger is created
for unverified/stale supervisor generations. Queries use SQLite read-only mode.

This is a diagnostic projection only. It does not reuse or mutate
`mailbox/coordination.sqlite`, `IronGodControl/owner-control.sqlite`, grant
files, leases, or roadmap state. A failed telemetry write is best effort and
does not block an authorized worker pass.

## Stall diagnosis and recovery boundaries

The snapshot distinguishes:

1. Missing/stale/mismatched generation evidence: `UNVERIFIED`.
2. Owner-controller permission denied: `OWNER_EXECUTION_NOT_PERMITTED`.
3. Fresh Canon/Product dispatch or implementation: active but **progress
   unverified**, even when the legacy worker pool is zero.
4. Product preflight failure: `PRODUCT_CANONICAL_PREFLIGHT_FAILED` when
   explicitly reported by the Product lane.
5. Canon idle with a **fresh** zero-eligible frontier but approved roadmap:
   `APPROVED_ROADMAP_WITH_NO_ELIGIBLE_SLICES`.
6. Stale eligibility: historical reasons only, never evidence of current
   global starvation. Memory admission only limits capacity, not authority.

The response supplies the smallest safe next diagnostic/repair direction,
not an auto-dispatch command. The existing Product/Canon schedulers alone
perform independently approved and authenticated dispatch, retries and
cross-repository rotation. The current safe improvement is to expose the
specific exclusion reasons and the true lane outcome, so authorized work
can repair the right preflight or capability failure. Do not reset attempt
caps, raise budgets, weaken memory floors, or reauthorize spent plans.

## Test and owner-controlled rollout

Regression tests: `tests/test_iron_god_observability.py`; existing MCP
protocol tests include the new read-only tool. Canonical repository preflight
must pass on the exact branch head, followed by independent GitHub CI/review.

A merge is **not** an installation. After an approved exact-head release
passes the owner's staging gate, the owner pauses Iron God for installation,
verifies local preflight and release receipt, and alone chooses when to
activate Take the Wheel. The existing hourly watcher remains read-only.
