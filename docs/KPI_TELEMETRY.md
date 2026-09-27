# Durable KPI telemetry

## Purpose

Project Orchestrator records flow evidence once, at the moment a state transition is observed, and reports from that durable evidence later. KPI reports must not reconstruct missing history from prose, current state, or assumptions.

The existing KPI definitions and targets in `src/project_orchestrator/portfolio_kpis.py` remain authoritative. This telemetry layer supplies durable transition evidence and additional observational flow metrics; it does not silently redefine existing targets.

## Durable stores

- `engineering/kpi-events.jsonl` â€” append-only, one content-addressed transition event per line.
- `engineering/kpi-run-snapshots.jsonl` â€” append-only, one portfolio snapshot per scheduler/run id.

Neither file is a mutable dashboard. A report is a projection of the ledger. Re-running a report changes no historical event.

## Event contract

Every event binds the observation to:

- ISO-8601 `observed_at` timestamp;
- scheduler/agent `run_id`;
- portfolio `project` and exact `repository`;
- `entity_type`: `pr`, `ci`, `branch`, or `issue`;
- stable `entity_id` within the repository;
- explicit `from_state` and `to_state`;
- semantic `transition_class`: `NEUTRAL`, `ADVANCE`, `REGRESSION`, `FAILURE`, `REPAIR`, `PROMOTION`, `HYGIENE`, or `HUMAN_INTERVENTION`;
- actor class: `ORCHESTRATOR`, `EXTERNAL_AUTOMATION`, or `HUMAN`;
- one or more durable `evidence_refs` such as a PR, issue, workflow-run, commit, or branch reference;
- optional structured metadata.

`event_id` is derived from the complete normalized event content. Re-appending the identical event is idempotent. A different event is always a new line; prior lines are never rewritten.

## Recording rule

Record transitions when they happen, not at report time. In particular:

- PR: opened/drafted, preflight-green, ready/green, merged/closed when known.
- CI: pending, first terminal result, failure, repaired/success, cancelled/platform-gated when represented by an authorized state.
- Branch: created, active, stale/hygiene-debt, deleted.
- Issue: started/advanced, blocked/regressed, repaired, promoted/closed when the transition is evidenced.
- Human intervention: use actor `HUMAN`; use `HUMAN_INTERVENTION` when the intervention itself is the material transition being measured.

Do not synthesize a transition because an entity is absent from a later query unless the final state is actually verified.

## Metrics

The telemetry projection reports:

- **Issue advancement rate** â€” distinct issues with an `ADVANCE`, `REPAIR`, or `PROMOTION` transition divided by distinct issues with a material transition in the reporting period.
- **Cycle time** â€” median elapsed time from an evidenced PR open/draft state to an evidenced merge, for merges completed in the period.
- **Repair latency** â€” median elapsed time from an evidenced CI failure to the next evidenced successful terminal transition for that CI entity.
- **Promotion latency** â€” median elapsed time from an evidenced `PREFLIGHT_GREEN`/`GREEN` PR state to merge.
- **First-pass CI rate** â€” share of CI entities whose first evidenced terminal result is success, for first terminal results occurring in the period.
- **Branch net delta** â€” evidenced branch creations minus evidenced branch deletions in the period.
- **Branch hygiene debt** â€” count of observed branches whose latest evidenced state at report end is `STALE`, `MERGED_UNDELETED`, `ORPHANED`, or `HYGIENE_DEBT`.
- **Human intervention rate** â€” human-attributed material transitions divided by all material transitions in the period; the report also includes the raw human-intervention count.

The existing first-pass **preflight** target remains the authoritative rework KPI. `first_pass_ci_rate` is an observational companion and must not overwrite or substitute for that target.

## Missing evidence

No-event and incomplete-pair cases remain `UNKNOWN`. They are not converted to zero. Examples: a merge without a recorded open transition cannot produce cycle time; a success without a recorded preceding failure cannot produce repair latency.

Control-plane events from `jweter/project-orchestrator` are retained in the ledger for auditability but excluded from product-flow reports so the control plane cannot make the four products look healthier.

## Querying

The installed CLI is:

```text
project-orchestrator-kpi validate
project-orchestrator-kpi record-event path/to/event.json
project-orchestrator-kpi report
project-orchestrator-kpi report --at 2026-09-16T01:00:00Z
project-orchestrator-kpi finalize-run --run-id <run-id>
```

`report` always returns 24-hour, 7-day, and 30-day windows. `finalize-run` appends one per-run snapshot containing run-scoped metrics plus those three rolling reports.

## Operating requirement

Future scheduler/agent integrations should call the telemetry recorder at the same transition point where the ledger/comment/status change is made, then finalize one snapshot at the end of the run. This makes KPI collection part of the state machine instead of a later analytics reconstruction step.
