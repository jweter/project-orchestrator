# Jev Shadow Evidence Ledger

This layer closes the measurement loop for Jev without granting it authority.

Each record binds a bounded decision to:

- the deterministic legal candidate set;
- Foreman's authoritative choice;
- Jev's shadow recommendation and probabilities, when available;
- the later observed candidate outcome;
- verification evidence, retries, and verification churn.

Records are append-only JSONL telemetry. They must never contain credentials or make a shadow recommendation authoritative.

## Replay

The deterministic evaluator compares completed records and reports:

- comparable decisions;
- Foreman/Jev disagreements;
- cases where the Jev choice matched later verified advancement while Foreman's did not;
- cases where Foreman's choice matched later verified advancement while Jev's did not;
- ties or inconclusive outcomes;
- per-project sample counts.

The report is descriptive. It always emits `promotion_authority=false` and `promotion_ready=false`. A future governed promotion slice may consume sufficient evidence, but this component cannot promote itself.

## Outcome authority

An outcome may only be attached to a candidate that was in the recorded deterministic legal set. `VERIFIED_ADVANCEMENT` requires the caller to rely on ordinary repository-native verification evidence; this module does not manufacture Product Reality, CI, or preflight truth.

## Storage

Runtime ledgers should live under telemetry-owned paths (for example `telemetry/jev-shadow/decisions.jsonl`) and remain bounded/rotated by the existing telemetry maintenance policy. They are operational evidence, not a second ownership database.
