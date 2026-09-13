# Agent policy

Read, in order:
1. `DoWork.txt`
2. `docs/UNATTENDED_VERIFICATION.md`
3. `engineering/control-plane.json`
4. `docs/ARCHITECTURE.md`
5. `docs/ROADMAP.md`
6. `docs/project-status.yaml`
7. `docs/REMOTE_PREFLIGHT.md`

This repository coordinates product repositories; it does not replace their governing documents.

Routine objective verification is machine-owned. Environment dependence is not automatically human dependence: schedule authorized unattended workers for laptop/Windows/Unreal/Ollama/local-only verification before escalating to Jeremy. Jeremy remains optional milestone/product acceptance except for genuinely subjective, irreversible, security/privacy, credential, release, or product-direction decisions allowed by `DoWork.txt` and `docs/UNATTENDED_VERIFICATION.md`.

Never infer missing GitHub state, invent Product Reality, invent percent complete, merge red/pending/uncertain code, bypass per-repository preflight/CI, create competing ownership, disable DoWork, or commit secrets/private Product Reality payloads.

When local execution is unavailable, use the repository-native remote branch preflight path documented in `docs/REMOTE_PREFLIGHT.md` when that repository authorizes and exposes it; never treat a missing local checkout alone as permission to park otherwise executable work.

Prefer small deterministic policy changes with regression tests.
