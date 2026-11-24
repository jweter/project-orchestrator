# Owner-approved roadmap direction — 2026-10-25

Status: OWNER-APPROVED — standing development direction and implementation authorization, reaffirmed 2026-11-01.

This document records the owner's accepted product objectives and constitutes standing authorization to plan, implement, test, review, and deliver development work that advances those objectives during an owner-authorized Iron God session. The owner has already specified **what to achieve**. Iron God and its qualified builders are responsible for deciding **how to achieve it**. Ordinary roadmap-derived development does not require a second, per-slice owner approval, a manually maintained approved-item list, or a fresh permission request whenever the backlog is exhausted.

## Standing development authorization and autonomous continuation (owner decision 2026-11-01)

- **Scope comes from this roadmap.** Treat the outcomes, constraints, and acceptance criteria below as approved development direction. The "Next slices" are starting points, not an exhaustive ceiling or an approval queue. Continue deriving dependency-ready slices until the outcomes are demonstrably achieved, or a genuine blocker requires guidance.
- **Plan from evidence, not guesses.** Reconcile this document with the project's authoritative local roadmap, AGENTS.md, current source, GitHub issues and reviews, verified acceptance evidence, existing PRs and file ownership, and the shared engineering, working, and development memory layers. Do not treat GitHub main as newer than an authoritative local version merely because it is remote; preserve dirty worktrees and reconcile conflicting changes.
- **Reuse existing implementations.** Before proposing a new feature or helper, search code, preserved branches, verified history, and shared memory for reusable work. Decompose larger outcomes into bounded, testable slices with explicit dependencies, allowed paths, baseline identity, verification steps, and traceability to the roadmap. Avoid duplicate, disconnected, or repeatedly failed work.
- **Proceed without routine reapproval.** The autonomous planner may generate, prioritize, and execute roadmap-derived slices without separately asking the owner to approve each slice. Use an available, capability-appropriate builder; a local worker's measured Level 1–2 limits do not limit the owner's roadmap authorization or justify silently skipping complex work. If a qualified implementer or necessary test environment is unavailable, record the blocker and keep other eligible repositories progressing.
- **Ask only when actual direction is missing.** Investigate unresolved details first using project context and verified evidence. If materially conflicting objectives, missing requirements, or choices with significant consequences remain, ask one focused clarification instead of inventing owner intent. Preserve all unaffected authorized work in the meantime.
- **Preserve engineering and product gates.** Maintain exact-head verification, native tests, canonical preflight, required GitHub CI/reviews, branch protection, provenance, product-reality checks, file-ownership reservations, resource limits, and safe publication/merge rules. Failed gates require repair, retry within bounded policy, or a recorded blocker—not a bypass. Never count an unmerged PR or synthetic-only result as a completed product outcome.
- **Separate scope authorization from runtime control.** This roadmap is ongoing authorization for development objectives; it is not itself a Take the Wheel execution grant. Iron God must still obey the existing authenticated owner-controller session, Give Me Control revocation, release pinning, and worker/lease fences. Shared memory provides context and verified lessons, never execution authority.
- **Respect scope and stop conditions.** Do not silently expand into unrelated products, alter owner-control safeguards, expose secrets, spend money, destroy user data, or install/deploy an unapproved engine release. If an unavoidable action falls outside the roadmap's authorization, ask first. Record progress, failures, merged deliverables, and remaining roadmap gaps in shared memory so subsequent cycles continue from verified evidence rather than start over.

The product-specific objectives, owner decisions, and acceptance criteria that follow remain in force. This authorization clarification does not replace or weaken them.

## Iron God / universal portfolio

**2026-11-24 sequencing and simplification decision:** Finish IRON CONTINUUM
S2 and its acceptance criteria, then prioritize S3.1 eligibility consolidation
and productive recovery. The owner approved the ordered audit, tagged work
selection, bounded alternative slices, overhead reduction and evidence-based
code deletion policy in the [integrated delivery plan](IRON_GOD_INTEGRATED_DELIVERY_PLAN.md#owner-approved-direction--2026-11-24-simplify-before-expanding).
Use the existing backlog, scheduler, state owners and recovery mechanisms.
Do not add permanent services or duplicate authority paths. Existing approved
product development and required safety repairs continue. This decision does
not authorize runtime control changes, installation or bypass of any gate.


**2026-11-22 expansion:** The owner's integrated delivery objective, detailed gates and cost-control plan are recorded in [Iron God integrated delivery plan](IRON_GOD_INTEGRATED_DELIVERY_PLAN.md) and incorporated by [main roadmap](ROADMAP.md) and [product-first execution plan](PRODUCT_V1_DELIVERY_EXECUTION_PLAN.md). Reuse existing DoWork Hourly as a stronger code-and-tests builder for verified failed local slices, then have Azathoth independently test and publish. After a real scheduled canary, evaluate an independently authenticated Claude builder. Give each supported actor scoped two-way SQLite/MCP advisory collaboration while preserving separate owner/worker/merge authority. Pursue secure no-new-fee remote access with BLACKICE as a *conditional* replacement, retaining Norton, Tailscale routes and the existing remote tool until a production-equivalent canary/rollback proves compatibility. This is roadmap scope, **not** an automatic installed-release grant, paid-model authorization or remote shell exposure.
- Treat Iron God engine as a first-class development workstream alongside product progress every hourly Chat-mode run. Preserve the existing task identity and cadence; no duplicates.
- Permit isolated nonconflicting manual development while supervisor paused ONLY where independently authorized. Never infer supervisor execution authorization from this decision.
- Investigate all 13 recorded Canon S2 failures; fix genuine defects without weakening safety checks. Recheck current results before planning.
- Finish and verify existing issue-frontier identity, process-tree containment, and Canon state-machine candidates rather than duplicate them.
- Maintain owner-control authority, exact-head checks, active claims/leases, Product Reality, and one supervisor identity. Never access the retired human-mode.json marker.
- Product lanes must not starve: Knowledge Engine online-on-demand research (locally processed), Rocksmith playable CDLC, Everward first playable loop, VoiceStudio speech-to-singing export.

## Next slices
1. Reconcile active local Canon S2 candidates, classify 13 failures, run exact-head native tests.
2. Verify issue-frontier assignment identity and bounded task dispatch; accept only with full gates.
3. Produce one independently authorized product slice with native checks and evidence.
