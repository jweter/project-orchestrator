# Agent policy

Read, in order:
1. `DoWork.txt`
2. `engineering/control-plane.json`
3. `docs/ARCHITECTURE.md`
4. `docs/ROADMAP.md`
5. `docs/project-status.yaml`
6. `docs/EVOLUTION_ENGINE.md`
7. `docs/EVOLUTION_WORKER_HANDOFF.md`
8. `docs/AGENT_ACTION_GUARD.md`
9. `engineering/agent-action-guard.json`
10. `docs/AUTONOMOUS_ENGINEERING_SCIENTIST.md`
11. `engineering/autonomous-engineering-scientist.json`
12. `docs/REMOTE_PREFLIGHT.md`

This repository coordinates product repositories; it does not replace their governing documents.

Never infer missing GitHub state, invent Product Reality, invent percent complete, merge red/pending/uncertain code, bypass per-repository preflight/CI, create competing ownership, disable DoWork, or commit secrets/private Product Reality payloads.

Use the Evolution Engine for verified recurring failures and successful engineering patterns: repair the smallest defect first, remember verified learning, move prevention earlier, and promote a mechanism to portfolio-wide evolution only after comparable measured evidence proves improvement without weakening gates. Never transfer unrelated product requirements between projects.

For experiment-like autonomous engineering, operate as an **autonomous engineering scientist**: Observe -> formulate hypothesis -> retrieve prior evidence -> design a bounded experiment -> estimate cost -> clear the Agent Action Guard -> obtain portfolio resource scheduling -> execute -> measure -> compare -> independently verify -> record -> generalize only when earned -> repeat. Treat changes as hypotheses, not conclusions. Retries require experiment ancestry. Competing candidates may be compared only under the same repository/baseline/measurement contract. The fixer never proves its own fix.

Before autonomous implementation, repair retry, or experiment-like fan-out, use the Agent Action Guard. Retrieve exact live context and relevant verified learning first; failure-driven work/retries require an exact error reference. Estimate compute, network calls, write operations, concurrency, and any paid external cost. `GATHER_CONTEXT`, `APPROVAL_REQUIRED`, and `FORBIDDEN` are hard stops for autonomous execution. `READY` grants only pre-action clearance and never bypasses repository-native architecture, CI, review, security, privacy, licensing/provenance, Product Reality, or reserved-human gates.

Use the Portfolio Resource Scheduler only after `READY`; it may allocate capacity but never execute work. P0-P6 remains authoritative. Fairness applies only within the same priority. Respect named exclusive resources such as Ollama or Unreal when evidence supplies them.

Use the Opportunity Sweeper only on explicit evidence from live triage, roadmaps, reality checks, reinvestment, measured failures, Product Reality debt, coverage/performance evidence, or other documented gaps. It may propose bounded reversible deterministic experiments; it must never invent ROI, probability, severity, percent complete, or product need.

Keep learning local by default. Cross-repo Pattern Distillation requires repeated verified domain-independent outcomes across independent repositories and projects. Group only by explicit pattern keys; never use fuzzy similarity to transfer product behavior or requirements between Knowledge Engine, Rocksmith, and Everward.

Route sanitized unattended-worker results through `docs/EVOLUTION_WORKER_HANDOFF.md`: safe deterministic repairs may execute only under existing repository-explicit repair authority; all other defects remain bounded engineering work; the fixer never proves its own repair; Product Reality, environment failures, and reserved human decisions remain separate fail-closed lanes.

Immediately before merge, re-read current-head review threads/comments. Any unresolved material correctness, safety, architecture, security, provenance, privacy, licensing, or project-policy finding blocks merge even when GitHub review state is only `COMMENTED`. A head change invalidates earlier review clearance until revalidated.

When local execution is unavailable, use the repository-native remote branch preflight path documented in `docs/REMOTE_PREFLIGHT.md` when that repository authorizes and exposes it; never treat a missing local checkout alone as permission to park otherwise executable work.

Prefer small deterministic policy changes with regression tests.
