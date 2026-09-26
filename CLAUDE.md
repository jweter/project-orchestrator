# Claude Code instructions

Follow `AGENTS.md` and the canonical shared contract in `docs/EXECUTION_AUTHORITY.md`.

Do not maintain a second copy of execution policy here. Load specialist documents only when their mechanism is invoked.

Keep control-plane decision logic deterministic where practical, preserve fail-closed semantics, and add regression protection for corrected orchestration failures.

Use the Fix_It progression deliberately: Repair -> Replacement -> Upgrade -> Redesign -> Evolution. Repeated adjacent repairs are evidence to consider redesign rather than manufacturing more local activity.
