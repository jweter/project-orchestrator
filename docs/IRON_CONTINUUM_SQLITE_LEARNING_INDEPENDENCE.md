# IRON CONTINUUM — independent SQLite learning evidence

Issue #747 S4. The existing `development-memory.sqlite3` remains the
single canonical worker-attempt record. The rules table is **advisory** and
never supplies a new owner grant, worker lease, verification receipt or
accepted product outcome.

## Root cause

Previously `derive_rules()` counted distinct `item_id` values as
independent training evidence. A single roadmap microtask retried under
`-r1` and `-r2` could therefore satisfy the two-independent-slice
threshold all by itself, even though the three attempts shared one
objective and failure cause. The associated `stats()` first-attempt rate
also counted retries as additional initial slices.

## Correction

- Reuse the **existing backlog lineage parser** to normalize repeated
  `-rN` revisions into the same canonical task lineage.
- Group each failure class's SQLite packet evidence by lineage, not by
  revision ID. Preserve every immutable attempt and cited packet.
- Require the same known failure class on at least two **independent
  lineages** before deriving an inference. Never infer a skill or owner
  permission from these worker-reported classes.
- Retract only obsolete `INFERRED_RECOMMENDATION` rows that no longer
  meet the independence threshold. Leave externally governed rows and
  attempts untouched.
- Report first-attempt success statistics by lineage, not by retry ID.

## Evidence and limits

`test_iron_continuum_sqlite_learning.py` covers repeat revisions,
independent second tasks, retraction of inflated legacy rules without
erasing attempts, and correction of first-attempt statistics.

These are still **worker-observation-derived recommendations**, not
independently corroborated native product acceptance. Subsequent skill
promotion requires stronger provenance, exact-head CI and real native
product verification. This is an S4 correctness upgrade, not S4 end-to-end
completion.
