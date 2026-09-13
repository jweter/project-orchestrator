# Claude Code instructions

Follow `AGENTS.md`, `DoWork.txt`, `docs/EVOLUTION_ENGINE.md`, `docs/EVOLUTION_WORKER_HANDOFF.md`, and `docs/AGENT_ACTION_GUARD.md`.

The orchestrator is a policy and evidence system. Keep decision logic pure/deterministic where practical; isolate GitHub I/O from ranking logic; preserve fail-closed semantics; add regression tests for every corrected orchestration failure.

Use the Fix_It progression deliberately: **Repair -> Replacement -> Upgrade -> Redesign -> Evolution**. Do not jump stages merely to create activity. Evolution requires verified before/after evidence and must never weaken review, CI, security, privacy, provenance, licensing, architecture, Product Reality, or project-isolation gates.

Before autonomous implementation, repair retries, or experiment-like fan-out, build an exact-head action context and evaluate the Agent Action Guard. Retrieve exact failure evidence instead of guessing from summaries, prove context/learning lookup happened, and estimate compute/network/write/concurrency/paid-service cost. Only `READY` may continue autonomously; `GATHER_CONTEXT`, `APPROVAL_REQUIRED`, and `FORBIDDEN` stop execution until their boundary is resolved. The guard itself never executes commands.

Treat unattended workers as independent verification/evidence producers. Route their sanitized exact-head results through the Evolution worker handoff. Execute only repository-explicit deterministic safe repairs automatically; otherwise perform bounded engineering diagnosis on a governed branch and require independent verification before recording a repair as successful learning.
