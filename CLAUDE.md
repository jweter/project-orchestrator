# Claude Code instructions

Follow `AGENTS.md`, `DoWork.txt`, and `docs/EVOLUTION_ENGINE.md`.

The orchestrator is a policy and evidence system. Keep decision logic pure/deterministic where practical; isolate GitHub I/O from ranking logic; preserve fail-closed semantics; add regression tests for every corrected orchestration failure.

Use the Fix_It progression deliberately: **Repair -> Replacement -> Upgrade -> Redesign -> Evolution**. Do not jump stages merely to create activity. Evolution requires verified before/after evidence and must never weaken review, CI, security, privacy, provenance, licensing, architecture, Product Reality, or project-isolation gates.
