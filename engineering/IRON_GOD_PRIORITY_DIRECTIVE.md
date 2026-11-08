# Iron God exclusive development priority — historical decision, 2026-10-12

Status: SUPERSEDED FOR DEVELOPMENT SCOPE by the owner-approved parallel portfolio direction in `docs/OWNER_APPROVED_ROADMAP_2026-10-25.md` (reaffirmed 2026-11-01). The restrictions below describe the historical exclusive-priority phase; they must not be treated as current owner direction or as permission to alter any runtime controller flag. The newer roadmap permits Iron God and product slices in parallel only when each independently satisfies authenticated controller authority, worker/lease ownership, native tests, PR/CI/review, and Product Reality gates.

Historical owner decision (no longer current development priority): until Iron God Local-First v1 meets its roadmap's demonstrated Definition of Done, all NEW engineering slices and available worker capacity must target project-orchestrator / Iron God only.

- Do not dispatch new feature-development slices for Knowledge Engine (core/web/ai), Rocksmith, Everward, or VoiceStudio. Preserve their existing worktrees, commits, branches, and pending publication state.
- In-flight work must be checkpointed safely; do not kill workers or delete unresolved work merely to switch priorities.
- Critical security, data-loss prevention, owner-control enforcement, and recovery actions remain permitted irrespective of repository.
- Continue small, non-conflicting Iron God slices locally, with exact-head verification, local integration, protected accepted refs, and durable milestone receipts.
- Publish only verified major milestones asynchronously; GitHub transport failures cannot block independent local development.
- Do not weaken safety, storage, Product Reality, review, or CI gates to accelerate completion.
- Resume normal portfolio dispatch only after evidenced completion of roadmap Definition of Done or explicit new owner instruction.
- This directive does not itself alter running scheduler code; dispatcher enforcement and regression tests are required before claiming operational cutover.
