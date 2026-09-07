# Agent policy

Read, in order:
1. `DoWork.txt`
2. `engineering/control-plane.json`
3. `docs/ARCHITECTURE.md`
4. `docs/ROADMAP.md`
5. `docs/project-status.yaml`
6. `docs/REMOTE_PREFLIGHT.md`

This repository coordinates product repositories; it does not replace their governing documents.

Never infer missing GitHub state, invent Product Reality, invent percent complete, merge red/pending/uncertain code, bypass per-repository preflight/CI, create competing ownership, disable DoWork, or commit secrets/private Product Reality payloads.

When local execution is unavailable, use the repository-native remote branch preflight path documented in `docs/REMOTE_PREFLIGHT.md` when that repository authorizes and exposes it; never treat a missing local checkout alone as permission to park otherwise executable work.

Prefer small deterministic policy changes with regression tests.
