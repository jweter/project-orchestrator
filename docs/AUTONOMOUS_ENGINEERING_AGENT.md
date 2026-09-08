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

### 1. Autonomous Branch Reconciliation + Modernization — ACTIVE

Recover useful engineering trapped on stale branches without rewriting published history or creating competing PRs.

The deterministic planner must classify branch state from live compare evidence:

- aligned/no unique work;
- stale/no unique work;
- current/unmerged;
- diverged/valuable.

Before modernization it must prove current roadmap authorization, open-PR/ownership safety, Product Reality independence, and changed-file scope. A diverged valuable branch is replayed onto a fresh branch from the exact current default head. It then follows targeted verification -> full preflight -> PR -> fresh CI.

### 2. Exact-head executable preflight everywhere

Provide a dependable local or branch-triggered execution path for every repository so an agent can prove PREFLIGHT_GREEN without relying on unverifiable assumptions. Preflight evidence must bind to the exact branch head SHA.

### 3. Area-specific learning retrieval before edits

Before modifying a component, retrieve verified lessons relevant to the repository, paths, contract type, and failure class. Surface prevention rules before implementation starts. Failure memory and success memory remain evidence-backed and sanitized.

### 4. Branch lifecycle manager

Classify every non-default branch as active, PR-owned, merged-disposable, stale-but-valuable, superseded, release/hotfix-required, or uncertain. Automatically clean only branches whose safe disposal is proven. Queue stale valuable branches for modernization rather than letting engineering capital decay.

### 5. Measured self-improvement proposals

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
