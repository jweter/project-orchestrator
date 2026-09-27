# Agent Evaluation and AI-Regression Harness

## Purpose

Project Orchestrator evaluates coding-agent configurations against **pinned deterministic regressions** rather than impressions. The evaluator is advisory: repository authority, preflight, CI, reviews, Product Reality, security, licensing/provenance, and human-only decisions remain unchanged.

Phase C deliberately does **not** create a second KPI database. Evaluation attempts are converted into ordinary append-only events in the existing canonical ledger:

`engineering/kpi-events.jsonl`

The evaluator then reports from that same evidence.

## Benchmark suite

The initial suite is:

`engineering/agent-eval-suite.json`

It contains three project-orchestrator regressions pinned to an exact baseline commit:

1. autonomous-loop independent-judge protection;
2. MCP literal-secret detection in the harness audit;
3. KPI `UNKNOWN` preservation.

Each task declares:

- exact repository;
- exact baseline commit SHA;
- a committed fault-injection patch;
- deterministic prompt;
- deterministic measurement contract;
- repository-native verification commands;
- required promotion stages.

The fault patches live under:

`engineering/agent-eval-fixtures/`

These patches are test fixtures. They intentionally make a known invariant fail. They must never be applied to `main` or to a product working tree that contains valuable uncommitted work.

## Safe execution pattern

Run an evaluation task only in a disposable checkout/worktree created from the task's exact `baseline_ref`.

Conceptually:

```text
pinned baseline
  -> disposable worktree/clone
  -> apply task fault fixture
  -> prove the measurement contract is RED
  -> hand the task prompt to the candidate coding agent
  -> record attempts/repairs/interventions/checks
  -> run repository-native verification
  -> record deterministic acceptance
  -> append events to canonical KPI ledger
  -> compare only like-for-like task coverage
```

The evaluator does not launch paid agents and does not require an external service in CI. A local worker, Codex, Claude, or another coding harness can execute the task, but its identity/configuration must be declared in the attempt record.

## CLI

Validate the suite and committed fault fixtures:

```bash
project-orchestrator agent-eval validate-suite
```

Validate a transient attempt record without writing it:

```bash
project-orchestrator agent-eval validate-attempt attempt.json
```

Record one completed or partial attempt into the canonical KPI ledger:

```bash
project-orchestrator agent-eval record attempt.json
```

Generate an advisory report from recorded events:

```bash
project-orchestrator agent-eval report \
  --start 2026-09-16T00:00:00Z \
  --end 2026-09-17T00:00:00Z
```

## Attempt evidence

An attempt JSON is transient input, not a second durable metrics store. It identifies:

- suite/task;
- evaluation run;
- coding agent and configuration;
- exact pinned baseline;
- measurement contract;
- start time;
- ordered preflight/PR-CI observations;
- repair attempts and deterministic failure classes;
- human interventions;
- deterministic acceptance;
- measured cost only when explicit evidence exists.

`record` validates the full attempt first and then emits standard KPI events. All agent-evaluation events carry `metadata.agent_eval` so they can be queried without polluting ordinary product-flow metrics. They use project scope `portfolio`, which the existing product KPI layer already excludes from product health calculations.

## Metrics

For each agent/configuration under one suite and one pinned baseline, reports expose:

- deterministic task pass rate;
- first-pass preflight rate;
- first-pass PR-CI rate when PR-CI evidence exists;
- median repair iterations;
- human-intervention rate;
- median elapsed seconds for completed deterministic tasks;
- repeated deterministic failure classes;
- measured cost by currency only when explicit evidence was supplied;
- task coverage.

Missing evidence is `UNKNOWN`, never zero.

A comparison is `COMPARABLE` only when at least two agent configurations have identical task coverage under the same suite/baseline. Otherwise it remains `INSUFFICIENT_DATA`.

## Product Reality boundary

The initial suite permits only:

`acceptance_kind = DETERMINISTIC`

Visual quality, musical interpretation, gameplay feel, tone/fingering judgment, scientific judgment that lacks a mechanical oracle, and other Product Reality cannot be converted into an agent benchmark PASS.

The evaluator sets:

- `advisory_only = true`
- `product_reality_may_be_inferred = false`

These are enforced by validation and regression tests.

## AI-regression role

The committed fault fixtures create controlled regressions that normal "write code, then review your own code" loops can miss. A coding agent must repair a known broken invariant while the repository's independent tests and preflight judge the result.

This is the first AI-regression layer. It does not claim that three tasks represent all engineering ability. Additional tasks may be added only when they represent a real, deterministic, repository-authorized failure class and can be pinned to a reproducible baseline/measurement contract.

## Expansion rule

Do not grow the benchmark simply to produce more scores.

Add a task only when all are true:

1. the failure class matters to the repository;
2. the regression can be reproduced without private/restricted data;
3. acceptance is deterministic;
4. the benchmark cannot weaken repository gates;
5. the task has an exact baseline and committed fault fixture;
6. results can be recorded in the canonical KPI ledger;
7. the new task will improve a real agent-selection, prevention, or regression decision.

Phase D should use the resulting evidence to decide whether repository-history skill extraction or additional adversarial regression tasks actually reduce repeat failures.
