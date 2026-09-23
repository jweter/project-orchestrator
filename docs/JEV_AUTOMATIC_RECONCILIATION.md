# Jev Automatic Reconciliation

The Jev shadow evidence loop is wired to the same identities used by Foreman specialist lanes.

## Decision capture

`records_from_foreman_plan()` converts only meaningful multi-candidate Jev observations into evidence records. The authoritative deterministic choice is copied from Foreman's selected lane. Singleton decisions are not recorded as neural comparisons because no Jev inference is needed.

## Outcome reconciliation

`reconcile_records()` consumes sanitized worker/orchestrator results and closes matching open evidence records by `lane_id` / `candidate_id`.

Only explicit terminal evidence is translated:

- `MERGED`, `PRODUCT_REALITY_VERIFIED`, or `VERIFIED_ADVANCEMENT` -> `VERIFIED_ADVANCEMENT`
- deterministic failure states -> `FAILED`
- cancelled/rejected/abandoned -> `ABANDONED`
- pending, unknown, or unrelated results leave the record open

This component does not infer success from branch existence, code changes, worker activity, a Jev recommendation, or an asynchronous wait.

The result is a closed measurement loop:

`Foreman legal set -> deterministic choice + Jev shadow -> bounded execution -> verified terminal result -> evidence ledger -> replay evaluator`

Jev remains advisory-only throughout.
