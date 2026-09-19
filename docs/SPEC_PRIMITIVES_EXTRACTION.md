# Native Spec Primitives — OpenSpec / Spec Kit Extraction

Daedalus adopts the useful invariants, not a second control plane.

Extracted concepts:
- explicit goal/spec/design/task/evidence/result artifacts;
- dependency-gated task readiness;
- acceptance criteria required before task execution;
- evidence required before a result can be considered complete;
- one product-level graph may span multiple repositories;
- impact analysis exposes every repository touched by a coordinated change.

Existing Daedalus machinery remains authoritative for scheduling, leases, execution, preflight, CI, Product Reality, learning, and promotion.

## Knowledge Engine application
A single Knowledge Engine spec can fan out into Core, Web, and AI tasks. Tasks become executable only when declared dependencies are complete. Completion still requires repo-native verification and Daedalus promotion gates.

## External-source disposition
OpenSpec: ADAPT — cross-repository artifact/dependency model is useful; no runtime dependency required.
Spec Kit: ADAPT — specification/plan/task and repair discipline is useful; existing Daedalus verification remains authoritative.
