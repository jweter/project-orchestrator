# Autonomous Engineering Agent Evolution Plan

The portfolio orchestrator is the durable operating substrate for the AI engineering agent across Jeremy's repositories. The goal is not merely higher commit volume. The system must become better at selecting, executing, verifying, and learning from engineering work over time.

## Closed-loop model

**SENSE -> REMEMBER -> DECIDE -> ACT -> VERIFY -> LEARN -> ADAPT**

- **Sense:** live GitHub state, issues, PRs, CI logs, branches, roadmaps, architecture, policies, Product Reality debt, and evidence-backed experiment opportunities.
- **Remember:** verified failure memory, successful engineering patterns, experiment ancestry, compatibility lessons, area-specific prevention rules, and sanitized evidence.
- **Decide:** P0-P6 triage, dependency-aware decomposition, ownership checks, blocker rotation, throughput optimization, context-before-action, hypothesis selection, bounded experiment design, and resource-budget gating.
- **Act:** implement, repair, modernize branches, or execute bounded experiments only when the Agent Action Guard returns `READY`, the Portfolio Resource Scheduler returns `SCHEDULED`, and all normal authority permits execution.
- **Verify:** exact-head preflight, fresh independent PR CI, reviews/comments, mergeability, policy checks, human Product Reality where required, and independent experiment verification.
- **Learn:** capture root cause, bounded fix, experiment outcome, rejected hypotheses, regression protection, residual risk, prevention rule, and measured successful patterns.
- **Adapt:** convert repeated verified evidence into replacement/upgrade/redesign/evolution proposals; distill cross-repo patterns only after independent domain-neutral evidence earns promotion.

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
- Product Reality incorrectly freezing independent engineering;
- anonymous retries versus lineage-bound experiments;
- duplicate experiments prevented;
- candidate hypotheses rejected by evidence before promotion;
- resource contention prevented;
- cross-repo patterns promoted only after independent evidence.

Target: **Preventable Repeat Failure Rate = 0**.

A lesson is effective only when it demonstrably prevents, detects earlier, or shortens a future failure class. If the same diagnosed failure repeatedly consumes runs, the prevention mechanism is incomplete.

### 6. Success memory and learning effectiveness — COMPLETE

The agent must learn from verified successes as well as failures. Successful engineering patterns may be persisted as `success_pattern` lessons only when supported by exact evidence. When a prior lesson is later applied, record a verified learning application and classify the observed outcome as prevention, earlier detection, shortened repair, successful-pattern reuse, no measurable effect, or UNKNOWN. Learning effectiveness is measured from these verified applications; retrieval or intention alone does not count as learning success.

### 7. Evidence-based pre-change risk predictor — COMPLETE

Before mutating a known component, the agent must ask which verified failure classes and successful engineering patterns have historically matched the repository, changed paths, and change summary. The predictor uses the same deterministic failure taxonomy as the repair engine, but its semantics are historical evidence only: it does not invent probability, severity, readiness, ROI, or time-saved estimates. `NO_RELEVANT_VERIFIED_HISTORY` means exactly that; it must never be relabeled as low risk.

The output is a pre-edit checklist of applicable prevention/reuse rules plus evidence references. These signals may change implementation and verification planning before CI fails, but they never override product-repository roadmap, architecture, security, licensing/provenance, privacy, CI, or Product Reality authority.

### 8. Context-first Agent Action Guard — COMPLETE

Before autonomous implementation, repair retry, or experiment-like work, the agent builds a context package proving exact repository head, bounded scope, authority, context lookup, learning lookup, evidence references, exact error for retries/failure-driven work, and an explicit compute/network/write/concurrency/paid-cost envelope.

The guard returns `READY`, `GATHER_CONTEXT`, `APPROVAL_REQUIRED`, or `FORBIDDEN`. Unknown paid cost requires approval; explicit hard-cap violations are forbidden. Reserved-human, destructive, and irreversible boundaries still require approval.

The guard is planning authority only. It emits no executable commands and cannot bypass CI, review, security, provenance/licensing, privacy, Product Reality, or repository architecture authority. See `docs/AGENT_ACTION_GUARD.md` and #57.

### 9. Autonomous Engineering Scientist — IMPLEMENTED, PENDING PROMOTION

The next layer treats autonomous engineering as a controlled experimental discipline:

**Observe -> formulate hypothesis -> retrieve prior evidence -> design bounded experiment -> estimate cost -> execute -> measure -> compare -> reject or retain -> independently verify -> record -> generalize -> repeat.**

This capability has four coordinated mechanisms:

1. **Experiment Lineage + Bounded Hypothesis Competition** — every attempt has exact baseline/candidate heads, ancestry, hypothesis, measurement contract, resource estimate, result, and independent verification. Competing fixes are comparable only under the same baseline and measurement contract; no clear winner is invented within tolerance.
2. **Portfolio Resource Scheduler** — schedules only `READY` actions under explicit compute/network/write/concurrency/cost/exclusive-resource capacity. P0-P6 remains authoritative and fairness applies only within equal priority.
3. **Opportunity Sweeper** — converts already-evidenced gaps into reversible, bounded, deterministically measurable experiment proposals. It is downstream of triage/reinvestment/roadmaps and never invents ROI, probability, severity, percent complete, or product need.
4. **Cross-Repo Pattern Distillation** — promotes only repeatedly verified domain-independent engineering mechanisms across independent repositories/projects. Product-specific requirements never transfer across projects and fuzzy semantic generalization is forbidden.

The scientist loop does not execute commands by itself. Candidate execution remains under existing governed lanes and independent verification remains mandatory. See `docs/AUTONOMOUS_ENGINEERING_SCIENTIST.md`, `engineering/autonomous-engineering-scientist.json`, and #59.

## Operational phase

Product work is the proving ground. Exercise the full loop on real Knowledge Engine, Rocksmith, and Everward work. Use experiment lineage when more than one deterministic repair hypothesis exists; schedule bounded competition only when resources allow; convert evidence-backed neglected gaps into cheap experiments; and distill mechanisms across projects only after verified independent outcomes.

New orchestrator infrastructure is justified only by measured recurring friction, a concrete execution blocker, a failed control invariant, a secondary effect created by increased autonomy, or verified experiment evidence showing that the current mechanism should be replaced, upgraded, redesigned, or evolved.
