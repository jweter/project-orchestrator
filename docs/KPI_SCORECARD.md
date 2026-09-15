# Portfolio KPI Scorecard

## Purpose

The scheduler report must answer two different questions without conflating them:

1. **Did this DoWork run materially advance product engineering?**
2. **Is the portfolio becoming faster, cleaner, and less bottlenecked over time?**

The first question is current-run execution. The second requires historical evidence. The KPI scorecard combines both while preserving the repository's fail-closed rule: missing evidence is `UNKNOWN` or `INSUFFICIENT_DATA`, never an invented zero or guessed percentage.

The scorecard does **not** create a second metrics database. It projects existing authoritative evidence from:

- `engineering/compounding-timeseries.json` through `project_orchestrator.timeseries`;
- explicit current-run rework evidence through `project_orchestrator.throughput_guard.compute_rework_kpis`;
- measured bottleneck ranking through `project_orchestrator.throughput_optimizer`.

## Scorecard layers

### 1. Current-run execution

The existing scheduler KPIs remain the first layer:

- independent projects inspected: target `3/3`;
- product repositories inspected: target `5/5`;
- eligible projects;
- projects materially advanced by the current orchestrator run;
- product advancement rate among projects explicitly proven eligible;
- throughput-floor status;
- preventable repeat failure target: `0`.

Only current-run `ORCHESTRATOR` actions count as advancement. Human merges, CI completion, bots, and other external changes remain visible but do not satisfy the throughput floor.

### 2. Rework quality

The report exposes the four throughput-guard rework KPIs:

| KPI | Target | Meaning |
| --- | --- | --- |
| First-pass preflight rate | `>= 0.90` | How often explicit preflight evidence shows the branch passed on its first attempt. |
| Merge-race count | `0` | Explicitly observed races between active implementation/repair and an external merge/completion. |
| Post-merge correctness-hotfix rate | `< 0.05` | Fraction of merged records with explicit evidence that a correctness hotfix was required after merge. |
| Median implementation-to-merge seconds | trend downward without quality regression | Median elapsed time only where both implementation start and merge timestamps are explicitly known. |

These KPIs are computed only from explicit records supplied to the run. Missing evidence stays `UNKNOWN`.

### 3. Delivery progress

Historical progress comes from the canonical compounding time-series store. The report shows the same curated metrics at three scopes: portfolio, independent product project, and each product repository.

| Metric | Direction / target |
| --- | --- |
| PR creation to GREEN seconds | trend down |
| Merge-cycle seconds | trend down |
| Failure-to-repair seconds | trend down |
| CI failure count | trend down |
| CI repeat-failure count | `0` |
| Blocked/waiting seconds | trend down |
| Roadmap slices completed | trend up |

A measured metric preserves its unit, sample count, evidence references, and aggregation coverage. A scope with no evidence window remains visible as `INSUFFICIENT_DATA` with `UNKNOWN` metrics.

This avoids a common reporting failure: a strong portfolio average must not hide that one repository has no usable measurement history.

## Bottleneck tracking

The scorecard reuses `project_orchestrator.throughput_optimizer` rather than introducing a second ranking model.

The optimizer considers only currently eligible independent product projects and only these measured dimensions:

- merge-cycle burden;
- failure-to-repair burden;
- CI-failure burden;
- repeat-failure burden.

A project needs at least three evidence windows and at least two measured bottleneck dimensions before it can enter an ordinal ranking. Otherwise it remains `INSUFFICIENT_DATA` with the exact reason. Eligible projects with zero historical windows are still emitted in the report instead of disappearing.

The ranking is **advisory**. It cannot override P0-P4 priority, product authority, Product Reality, dependency/ownership constraints, preflight, CI, review, security, licensing/provenance, privacy, or the central merge arbiter.

The bottleneck score is an ordinal measured-severity signal. It is not predicted ROI, capacity, probability, percent complete, or time saved.

## Evidence coverage

Every scheduler report now includes evidence coverage so missing KPI data itself becomes actionable engineering information:

- total evidence windows;
- latest recorded window end;
- window count by independent product project;
- window count by each product repository;
- product projects with no evidence windows;
- product repositories with no evidence windows.

This is intentional. A dashboard full of `UNKNOWN` values is not a failed report if the underlying observation system truly lacks evidence; it is a precise description of the measurement bottleneck that needs to be closed.

## Run-evidence fields for rework KPIs

A project's current scheduler evidence may include these optional explicit fields:

- `preflight_attempts`: positive integer;
- `merge_race`: boolean;
- `merged`: boolean;
- `post_merge_correctness_hotfix`: boolean when `merged=true`;
- `implementation_started_at`: timezone-aware ISO-8601 timestamp;
- `merged_at`: timezone-aware ISO-8601 timestamp.

The scheduler report extracts only fields that are present. It does not infer any of them from promotion state or prose.

## Operational use

Before claiming portfolio improvement, DoWork should continue to run `project-orchestrator collect-evidence` and query the time-series evidence as required by `DoWork.txt`. The scorecard makes that evidence visible in the normal executive report; it does not relax the evidence-collection requirement.

Use the scorecard to drive questions such as:

- Which product is consuming the most measured merge/repair time?
- Are repeat CI failures recurring in one project?
- Are first-pass preflight and post-merge correctness moving toward their targets?
- Which repository has insufficient evidence to support a progress claim?
- Is engineering activity translating into product advancement, or only into more infrastructure?

## Non-goals

The scorecard never reports guessed percent complete, invented ROI, speculative delivery dates, predicted developer capacity, or a fabricated GREEN status from absent data. Product Reality remains a separate acceptance dimension and must never be inferred from CI or throughput metrics.
