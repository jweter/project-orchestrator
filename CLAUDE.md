# Claude Code instructions

Follow `AGENTS.md`, `DoWork.txt`, `docs/EVOLUTION_ENGINE.md`, and `docs/EVOLUTION_WORKER_HANDOFF.md`.

The orchestrator is a policy and evidence system. Keep decision logic pure/deterministic where practical; isolate GitHub I/O from ranking logic; preserve fail-closed semantics; add regression tests for every corrected orchestration failure.

Use the Fix_It progression deliberately: **Repair -> Replacement -> Upgrade -> Redesign -> Evolution**. Do not jump stages merely to create activity. Evolution requires verified before/after evidence and must never weaken review, CI, security, privacy, provenance, licensing, architecture, Product Reality, or project-isolation gates.

Treat unattended workers as independent verification/evidence producers. Route their sanitized exact-head results through the Evolution worker handoff. Execute only repository-explicit deterministic safe repairs automatically; otherwise perform bounded engineering diagnosis on a governed branch and require independent verification before recording a repair as successful learning.
