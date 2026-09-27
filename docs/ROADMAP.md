# Roadmap

## v1 â€” Portfolio brain

- [x] Canonical three-project / five-repository inventory
- [x] DoWork operating contract
- [x] Machine-readable control plane
- [x] Deterministic P0-P6 decision kernel
- [x] Fail-closed PR classification
- [x] One-new-slice-per-project fairness guard
- [x] Exact-head preflight
- [x] Branch/PR CI workflows
- [x] Live GitHub evidence collector
- [x] Required-check discovery adapter
- [x] Cross-repository compatibility planner for Knowledge Engine
- [x] Persistent sanitized regression/learning memory
- [x] Version-controlled n8n event-router/dispatch layer with fail-closed policy
- [x] n8n layer operationally activated (verified HTTPS ingress, credentials, and a real webhook route â€” see below)
- [x] Portfolio compounding time-series store
- [x] Reinvestment controller consuming measured evidence (recommendation-only; see below)
- [x] Automatic portfolio evidence collector correctness remediation
- [x] Anti-stall blocker rotation + throughput floor
- [x] Deterministic repair engine
- [x] Parallel specialist execution lanes
- [x] Predictive work decomposition
- [x] Automatic regression prevention from repair memory
- [x] Portfolio throughput optimizer using measured evidence
- [x] Scheduler-facing executive run report artifact
- [x] Portfolio KPI scorecard + per-project/per-repository progress and bottleneck tracking
- [x] Durable append-only KPI transition telemetry + per-run snapshots + 24h/7d/30d flow reports
- [x] Deterministic autonomous-loop structural audit adapted from ECC loop-design concepts
- [x] ECC selective-adoption gap analysis with product-specific guardrails
- [x] Read-only harness inventory/audit + repository context-budget observability
- [x] Deterministic agent-evaluation + AI-regression measurement harness using the canonical KPI ledger
- [x] Disposable exact-baseline agent-evaluation matrix runner for like-for-like local agent/config experiments
- [x] Verified repository-history skill extraction + explicit task/path-triggered advisory selector
- [x] Autonomous branch reconciliation and modernization
- [x] Exact-head executable preflight coverage across every product repository
- [x] Area-specific learning retrieval before edits
- [x] Evidence-based pre-change risk predictor
- [x] Branch lifecycle manager with stale-value recovery and safe merged-branch cleanup
- [x] Measured self-improvement proposals from recurring orchestration friction
- [x] Success-pattern memory + verified learning-effectiveness measurement
- [ ] Re-collect corrected current portfolio evidence â€” operational measurement task
- [ ] Exercise the full learning loop on product work and measure outcomes â€” primary operational gate
  - [x] Bind claimed advancement to concrete product execution + verification evidence
  - [ ] Execute the contract against live Knowledge Engine, Rocksmith, and Everward work
- [x] Product Reality debt aggregation without payload leakage

## v1 completion criterion

The orchestrator can consume live evidence for all six repositories, deterministically select the next action under DoWork, preserve ownership and dependency boundaries, produce machine-readable evidence, and prove its own changes through preflight + fresh PR CI.

The n8n layer is considered **repository-integrated** when its versioned workflows and policy validators pass preflight, and **operationally activated** once the user-approved HTTPS ingress, least-privilege runtime credential, security audit, and a real webhook route have been verified. Both are now true for **both** canonical workflows: PR #12 verified `ciFailureRouter` (outbound GitHub API TLS validates normally, all five product repositories have a registered `check_suite` webhook returning HTTP 200, each GitHub Trigger node's webhook secret persists and survives a full container restart). A later fix verified `keImpactRouter` the same way, plus a real (non-ping) `push` to `main` on `knowledge-engine-core` was observed flowing through the classifier and dispatch node into a successful `orchestrator-observe.yml` run â€” the first fully end-to-end verification of either router against a real event rather than a ping.

The Reinvestment Controller (`project_orchestrator.reinvestment`) is a recommendation layer only: it scores deterministic candidates from `timeseries.build_reinvestment_snapshot` and always sets requires_jeremy_approval=true. The first Automatic Portfolio Evidence Collector backfill exposed correctness defects after merge: exact-head CI timing was not used consistently, pending checks could be treated as green, manually seeded PR evidence could be double-counted, and a dependency-recheck routing candidate was incorrectly equated with an actual cross-repository incident. Those automatically derived windows have been invalidated while the manually verified seed windows remain. No reinvestment recommendation from the invalid backfill is authoritative. Corrected collection passed exact-head preflight and fresh PR CI in PR #17 and merged. Before trend or reinvestment claims resume, corrected history must be re-collected. The scheduler additionally enforces anti-stall blocker rotation and a throughput floor so one blocked lane cannot consume repeated hourly runs while independent executable product work exists.

The scheduler executive report now carries a KPI scorecard defined in `docs/KPI_SCORECARD.md`. It reuses the canonical compounding time-series, explicit rework evidence, and measured throughput optimizer; it shows portfolio, project, and repository delivery metrics, evidence gaps, and advisory bottleneck ranking while keeping `UNKNOWN`/`INSUFFICIENT_DATA` explicit. It never invents percent complete, ROI, capacity, or Product Reality.

Durable flow telemetry is defined in `docs/KPI_TELEMETRY.md`. PR, CI, branch, and issue transitions can be recorded once into the append-only `engineering/kpi-events.jsonl` ledger and projected into append-only per-run snapshots plus 24-hour, 7-day, and 30-day flow reports. The telemetry layer preserves existing KPI definitions/targets as authority, excludes control-plane activity from product-flow health, and leaves incomplete evidence `UNKNOWN` instead of reconstructing or guessing missing history.

Selective ECC adoption is governed by `docs/ECC_GAP_ANALYSIS.md`. The first adopted technique is a deterministic structural audit of the DoWork autonomous regulator: machine-decidable acceptance, independent verification, bounded repair, anti-Goodhart invariants, fresh fail-closed evidence, explicit automated-vs-human authority, and safe no-op semantics are now versioned and preflight-gated. Phase B adds `engineering/harness-audit.json` plus `project_orchestrator.harness_audit`: a read-only inventory/scanner that measures repository-resident instruction/skill/agent/MCP/hook surfaces, reports context bytes and approximate tokens, detects duplicate context and unsafe MCP literals, and leaves unmounted repositories explicitly UNVERIFIED. Phase C adds `engineering/agent-eval-suite.json` plus `project_orchestrator.agent_eval`: pinned fault-injection tasks, deterministic acceptance, first-pass/repair/intervention/elapsed-time reporting, and like-for-like agent configuration comparisons. Evaluation outcomes are written into the existing KPI ledger rather than a competing store, and Product Reality cannot be scored by this harness. The operational runner in `project_orchestrator.agent_eval_runner` now creates detached exact-baseline worktrees, proves the injected fault makes the deterministic contract RED before agent execution, runs direct argv-based local agent configurations, re-runs repository-native verification, records Phase C attempts, and removes the worktree. Comparative claims remain blocked until real matrix runs supply evidence. Phase D adds `engineering/repository-skills.json` plus `project_orchestrator.skill_extraction`: only VERIFIED learning-memory lessons and exact git-history commit refs may back a repository skill; selection requires an explicit path-pattern or literal task-trigger match; every skill remains advisory and starts with effectiveness `UNMEASURED`. The first bounded pilot preserves the verified risk-similarity false-positive prevention rule in project-orchestrator. Product-repository skills remain blocked until verified product history/lessons support them.

## Autonomous completion mandate

- [x] Five-repository production Laptop Actor routing and exact-head execution proof
- [x] Portfolio readiness gate: missing/failed/stale repo evidence fails closed
- [x] Scheduler -> local pending -> production runner acquisition path
- [x] Persistent self-driving control-loop primitive with durable P1 failure state
- [ ] Supervise the self-driving loop as a durable Azathoth process with startup/restart and crash recovery
- [ ] Replace global stall accounting with per-product two-cycle liveness enforcement
- [ ] Add bounded persistent history/state compaction
- [ ] Wire live production lane and result providers into the persistent loop
- [ ] Prove unattended product advancement -> verification -> feedback -> next advancement on all four products
- [ ] Drive Knowledge Engine, Rocksmith, and Everward roadmaps continuously until their authoritative completion criteria are met

The full durable execution contract is docs/AUTONOMOUS_COMPLETION_PATH.md. Zero open PRs is explicitly a build trigger. Product work outranks governance expansion except when a P0/P1 control-plane defect prevents safe product execution. The existing execution schedule/processes continue this plan; do not create duplicate schedules.


## Project Hands-Off Evolution â€” portfolio north star

**Goal:** move Jeremy out of the necessary routine engineering loop. Establish product intent, guiding documents, roadmaps, acceptance criteria, and guardrails first; then enable the Evolution Engine to execute ordinary roadmap development continuously. Jeremy observes repository progress and realigns product authority when needed rather than manually driving each slice.

Authoritative design: [HANDS_OFF_EVOLUTION.md](HANDS_OFF_EVOLUTION.md).

- [ ] Phase 1 â€” deterministic authority/readiness audit for every product
- [ ] Phase 2 â€” explicit reversible per-product Evolution activation
- [ ] Phase 3 â€” durable supervised Evolution runtime with per-product liveness
- [ ] Phase 4 â€” automatic merge/verification -> next-work continuation without a manual prompt
- [ ] Phase 5 â€” closed-loop deterministic repair, regression prevention, and capability reuse
- [ ] Phase 6 â€” repeated unattended roadmap advancement proof across Knowledge Engine, Rocksmith, and Everward
- [ ] Phase 7 â€” retire remaining routine Jeremy touches; preserve only reserved decisions and genuine Product Reality

**Completion test:** Jeremy can set/realign authoritative direction, leave the routine loop, and later inspect evidence of continuous verified product advancement. A recurring need to say "continue" is P1 liveness debt, not normal operation.

## After v1

Freeze governance-layer expansion unless measured evidence identifies a bottleneck. Direct most engineering capacity into product roadmap work and use the orchestrator to measure, learn, and improve the loop.

## Daedalus Engineering Gym â€” deliberate-practice path

The Gym is an explicit post-v1 capability path. It uses otherwise-idle Laptop Actor capacity and disposable exact-baseline worktrees; it never displaces higher-priority eligible product work.

### Phase 0 â€” prerequisite trust
- [ ] Bind canonical verification to the requested exact repository/worktree/head and fail closed on stale-source execution.
- [ ] Complete Evolution capability compilation from measured EVOLUTION_VERIFIED outcomes into scheduler-consumable retained capabilities.
- [ ] Prove degrading capabilities can be demoted or quarantined.

### Phase 1 â€” real-history replay
- [ ] Mine verified failures, repairs, CI failures, review findings, and regression fixes from all five product repositories.
- [ ] Build sanitized challenge episodes with pre-fix state, acceptance contract, authoritative verification, and hidden verified outcome.
- [ ] Replay challenges in disposable worktrees without exposing the historical fix.
- [ ] Measure first-pass success, repair iterations, elapsed cycle time, unnecessary churn, regression escapes, and Jeremy intervention.

### Phase 2 â€” mutation gym
- [ ] Generate bounded reversible defects only from repository-authorized failure classes.
- [ ] Prove each mutation RED before Actor execution and canonical verification GREEN afterward.
- [ ] Exercise environment, dependency, typing, boundary, state, provenance, deterministic-validation, worker-recovery, and regression classes where policy permits.
- [ ] Never use restricted/private Rocksmith content or fabricate Product Reality.

### Phase 3 â€” adversarial engineering tournament
- [ ] Run multiple isolated strategies against the same exact-baseline challenge.
- [ ] Add adversarial review for counterexamples, hidden regressions, security/privacy/provenance violations, architecture drift, and unnecessary complexity.
- [ ] Compare strategies only with deterministic measured evidence; code volume is never a success metric.
- [ ] Retain candidates only when they outperform baseline without weakening gates.

### Phase 4 â€” skill/capability compilation
- [ ] Extract reusable procedures from repeatedly verified winning episodes.
- [ ] Keep repository-specific product knowledge separate from portfolio-safe engineering-process capabilities.
- [ ] Compile only measured gate-cleared capabilities into retained scheduler advice and inject applicable capabilities before planning.
- [ ] Automatically demote or quarantine capabilities that worsen cycle time, repeat failures, regressions, or human intervention.

### Phase 5 â€” idle deliberate practice
- [ ] Let the persistent Laptop Actor consume Gym work only when no higher-priority eligible production lane is waiting.
- [ ] Enforce CPU/memory/disk budgets and avoid overlapping memory-heavy workloads.
- [ ] Feed sanitized verified novel production failures back into future curriculum.
- [ ] Report which trained capabilities successfully transferred to real product work.

### Phase 6 â€” self-improving engineering loop
- [ ] Measure real effects on Product Advancement Rate, failure-to-repair time, merge cycle time, repeat-failure rate, and Jeremy-required intervention.
- [ ] Select future curriculum from measured production bottlenecks rather than generic coding puzzles.
- [ ] Graduate portfolio capabilities only after cross-context evidence proves transfer safe and beneficial.

### Gym north star

Each verified production or practice episode gets a governed path to make future engineering cheaper: recognize -> retrieve -> prevent/repair -> verify -> measure -> retain/demote. Idle compute becomes deliberate practice; deliberate practice creates candidate skills; production evidence decides whether those skills survive.
