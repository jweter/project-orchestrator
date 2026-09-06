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
- [ ] Predictive work decomposition — current gate
- [ ] Corrected evidence re-collection and scheduler-facing run report artifact — next
- [ ] Product Reality debt aggregation without payload leakage

## v1 completion criterion

The orchestrator can consume live evidence for all five repositories, deterministically select the next action under DoWork, preserve ownership and dependency boundaries, produce machine-readable evidence, and prove its own changes through preflight + fresh PR CI.

The n8n layer is considered **repository-integrated** when its versioned workflows and policy validators pass preflight, and **operationally activated** once the user-approved HTTPS ingress, least-privilege runtime credential, security audit, and a real webhook route have been verified. Both are now true for **both** canonical workflows: PR #12 verified `ciFailureRouter` (outbound GitHub API TLS validates normally, all five product repositories have a registered `check_suite` webhook returning HTTP 200, each GitHub Trigger node's webhook secret persists and survives a full container restart). A later fix verified `keImpactRouter` the same way, plus a real (non-ping) `push` to `main` on `knowledge-engine-core` was observed flowing through the classifier and dispatch node into a successful `orchestrator-observe.yml` run — the first fully end-to-end verification of either router against a real event rather than a ping.

The Reinvestment Controller (`project_orchestrator.reinvestment`) is a recommendation layer only: it scores deterministic candidate investments from `timeseries.build_reinvestment_snapshot` and always requires Jeremy's approval before anything is acted on. The first Automatic Portfolio Evidence Collector backfill exposed correctness defects after merge: exact-head CI timing was not used consistently, pending checks could be treated as green, manually seeded PR evidence could be double-counted, and a dependency-recheck routing candidate was incorrectly equated with an actual cross-repository incident. Those automatically derived windows have been invalidated while the manually verified seed windows remain. No reinvestment recommendation from the invalid backfill is authoritative. Corrected collection passed exact-head preflight and fresh PR CI in PR #17 and merged. Before trend or reinvestment claims resume, corrected history must be re-collected. The scheduler additionally enforces anti-stall blocker rotation and a throughput floor so one blocked lane cannot consume repeated hourly runs while independent executable product work exists.

## After v1

Freeze governance-layer expansion unless measured evidence identifies a bottleneck. Direct most engineering capacity into product roadmap work and use the orchestrator to measure, learn, and improve the loop.
