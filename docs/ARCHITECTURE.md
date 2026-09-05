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

## Required-check resolution

`project_orchestrator.required_checks` enriches raw PR evidence with the CI policy that applies to the PR base branch.

Authority is layered deliberately:

1. effective GitHub branch rules and legacy branch-protection required contexts are live authority;
2. current control-plane `verification.mandatory_check_names` are policy evidence;
3. raw check runs prove which named checks actually exist on the exact PR head.

A check required by live branch policy is synthesized as `status: missing` when absent, preventing merge. A manifest-only name that is absent from the current head is **not** treated as permanently required; it produces `UNCERTAIN` because the workflow may have been renamed, retired, or made non-applicable. Required-workflow rules that cannot yet be mapped to concrete check contexts also fail closed as `UNCERTAIN`.

The decision kernel accepts `CONFIRMED_NONE` only when live branch rules were successfully read and the current control plane explicitly declares an empty mandatory-check list. This allows repositories with truly no required CI to proceed without pretending that missing policy evidence is equivalent to no policy.

## Knowledge Engine compatibility planning

`engineering/knowledge-engine-compatibility.json` records only cross-repository boundaries already established by the product repositories' governing documents. It does not invent a new product architecture.

The deterministic planner models three supported dependency paths:

- Core machine-readable `ke` CLI contracts are consumed directly by AI through `knowledge_engine_ai.ke_client`; Web is a downstream consumer of AI.
- Core persisted database schema is consumed read-only by Web through SQLAlchemy reflection.
- AI typed Python contracts are consumed by Web through its pinned `knowledge-engine-ai` dependency.

For a proposed shared-contract change, `project-orchestrator compat-plan` identifies direct and transitive consumers and emits dependency-safe preparation and verification actions. Breaking changes require consumers to be prepared before producer merge and recommend temporary old/new compatibility when practical. Unknown contract kinds fail closed.

The planner also records forbidden boundary shortcuts already documented by the family, including direct Web imports of Core and raw Web subprocess calls to Core that bypass AI's supported `ke_client` boundary. Repository governing documents remain higher authority than this machine-readable projection.
