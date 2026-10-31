# Canon Engineering Review — Independent Findings & Triage

Review workbook (local owner-provided source): `Downloads/Canon_Engineering_Review.xlsx`,
six sheets; prepared by Claude on 2026-10-25. Claude explicitly reported
its review VM could not verify the Git head and did not run the real test suite.
**Confirmed by Claude** means source inspection or review-harness reproduction;
it is not equivalent to independent exact-head native verification on Azathoth.

## Findings by remediation slice

| Gate | Review IDs | Independent next action | State |
|---|---|---|---|
| S1 Ledger integrity | C6 C7 C8 | Ordered event history, sealed packet projection, atomic file replace, lock and compare-and-swap | SOURCE FIX + focused native tests passing; NOT accepted/deployed |
| S2 State transition rules | C3 C4 C5 | Explicit legal edges, no silent re-dispatch from completed states, worker PASS hands back unverified until independent review | SOURCE FIX + native focused PASS; NOT accepted/deployed |
| S3 Plan/secret/path hardening | C9 C10 C19 | Windows/UNC/glob path authority, secret corpus, verification-command provenance | OPEN |
| S4 Worker identity / stall / questions | C11 C12 C13 | Bound worker claim, heartbeat/expiry, retries/cap, answered-question replay | OPEN |
| S5 Admission conflicts | C14, H4 | Live exact-head and leased path/branch overlap proof | OPEN |
| S6 Independent handback | C5 C15 | Typed artifacts, separate verifier, safe successor after parking/replan | OPEN |
| S7 Iron Memory binding | C17 C18 | Freshness and retrieval policy with source evidence; current memory module only prepares advisory context | PARTIAL (candidate, not wired) |
| S8 Verified memory write-back | C17 | Write only independently accepted, sanitized lessons to existing learning store | OPEN |
| S9 Portfolio scope | C16 | Verify orchestrator scope against existing owner/activation authority | OPEN |
| S10 Operational wiring | C1 C2 | Controller-safe admission, complete job handoff, worker ask/poll and exact-head acceptance proof | OPEN |

Additional unverified hypotheses H1-H3 concern free-form supervisor guidance,
nested mutable snapshot aliasing and event-validation scaling. S1's deep-copy
validation addresses a substantial portion of H2; a dedicated mutation test
was added. Do not mark other hypotheses closed without independent evidence.

## S1 native proof and residual limitations

The isolated S1 implementation is in
`fix/iron-canon-ledger-integrity-20261025`, based on the existing Canon
and memory-retrieval commits. Local regression tests cover repeated identical
transition payloads, corrupt/missing/reordered events, unauthorized packet
flags, snapshot aliasing, interrupted publication, revision conflicts and
two concurrent stale writers.

The seal/hash chain detects accidental and unsophisticated tampering but
does NOT authenticate the writer: a malicious actor with write access can
recompute both. Authority, exact-head trust, worker identity, and independent
promotion remain separate prerequisites. Schema v1 projections are refused;
a verified migration must be designed if production v1 records are discovered.

Do not deploy this ledger from a source-only test result. Before acceptance,
complete project-native exact-head preflight and integrate only after S2-S10
authority/ownership gates. Never create a competing ledger or controller.
