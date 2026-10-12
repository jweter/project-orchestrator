# Iron God exclusive development priority — 2026-10-12

Owner decision: until Iron God Local-First v1 meets its roadmap's demonstrated Definition of Done, all NEW engineering slices and available worker capacity must target project-orchestrator / Iron God only.

- Do not dispatch new feature-development slices for Knowledge Engine (core/web/ai), Rocksmith, Everward, or VoiceStudio. Preserve their existing worktrees, commits, branches, and pending publication state.
- In-flight work must be checkpointed safely; do not kill workers or delete unresolved work merely to switch priorities.
- Critical security, data-loss prevention, owner-control enforcement, and recovery actions remain permitted irrespective of repository.
- Continue small, non-conflicting Iron God slices locally, with exact-head verification, local integration, protected accepted refs, and durable milestone receipts.
- Publish only verified major milestones asynchronously; GitHub transport failures cannot block independent local development.
- Do not weaken safety, storage, Product Reality, review, or CI gates to accelerate completion.
- Resume normal portfolio dispatch only after evidenced completion of roadmap Definition of Done or explicit new owner instruction.
- This directive does not itself alter running scheduler code; dispatcher enforcement and regression tests are required before claiming operational cutover.
