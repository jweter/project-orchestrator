# Claude Code instructions

Follow `AGENTS.md` and `DoWork.txt`.

The orchestrator is a policy and evidence system. Keep decision logic pure/deterministic where practical; isolate GitHub I/O from ranking logic; preserve fail-closed semantics; add regression tests for every corrected orchestration failure.
