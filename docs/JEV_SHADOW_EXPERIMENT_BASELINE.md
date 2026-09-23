# Jev Shadow Experiment Baseline

The shadow infrastructure is complete enough to distinguish infrastructure observations from actual Jev performance evidence.

Run:

`project-orchestrator jev-experiment-report`

The report classifies the persistent ledger into three gates:

- `NO_LIVE_JEV_OBSERVATIONS`: only deterministic/synthetic shadow requests exist. No claim about Jev quality is permitted.
- `LIVE_OBSERVATION_PENDING`: at least one real neural shadow recommendation exists, but terminal outcome evidence is still pending.
- `MEASURED_LIVE_BASELINE`: at least one real neural recommendation has a terminal outcome and may enter comparison/calibration analysis.

This prevents a dangerous measurement error: `SHADOW_PENDING` is evidence that the integration found a multi-candidate decision, not evidence that Jev made a good or bad decision.

## Activation boundary

The existing provider adapter remains fail-closed. A real neural observation requires both:

- explicit live shadow enablement;
- a TypeSafe API credential supplied through the environment.

Neither the experiment report nor the unattended runtime can create credentials, approve provider cost, or promote Jev authority.

Until live observations exist, the correct next gate is `ENABLE_EXPLICIT_LIVE_SHADOW_PROVIDER`, not a fabricated performance conclusion.
