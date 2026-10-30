# Iron God — Autonomous Local-Git Software Factory
Date adopted: 2026-10-14
Status: controlling TARGET ARCHITECTURE and rollout contract; not a production-complete claim.

## Product-owner intent
Jeremy sets product goals, constraints and acceptance authority once. ChatGPT is a strategic design, review and diagnosis interface; a ChatGPT conversation or hourly DoWork automation must NEVER be a prerequisite for the next ordinary engineering slice. Azathoth's persistent local Iron God supervisor owns continuous execution within explicit owner-granted permissions. Claude Code and other locally registered agents are interchangeable bounded workers, not the source of scheduling or release authority.

## Non-negotiable operating model
LOCAL GIT IS THE DEVELOPMENT AUTHORITY. GitHub is the asynchronous external RELEASE, independent CI, collaboration and off-device recovery authority. Do NOT require a GitHub branch, PR, merge, or service availability between accepted local development slices. Keep local Git commit/worktree/ref lineage, exact-head verification and rollback. Locally accepted != externally released. Never publish unverified product changes; never weaken independent release gates.

Pipeline:
Roadmap/product usability gate -> Capability Compiler -> bounded SliceSpec -> capacity/owner/resource gate -> isolated worker worktree -> local commit -> independent exact-head verification -> required Product Reality -> integration against local accepted baseline -> durable milestone / recovery checkpoint -> IMMEDIATE next eligible slice.
In parallel ONLY: accepted milestone -> durable publication queue -> GitHub push/PR -> independent CI/review -> policy-approved merge -> released baseline sync. Publication failure becomes deferred; it does not undo acceptance or block unrelated local development.

## FIRST PRIORITY — No More NEXT / Continue
The first proof is ONE persistent local controller, ONE real worker, ONE independent verifier, ONE real product slice, and automatic dispatch of a second non-conflicting slice without any chat prompt, hourly tick, or GitHub request. Implement the minimal event-driven immediate-refill primitive EARLY, alongside the accepted-baseline foundation, rather than waiting until roadmap Phase 7. Preserve the Phase 7 advanced capacity scheduler and full stress suite as later work.

The hourly DoWork run is an independent watchdog/CAPA/metrics audit, never the throughput clock. Existing scheduler must remain enabled unless owner instructs otherwise; prevent competing schedulers or duplicate actor claims. Workers may leave a completed candidate and claim unrelated work immediately once their lease and resource constraints permit. Integration controller alone advances protected accepted refs via tested compare-and-swap.

## Work selection and repairs
Prioritize proven first-working-build/product usability blockers, existing P0/P1 risks, and unfinished dependent product work over unmeasured orchestrator expansion. Every task specifies objective, source baseline SHA, allowed/forbidden paths, expected artifact, resource/runner identity, deterministic acceptance, Product Reality policy and retry budget.
Agent NO_OP -> diagnose and replan/narrow/switch permitted runner, not the identical attempt forever.
Test failure -> persist logs and candidate ref, create bounded repair slice; failed acceptance may not publish.
Local conflict -> preserve candidate and classify, repair/replan or choose independent work; never guess at unsafe conflict resolution.
Transport/CI/review blocked -> queue/reconcile publication asynchronously; continue independent local work.
Crash/reboot -> reconstruct from SQLite journal + protected Git refs + receipts; fence stale worker identities; never delete ambiguous work.
No eligible work -> report candidate-by-candidate exclusions with exact reasons; human escalation only for real product ambiguity/approval or unsafe operation.
No new worker may overwrite Claude Code's existing in-flight work, dirty worktrees, agent state or candidate commits.

## Autonomy and ownership
All file, process, scheduler, integration, recovery and publication mutation paths respect the same fail-closed human-mode marker. TAKE THE WHEEL (enabled:true) means NO ENGINEERING, including background launches. Never reset, clear or infer release from the owner marker; only explicit user action releases. No focus-stealing console/windows or gameplay disruption. The local controller should need neither Remote Desktop Commander nor ChatGPT for routine operation. Do not try to evade external tool permission denials or OS security restrictions; use explicitly granted local authority.
A machine-readable bounded autonomy contract must enforce approved repositories/paths, local model and paid API budgets, concurrency, disk and RAM headroom, allowed retry counts and forbidden destructive operations. Prompt injection or worker claims cannot widen permission. Major direction/security/license/credential/cost/irreversible changes remain owner-only.

## Durability and quality
SQLite transactional state (WAL, appropriate synchronous durability) plus append-only event journal; Git refs are independently reconstructible recovery anchors. A candidate is not accepted merely because its worker asserts success. Independent verifier checks exact commit, repository-native full preflight, integration regression, required product artifact/real-use evidence and receipt integrity. Backup locally accepted work off-device with exposure budget. Throttle allocations on critically low storage or RAM; NEVER clean user Documents, Pictures, Rocksmith content or unrelated files.

## Five gated implementation increments
A. Audit Claude's live work, preserve uncommitted state, inventory existing IG implementation, safety/permission/deployment status, baseline product usability. Do not duplicate completed slices.
B. Implement/prove minimal event-driven auto-dispatch (finish -> harvest -> verify -> integrate -> refill) and real product slice A -> slice B with no prompt. No GitHub dependency.
C. Complete SQLite/ref authority, protected checkpoints, reboot recovery and publication deferral; induce simulated transport outage and prove two independent locally accepted milestones, ordered later publication.
D. Complete roadmap/capability compiler, dedupe, bounded repair and independent evidence receipts; escalate only genuine owner decisions.
E. Ramp concurrency based on measured delivered usable product milestones, RAM/disk risk and repeat failure rates; roll out one repo at a time. Keep user-control absolute.

## Measurable gates
G1: supervisor boot/restart restores state, no duplicate claims; worker implements a real VoiceStudio or Knowledge Engine change.
G2: third-party/native tests + required Product Reality independently pass, exact local Git head pinned; accepted ref advances only by integration controller.
G3: simulated GitHub outage: another eligible independent slice starts automatically and is locally accepted. Publication deferred; no product worker occupied waiting for GitHub.
G4: controller reboot preserves candidates, accepted refs and publication queue without data loss; owner takeover halts mutation.
G5: restored GitHub publication passes independent CI/review and releases in order, subject to policy/owner release authorization.
G6: full end-to-end transition starts next useful slice with ZERO routine Jeremy interventions.
Use VERIFIED USABLE PRODUCT MILESTONES/WEEK WITH ZERO OWNER INTERVENTION as the primary outcome; report blocked duration, duplicate work, retries, disk risk, and accepted/unpublished backlog. Commits/PRs/tests are supporting data, not achievement.

## Safe migration, not rewrite
Keep existing active system and Claude's in-flight candidate work untouched. Shadow observe -> candidate mode -> local acceptance canary -> deferred publication canary -> multi-repo rollout -> retire GitHub-coupled worker flow. No production cutover, owner-control change, GitHub push, auto-merge or scheduled-task change solely because this file was written. Record each IG item as VERIFIED, CANDIDATE, or NOT IMPLEMENTED with exact evidence; never mark complete from design prose.

## Source crosswalk
Parent architecture: Project Iron God Roadmap.txt Phases 0–10, IG-001–086. Related local documents: docs/IRON_GOD_ROADMAP_PROGRESS.md, docs/IRON_GOD_LOCAL_FIRST_STATE.md, docs/IRON_GOD_LOCAL_INTEGRATION.md, docs/EXECUTION_AUTHORITY.md, docs/ROADMAP.md, DoWork.txt and engineering/control-plane.json. In conflicts, explicit user direction and fail-closed safety/product authority outrank this design.