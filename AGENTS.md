# Agent policy

Read, in order:
1. `DoWork.txt`
2. `engineering/control-plane.json`
3. `docs/ARCHITECTURE.md`
4. `docs/ROADMAP.md`
5. `docs/project-status.yaml`
6. `docs/EVOLUTION_ENGINE.md`
7. `docs/EVOLUTION_WORKER_HANDOFF.md`
8. `docs/REMOTE_PREFLIGHT.md`

This repository coordinates product repositories; it does not replace their governing documents.

Never infer missing GitHub state, invent Product Reality, invent percent complete, merge red/pending/uncertain code, bypass per-repository preflight/CI, create competing ownership, disable DoWork, or commit secrets/private Product Reality payloads.

Use the Evolution Engine for verified recurring failures and successful engineering patterns: repair the smallest defect first, remember verified learning, move prevention earlier, and promote a mechanism to portfolio-wide evolution only after comparable measured evidence proves improvement without weakening gates. Never transfer unrelated product requirements between projects.

Route sanitized unattended-worker results through `docs/EVOLUTION_WORKER_HANDOFF.md`: safe deterministic repairs may execute only under existing repository-explicit repair authority; all other defects remain bounded engineering work; the fixer never proves its own repair; Product Reality, environment failures, and reserved human decisions remain separate fail-closed lanes.

Immediately before merge, re-read current-head review threads/comments. Any unresolved material correctness, safety, architecture, security, provenance, privacy, licensing, or project-policy finding blocks merge even when GitHub review state is only `COMMENTED`. A head change invalidates earlier review clearance until revalidated.

When local execution is unavailable, use the repository-native remote branch preflight path documented in `docs/REMOTE_PREFLIGHT.md` when that repository authorizes and exposes it; never treat a missing local checkout alone as permission to park otherwise executable work.

Prefer small deterministic policy changes with regression tests.
