# VNext: Compounding Engineering Learning Engine

## Goal

VNext turns autonomous execution into measurable process improvement. The system must not merely remember failures; it must reuse verified lessons, move deterministic detection earlier, measure whether the reuse prevented work, and improve the engineering process through the same governed branch/preflight/CI path used for product changes.

## Required closed loop

`OBSERVE -> DIAGNOSE -> FIX -> VERIFY -> RECORD -> PREVENT -> REUSE -> MEASURE -> IMPROVE_PROCESS -> VERIFY_IMPROVEMENT`

A loop is incomplete until the improvement itself has evidence. A new rule, test, validator, or scheduler behavior is not proof of improvement merely because it exists.

## Milestone 1: Structured learning ledger

Extend existing learning memory rather than creating a competing source of truth. Normalize verified failure fingerprints and verified success patterns around repository, affected area, root cause, repair/pattern, verification evidence, regression/reuse capability, recurrence state, and evidence state.

Acceptance:
- sanitized verified evidence only;
- deterministic deduplication;
- failures and successful patterns are both first-class;
- private Product Reality payloads, credentials, secrets, and restricted content never enter the ledger.

## Milestone 2: Repair and capability reuse

Before reasoning from scratch, query applicable verified history. A known deterministic failure may select a previously verified repository-authorized repair or prevention rule. Reuse never bypasses exact-head verification.

Initial target pattern: deterministic formatting failures should be caught by repository-authorized formatting/static gates before an expensive unattended Windows worker or equivalent environment gate when repository policy permits.

Acceptance:
- match is evidence-backed and area-specific;
- automatic mutation remains limited to explicitly authorized deterministic classes;
- full exact-head preflight still follows repair;
- reuse count and recurrence are measurable.

## Milestone 3: Verification cost ladder

Learn where cheap deterministic gates can safely detect known defect classes earlier. Required final gates remain unchanged.

Candidate ladder:
1. known fingerprint detector;
2. repository-authorized format/static checks;
3. targeted regression;
4. authorized FAST_GATE;
5. canonical exact-head preflight;
6. fresh PR CI;
7. Product Reality where required.

Promotion of an earlier detector requires repeated verified evidence, not intuition.

## Milestone 4: Process improvement detector

Analyze verified history for recurring engineering friction:
- repeat deterministic failures;
- stale branches and merge races;
- CI retry clusters;
- cross-platform failures;
- expensive gates catching cheap defects;
- eligible product starvation;
- slow verification stages;
- routine human checks with deterministic substitutes.

Prefer executable prevention over prose. Safe bounded control-plane improvements may be implemented automatically on an ordinary feature branch. Anything outside existing authority becomes an issue or Jeremy escalation under the existing human-escalation policy.

## Milestone 5: KPI feedback controller

KPIs become scheduler inputs rather than report decoration. Deviations trigger diagnosis and bounded process improvement.

Key signals:
- Product Advancement Rate;
- first-pass preflight rate;
- merge-race count;
- post-merge correctness-hotfix rate;
- implementation-to-merge seconds;
- failure-to-repair seconds;
- repeat-failure count;
- reusable-capability reuse;
- eligible-lane starvation.

Unknown evidence remains UNKNOWN.

## Milestone 6: Counterfactual evidence

Measure the value of learning only where both sides are observable. Examples include a known fingerprint preventing a repeated CI failure, a deterministic validator replacing a routine manual check, or an inexpensive gate preventing dispatch of an expensive worker gate.

Never invent hypothetical time savings. If comparable elapsed-time evidence is unavailable, report the prevented step or failure rather than estimated minutes saved.

## Maturity model

1. **RECORD** — verified lessons persist.
2. **PREVENT** — lessons create executable regression protection.
3. **REUSE** — prior capabilities are selected when applicable.
4. **OPTIMIZE** — measured recurring friction changes the engineering process.
5. **COMPOUND** — sustained comparable evidence demonstrates increasing verified advancement and/or decreasing rework/cycle time without quality regression.

## Safety invariant

The Learning Engine cannot grant itself merge authority, weaken checks, alter Product Reality truth, expand security/privacy boundaries, accept licensing/provenance risk, or bypass repository-local authority. Changes to the Learning Engine itself must pass ordinary branch, test, exact-head preflight, PR CI, review, and merge gates.
