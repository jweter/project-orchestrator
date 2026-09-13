# Evolution Engine

## Purpose

The Evolution Engine brings Everward's `Fix_It` philosophy into the engineering portfolio control plane.

The central loop is:

```text
observe
  -> diagnose
  -> repair
  -> verify
  -> remember
  -> prevent
  -> compare
  -> improve
  -> promote
```

The goal is not maximum code change. The goal is a system that becomes harder to break, faster to diagnose, less dependent on Jeremy for deterministic work, and better at reusing verified engineering lessons.

The engine is governed. It is not unconstrained self-modifying software.

## Canonical progression

### 1. REPAIR

Fix the smallest verified defect first.

A repair must:

- be supported by evidence;
- preserve repository authority and product boundaries;
- be bounded and reversible where practical;
- verify the original failure is gone;
- preserve or add regression protection;
- run the repository's normal exact-head verification path.

A one-off failure stays here. One incident does not justify a redesign.

### 2. REPLACEMENT

Repeated evidence may show that the repaired mechanism itself is brittle.

Replacement means replacing only that bounded mechanism with a safer alternative. It does not authorize unrelated product or architecture changes.

The replacement must prove parity with the existing acceptance contract before the old mechanism is retired.

### 3. UPGRADE

Once a repair or replacement is verified, move prevention earlier in the lifecycle when practical.

Examples:

- add a regression fixture;
- add a fail-closed invariant;
- move a deterministic failure into preflight;
- add an exact-head worker probe;
- strengthen a review/merge gate;
- automate a repetitive deterministic human check;
- reuse a proven success pattern.

The engine tracks whether earlier learning later **PREVENTED** an incident, **SHORTENED** it, produced **NO_CHANGE**, or remains **UNKNOWN**.

### 4. REDESIGN

A redesign becomes appropriate only when verified evidence shows local repair/replacement/upgrade cannot remove a recurring failure class or bottleneck.

A redesign remains subject to normal roadmap, architecture, compatibility, security, privacy, licensing, cost, and human-decision boundaries.

The engine may propose a redesign. It does not grant itself permission to cross a reserved decision boundary.

### 5. EVOLUTION

Evolution is earned, not declared.

A candidate improvement becomes an evolved portfolio capability only when comparable before/after measurements show a real improvement, no measured degradation is present, no safety/policy regression exists, and no reserved human decision is being crossed.

If measurement is unavailable, the state remains pre-Evolution. `UNKNOWN` is never converted into success.

Portfolio promotion means reusing a verified engineering process/tooling pattern where repository authority permits. It never transfers unrelated product requirements, game mechanics, music-processing behavior, or architecture between projects.

## Evidence model

The engine consumes verified, sanitized evidence. Each event is bound to:

- project;
- repository;
- stable failure/success `pattern_id`;
- evidence reference;
- evidence state;
- failure or success-pattern type;
- whether root cause is verified;
- whether a repair is verified;
- whether the mechanism is demonstrably brittle;
- whether prevention is verified;
- whether redesign is verified;
- whether a reserved human decision is involved;
- whether any safety/policy regression exists;
- whether earlier learning prevented or shortened later work.

Unverified events are ignored for promotion. If no verified evidence remains, the engine fails closed at evidence gathering.

## Measurement model

Evolution measurements are explicit before/after values with:

- repository and pattern identity;
- evidence reference;
- metric name;
- `lower_is_better` or `higher_is_better` direction;
- before/after values;
- before/after sample counts.

The engine reports only `IMPROVED`, `DEGRADED`, `UNCHANGED`, or `INSUFFICIENT_DATA` from those supplied measurements. It does not invent ROI, probability, readiness percentages, or statistical confidence.

Any measured degradation blocks Evolution promotion.

## Material review gate

Issue #49 exposed an important control-plane defect: a pull request can have green CI while a material correctness finding arrives as a `COMMENTED` review rather than GitHub's `CHANGES_REQUESTED` state.

The Evolution Engine therefore includes a fail-closed review gate:

- review evidence must be bound to the current PR head;
- a stale reviewed head requires revalidation;
- any unresolved, non-outdated material finding blocks merge regardless of `COMMENTED` versus `CHANGES_REQUESTED` state;
- informational/non-material comments do not automatically block merge.

Material findings include correctness, safety, architecture, provenance, privacy, licensing, or equivalent repository-policy concerns.

This is the first concrete example of the Evolution loop:

```text
Everward PR #251 review finding
  -> REPAIR the two adapter defects
  -> VERIFY with regression tests + fresh CI/review
  -> REMEMBER the failure class
  -> UPGRADE the merge gate so COMMENTED material findings block promotion
  -> MEASURE whether post-merge correctness hotfixes/review escapes decline
  -> EVOLVE only after evidence proves the stronger gate is better
```

## Relationship to existing orchestrator systems

The Evolution Engine is an outer learning/promotion state machine. It does not replace existing authorities.

It reuses:

- `repair.py` for repository-authorized deterministic repair planning;
- `learning.py` / `engineering/learning-memory.json` for verified sanitized engineering memory;
- `self_improvement.py` for evidence-backed recurring-friction proposals;
- throughput and timeseries metrics for measured before/after outcomes;
- per-repository control-plane policy for allowed commands and boundaries;
- normal preflight, PR CI, review, mergeability, security, provenance, privacy, licensing, and Product Reality gates.

The implementation should increasingly connect these existing pieces rather than duplicate them.

## CLI

Build an Evolution plan from a JSON input:

```text
project-orchestrator-evolve plan --input evolution-input.json
```

Evaluate normalized current-head review evidence:

```text
project-orchestrator-evolve review-gate --input review-input.json
```

Both commands emit machine-readable JSON.

## Hourly DoWork use

Every run should continue normal triage first. The Evolution Engine does not manufacture work ahead of P0-P4 obligations.

When a meaningful repair, recurring failure, successful pattern, unattended-worker result, or orchestration defect is verified:

1. retrieve relevant learning memory;
2. classify the current Evolution stage;
3. execute only already-authorized bounded engineering work;
4. record verified learning;
5. identify the next prevention/improvement opportunity;
6. keep structural redesign proposal-only unless already authorized;
7. collect comparable outcome evidence;
8. promote to reusable capability only after the Evolution gate is actually satisfied.

## Human boundary

Jeremy should disappear from deterministic routine verification, log transport, repeatable local tests, and ordinary bounded engineering decisions.

Jeremy remains the decision authority for:

- fundamental product direction;
- materially different product outcomes;
- unauthorized major architecture changes;
- new paid services or meaningful recurring cost;
- licensing changes;
- destructive migrations;
- security/privacy boundary changes;
- credential/secret actions requiring the owner;
- release/publishing authorization;
- irreversible actions;
- subjective Product Reality such as fun, feel, art direction, musical preference, or equivalent human judgment.

The engine's purpose is to make those human interventions increasingly high-value rather than frequent.

## North star

The desired engineering behavior is the same principle behind Fix_It:

> Something failed. Fix it. Understand why. Remember the fix. Make the same failure harder to repeat. Then ask whether the repair itself can become better.

That repeated, evidence-governed loop is the Evolution Engine.

Related: #49, #51, #52.
