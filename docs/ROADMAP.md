# Roadmap

## v1 — Portfolio brain

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
- [x] n8n layer operationally activated (verified HTTPS ingress, credentials, and a real webhook route — see below)
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
- [x] Autonomous branch reconciliation and modernization
- [x] Exact-head executable preflight coverage across every product repository
- [x] Area-specific learning retrieval before edits
- [x] Evidence-based pre-change risk predictor
- [x] Branch lifecycle manager with stale-value recovery and safe merged-branch cleanup
- [x] Measured self-improvement proposals from recurring orchestration friction
- [x] Success-pattern memory + verified learning-effectiveness measurement
- [ ] Re-collect corrected current portfolio evidence — operational measurement task
- [ ] Exercise the full learning loop on product work and measure outcomes — primary operational gate
  - [x] Bind claimed advancement to concrete product execution + verification evidence
  - [ ] Execute the contract against live Knowledge Engine, Rocksmith, and Everward work
- [x] Product Reality debt aggregation without payload leakage

## v1 completion criterion

The orchestrator can consume live evidence for all five repositories, deterministically select the next action under DoWork, preserve ownership and dependency boundaries, produce machine-readable evidence, and prove its own changes through preflight + fresh PR CI.

The n8n layer is considered **repository-integrated** when its versioned workflows and policy validators pass preflight, and **operationally activated** once the user-approved HTTPS ingress, least-privilege runtime credential, security audit, and a real webhook route have been verified. Both are now true for **both** canonical workflows: PR #12 verified `ciFailureRouter` (outbound GitHub API TLS validates normally, all five product repositories have a registered `check_suite` webhook returning HTTP 200, each GitHub Trigger node's webhook secret persists and survives a full container restart). A later fix verified `keImpactRouter` the same way, plus a real (non-ping) `push` to `main` on `knowledge-engine-core` was observed flowing through the classifier and dispatch node into a successful `orchestrator-observe.yml` run — the first fully end-to-end verification of either router against a real event rather than a ping.

The Reinvestment Controller (`project_orchestrator.reinvestment`) is a recommendation layer only: it scores deterministic candidate investments from `timeseries.build_reinvestment_snapshot` and always requires Jeremy's approval before anything is acted on. The first Automatic Portfolio Evidence Collector backfill exposed correctness defects after merge: exact-head CI timing was not used consistently, pending checks could be treated as green, manually seeded PR evidence could be double-counted, and a dependency-recheck routing candidate was incorrectly equated with an actual cross-repository incident. Those automatically derived windows have been invalidated while the manually verified seed windows remain. No reinvestment recommendation from the invalid backfill is authoritative. Corrected collection passed exact-head preflight and fresh PR CI in PR #17 and merged. Before trend or reinvestment claims resume, corrected history must be re-collected. The scheduler additionally enforces anti-stall blocker rotation and a throughput floor so one blocked lane cannot consume repeated hourly runs while independent executable product work exists.

The scheduler executive report now carries a KPI scorecard defined in `docs/KPI_SCORECARD.md`. It reuses the canonical compounding time-series, explicit rework evidence, and measured throughput optimizer; it shows portfolio, project, and repository delivery metrics, evidence gaps, and advisory bottleneck ranking while keeping `UNKNOWN`/`INSUFFICIENT_DATA` explicit. It never invents percent complete, ROI, capacity, or Product Reality.

Durable flow telemetry is defined in `docs/KPI_TELEMETRY.md`. PR, CI, branch, and issue transitions can be recorded once into the append-only `engineering/kpi-events.jsonl` ledger and projected into append-only per-run snapshots plus 24-hour, 7-day, and 30-day flow reports. The telemetry layer preserves existing KPI definitions/targets as authority, excludes control-plane activity from product-flow health, and leaves incomplete evidence `UNKNOWN` instead of reconstructing or guessing missing history.

## After v1

Freeze governance-layer expansion unless measured evidence identifies a bottleneck. Direct most engineering capacity into product roadmap work and use the orchestrator to measure, learn, and improve the loop.
