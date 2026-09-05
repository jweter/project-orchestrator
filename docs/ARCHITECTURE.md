# Architecture

## Purpose

The project orchestrator is a portfolio control plane, not a sixth product implementation repository.

Each product repository owns its own product truth: roadmap, architecture, code, tests, CI, Product Reality, licensing/provenance boundaries, and merge requirements. The orchestrator owns only portfolio-level coordination.

## Authority graph

```text
DoWork scheduler
    |
    v
project-orchestrator
    |
    +-- Knowledge Engine family
    |    +-- core
    |    +-- web
    |    +-- ai
    |
    +-- Rocksmith
    |
    +-- Everward
```

Knowledge Engine is one coordinated dependency family. Rocksmith and Everward are isolated standalone projects.

## Deterministic decision kernel

`project_orchestrator.decision` takes an evidence snapshot and emits the highest-priority authorized actions. It does not fetch GitHub itself and therefore cannot silently invent live state. Collection and mutation remain explicit orchestration responsibilities.

Priority order: P0 critical security/data loss/correctness; P1 repair failing PRs; P2 merge objectively GREEN PRs; P3 resolve conflicts/reviews/uncertainty; P4 finish/wait existing work; P5 authorized roadmap slices; P6 nonblocking debt.

## Fail-closed semantics

Missing required-check evidence is UNCERTAIN. Pending checks are PENDING. Human Product Reality evidence is never inferred. Readiness percentages are prohibited.

## Exponential-development hypothesis

The repository does not assert exponential development. It supplies the control structure needed to measure whether engineering capacity compounds over time. Claims require sustained evidence across comparable windows: rising verified throughput and autonomous completion, falling cycle time/repeat failures/human intervention, and increasing dependency unlocks without quality regression.

## Security

No repository secrets, credentials, private Product Reality payloads, copyrighted private scores/audio, or private generated workspaces belong here. Only redacted/sanitized evidence and repository identifiers may be persisted.

## Live evidence collection

`project_orchestrator.github_evidence` is the I/O boundary for GitHub observation. It collects repository metadata, default-head SHA, open PR details, raw check runs, reviews/comments, recent issues, branches, recent commits, and a sanitized projection of each repository control-plane manifest.

The collector deliberately does **not** decide which check runs are required. Every collected PR therefore carries `required_checks_state: UNKNOWN` until the separate required-check discovery adapter maps current rulesets/branch protection/workflow policy onto those raw checks. This prevents a successful optional workflow from being mistaken for merge authority.

## Review and pagination safety

Collection is exhaustive for list endpoints: pagination continues until a short page is observed and fails closed if the configured safety bound is exceeded. Pull-request Conversation comments and inline review comments are both retained.

Active `CHANGES_REQUESTED` states are normalized per reviewer. Comment semantics remain conservative: when comments exist but their blocking status is not mechanically resolved, `blocking_comments_state` is `UNKNOWN`, which prevents the decision kernel from emitting a GREEN merge action.
