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
- [ ] Automatic portfolio evidence collector — next
- [ ] Scheduler-facing run report artifact
- [ ] Product Reality debt aggregation without payload leakage

## v1 completion criterion

The orchestrator can consume live evidence for all five repositories, deterministically select the next action under DoWork, preserve ownership and dependency boundaries, produce machine-readable evidence, and prove its own changes through preflight + fresh PR CI.

The n8n layer is considered **repository-integrated** when its versioned workflows and policy validators pass preflight, and **operationally activated** once the user-approved HTTPS ingress, least-privilege runtime credential, security audit, and a real webhook route have been verified. Both are now true: PR #12 verified, from inside the running container and against live GitHub webhook deliveries, that outbound GitHub API TLS validates normally, all five product repositories have a registered `check_suite` webhook returning HTTP 200, each GitHub Trigger node's webhook secret persists, that persistence survives a full container restart, and the corrected `ciFailureRouter` workflow is active.

The Reinvestment Controller (`project_orchestrator.reinvestment`) is a recommendation layer only: it scores deterministic candidate investments from `timeseries.build_reinvestment_snapshot` and always requires Jeremy's approval before anything is acted on. It does not yet have enough recorded evidence windows (2, as of this gate) to produce a confident (`>= 0.5`) recommendation, and correctly reports that as the current state rather than picking a low-confidence action. Its next dependency is the Automatic Portfolio Evidence Collector, which must exist before the controller's recommendations can be trusted at meaningful confidence.

## After v1

Freeze governance-layer expansion unless measured evidence identifies a bottleneck. Direct most engineering capacity into product roadmap work and use the orchestrator to measure, learn, and improve the loop.
