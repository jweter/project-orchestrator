# Unattended Verification Policy

## Purpose

Routine engineering verification is machine-owned. Jeremy is not a recurring test executor and must not be placed inside ordinary implementation, regression, packaging, Product Reality, or promotion loops when an objective test can be automated or delegated to an unattended execution environment.

This policy applies across the entire portfolio and is intended to be enforced by DoWork scheduling, repository control planes, CI, local workers, and laptop-based test agents.

## Operating principle

The default loop is:

`repository change -> FAST_GATE -> exact-head preflight -> CI -> unattended environment-specific verification -> structured evidence -> automated diagnosis/repair -> re-verification -> promotion -> optional Jeremy milestone acceptance`

A test that needs Windows, Unreal, Ollama, local hardware, a packaged build, desktop software, or another environment not available in ordinary CI is not automatically a Jeremy task. It should first become an unattended worker task.

## Human-out-of-the-loop rule

When work reaches a verification step, classify the required evidence in this order:

1. **Repository-native deterministic test** — run in unit/integration/preflight/CI.
2. **Unattended environment test** — run on an approved idle worker such as Jeremy's laptop when the environment is required.
3. **Automatable Product Reality probe** — capture structured metrics, screenshots, logs, traces, generated artifacts, timing evidence, build/install results, game/editor telemetry, or other machine-evaluable observations.
4. **Human judgment** — use only when the remaining question is genuinely subjective, ambiguous, irreversible, safety-sensitive, or a product-direction decision.

`Jeremy must run this manually` is therefore a workflow deficiency unless category 4 actually applies.

## Unattended worker contract

An unattended worker must:

- operate only on explicitly authorized repository/test requests;
- never expose or commit secrets, private source material, commercial media, restricted game assets, or unrelated personal files;
- identify repository, branch, exact commit SHA, test request, worker/environment identity, and timestamp;
- capture the commands/actions performed and bounded stdout/stderr/log evidence;
- produce a machine-readable result of `PASS`, `FAIL`, `REVIEW_REQUIRED`, `PRODUCT_REALITY_REQUIRED`, or `ENVIRONMENT_FAILURE`;
- attach or reference permitted artifacts needed to reproduce the finding;
- write results back to the appropriate repository issue, PR, artifact store, or authoritative evidence ledger;
- preserve fail-closed behavior: missing evidence is never treated as PASS;
- leave the machine and application state safe after completion.

Workers may run while the laptop is idle. They must not require Jeremy to watch, click through ordinary steps, copy results manually, or remember to report completion.

## Scheduler behavior

DoWork must treat an unattended-capable environment requirement as a schedulable verification lane, not as `NEEDS_JEREMY` by default.

Preferred states include:

- `AUTOMATED_VERIFICATION_PENDING`
- `AUTOMATED_PRODUCT_REALITY_PENDING`
- `ENVIRONMENT_WORKER_PENDING`
- ordinary existing promotion/failure states when evidence has returned.

A pending laptop/worker test blocks only the dependent acceptance lane. Parallel-safe development continues under the existing project-liveness and anti-stall rules.

When evidence returns, the orchestrator should consume it without human relay, classify failures from actual evidence, repair when authorized, rerun verification, and continue promotion.

## Jeremy's role

Jeremy may always test a new version, milestone, release candidate, gameplay experience, UI, report, or other product behavior. That testing is valuable acceptance feedback, but it is optional unless repository authority proves that a genuinely human judgment is required.

Jeremy should be escalated only for the portfolio's existing human-escalation categories plus irreducibly subjective acceptance that automation cannot responsibly decide.

Routine requests such as "run this build", "paste this log", "try this command", "check whether the program launches", or "repeat this regression test" should be designed out of the workflow.

## Continuous improvement requirement

Every recurring manual test discovered during development is a candidate automation defect. The engineering loop should ask:

1. Can the observation be expressed as a deterministic assertion or metric?
2. Can a fixture/simulator remove the environment dependency?
3. Can CI execute it?
4. If not, can an unattended worker execute it safely?
5. Can the result be written back in a machine-readable form?
6. Can regression protection prevent the same manual request from recurring?

Prefer building that capability over asking Jeremy to repeat the test.

## Portfolio targets

### Knowledge Engine

Local Core/Ollama research runs, persistent-store benchmarks, acquisition/retrieval checks, browser/service integration, and environment-specific acceptance should be automated through CI or an unattended local worker. Jeremy is not the transport layer for logs or benchmark results.

### Rocksmith Song DLC Generator

Windows packaging, launcher-safe checks, generated-workspace validation, timing/alignment metrics, deterministic chart inspection, and other objectively measurable desktop Product Reality should flow through the unattended Rocksmith worker. Private/commercial source restrictions remain unchanged. Subjective musical feel/tone/fingering judgment stays human only when objective evidence cannot settle it.

### Everward

UBT builds, Unreal automation tests, packaged launch checks, deterministic simulation probes, save/load tests, controls/HUD instrumentation, screenshots and objective visual-state assertions should move through unattended workers where CI cannot execute them. Subjective gameplay/visual quality remains optional milestone acceptance unless explicitly required by product authority.

## Definition of success

The portfolio has achieved the intended operating model when ordinary commits and PRs can progress from implementation through environment-specific verification and evidence return without Jeremy being required to sit at the laptop, execute commands, copy logs, or repeatedly playtest incremental changes.
