# Autonomous Engineering Agent Evolution Plan

The portfolio orchestrator is the durable operating substrate for the AI engineering agent across Jeremy's repositories. The goal is not merely higher commit volume. The system must become better at selecting, executing, verifying, and learning from engineering work over time.

## Closed-loop model

**SENSE -> REMEMBER -> DECIDE -> ACT -> VERIFY -> LEARN -> ADAPT**

- **Sense:** live GitHub state, issues, PRs, CI logs, branches, roadmaps, architecture, policies, and Product Reality debt.
- **Remember:** verified failure memory, successful engineering patterns, compatibility lessons, area-specific prevention rules, and sanitized evidence.
- **Decide:** P0-P6 triage, dependency-aware decomposition, ownership checks, blocker rotation, and throughput optimization.
- **Act:** implement, repair, modernize branches, test, preflight, open PRs, and merge only verified GREEN work.
- **Verify:** exact-head preflight, fresh independent PR CI, reviews/comments, mergeability, policy checks, and human Product Reality where required.
- **Learn:** capture root cause, bounded fix, regression protection, residual risk, prevention rule, and measured successful patterns.
- **Adapt:** convert repeated friction into safe orchestrator improvements through the same governed promotion path.

## Build sequence

### 1. Autonomous Branch Reconciliation + Modernization — COMPLETE

Recover useful engineering trapped on stale branches without rewriting published history or creating competing PRs.

The deterministic planner must classify branch state from live compare evidence:

- aligned/no unique work;
- stale/no unique work;
- current/unmerged;
- diverged/valuable.

Before modernization it must prove current roadmap authorization, open-PR/ownership safety, Product Reality independence, and changed-file scope. A diverged valuable branch is replayed onto a fresh branch from the exact current default head. It then follows targeted verification -> full preflight -> PR -> fresh CI.

### 2. Exact-head executable preflight everywhere — COMPLETE

Provide a dependable local or branch-triggered execution path for every repository so an agent can prove PREFLIGHT_GREEN without relying on unverifiable assumptions. Preflight evidence must bind to the exact branch head SHA.

### 3. Area-specific learning retrieval before edits — COMPLETE

Before modifying a component, retrieve verified lessons relevant to the repository, paths, contract type, and failure class. Surface prevention rules before implementation starts. Failure memory and success memory remain evidence-backed and sanitized.

### 4. Branch lifecycle manager — COMPLETE

Classify every non-default branch as active, PR-owned, merged-disposable, stale-but-valuable, superseded, release/hotfix-required, or uncertain. Automatically clean only branches whose safe disposal is proven. Queue stale valuable branches for modernization rather than letting engineering capital decay.

### 5. Measured self-improvement proposals — COMPLETE

When evidence shows recurring orchestration friction, produce a bounded self-improvement proposal: observed bottleneck, evidence, proposed mechanism, safety boundaries, verification plan, and expected capability unlocks. Never invent ROI/probability/time saved. The orchestrator may implement authorized reversible improvements through branch -> preflight -> PR -> CI; it never weakens its own gates.

## Learning effectiveness

The system should measure whether prior learning changes later outcomes, not merely count lessons.

Track when evidence permits:

- preventable repeat failures;
- prior lessons reused before relevant changes;
- stale valuable branches recovered;
- PR repair iterations;
- preflight catches before PR;
- recurring CI failure classes;
- product projects advanced per eligible run;
- routine human intervention;
- Product Reality incorrectly freezing independent engineering.

Target: **Preventable Repeat Failure Rate = 0**.

A lesson is effective only when it demonstrably prevents, detects earlier, or shortens a future failure class. If the same diagnosed failure repeatedly consumes runs, the prevention mechanism is incomplete.


### 6. Success memory and learning effectiveness — COMPLETE

The agent must learn from verified successes as well as failures. Successful engineering patterns may be persisted as `success_pattern` lessons only when supported by exact evidence. When a prior lesson is later applied, record a verified learning application and classify the observed outcome as prevention, earlier detection, shortened repair, successful-pattern reuse, no measurable effect, or UNKNOWN. Learning effectiveness is measured from these verified applications; retrieval or intention alone does not count as learning success.

The next operational phase is to exercise the full closed loop on real product work, record which lessons were reused, and determine from evidence whether the system is actually reducing repeat failures and repair cycles.


### 7. Evidence-based pre-change risk predictor — COMPLETE

Before mutating a known component, the agent must ask which verified failure classes and successful engineering patterns have historically matched the repository, changed paths, and change summary. The predictor uses the same deterministic failure taxonomy as the repair engine, but its semantics are historical evidence only: it does not invent probability, severity, readiness, ROI, or time-saved estimates. `NO_RELEVANT_VERIFIED_HISTORY` means exactly that; it must never be relabeled as low risk.

The output is a pre-edit checklist of applicable prevention/reuse rules plus evidence references. These signals may change implementation and verification planning before CI fails, but they never override product-repository roadmap, architecture, security, licensing/provenance, privacy, CI, or Product Reality authority.


## Operational phase

The planned v1 agent capabilities are implemented and verified. The next engineering objective is not another speculative governance layer. The orchestrator must now exercise the complete loop on real Knowledge Engine, Rocksmith, and Everward work, apply pre-change risk and prior learning before mutation, and record verified outcomes afterward. New orchestrator infrastructure is justified only by measured recurring friction, a concrete execution blocker, or a failed control invariant discovered during product work.
