# Issue #710 — independent repair handoff qualification

This module is a **read-only gate**, not a new backlog writer or supervisor.
Its purpose is to prevent a local Ollama advisory, a copied JSON intake or
a change of status from granting itself another coding attempt after an
exhausted parent.

`iron_god_microtask_repair_handoff.qualification()` requires ALL of:

1. A review-only intake record derived from the original exhausted lineage,
   with advisory flags intact. It does not inherit dispatch authority.
2. An *independently* approved, NEW roadmap capability/slice. The request
   identity and roadmap path must match the existing project-orchestrator
   canonical identity and owner-approved graph; a trusted verifier must
   confirm that the graph actually contains this newly reviewed slice.
3. An exact repository SHA and the original repair scope: only the original
   child's module/test paths and original child-owned acceptance criteria.
   Dependent work and previously merged siblings are not reclassified.
4. Confirmation from the **canonical lane state and checkpoint** that the
   original attempt lineage remains exhausted. Its consumed attempts are never
   reset or renamed to manufacture a retry.
5. A new draft satisfying **the existing**
   `iron_god_roadmap_refill.draft_problems()` admission contract: correct
   level, locality, owner route, baseline, scope, reservations, duplicates.
6. For an existing-module Level 2 repair, a real independent red test at the
   exact current SHA. A verified native test failure must be an assertion,
   not an environment error, timeout or missing test dependency.

If anything is unavailable or unverified the function returns a specific
reason and **never writes/dispatches**. A success result is still
`dispatchable=false` and names
`iron_god_roadmap_refill.process_drafts` as the **separate** existing
admission path. No worker is started by this module; no new capability graph
is fabricated; owner-controlled execution and release gates are not changed.

This development slice has no runtime hook. It cannot itself meet the three
independent verified micro-edits, post-second-edit interruption/resume,
multi-repository fairness, zero-Claude token accounting or native product
acceptance requirements of #710.

## Laptop restart signal for owner and Claude

**DO NOT advise switching Iron God back on merely because these PRs merge.**
First confirm all #710 changes have passed current-head GitHub CI and review,
then the original authoritative pinned release and exact installed SHA are
verified, owner Take the Wheel is explicitly acknowledged, and supervisor/
worker leases, RAM floors, Ollama availability and clone heads are healthy.
An authorized dry-run must demonstrate correct local model admission, targeted
test receipts and checkpoints without invoking Claude. The three-micro-edit,
interrupted-resume and full product acceptance canaries must pass before
calling the *new #710 behavior* deployment-ready.

The distinction matters: merging source code, synchronizing Claude's laptop
checkout and starting an owner-authorized supervisor are different events.
Always communicate current factual readiness to both the owner and Claude;
the MCP mailbox is advisory, not a control channel. Never auto-start a paused
engine or re-enable cloud-token dispatch to satisfy a readiness check.
