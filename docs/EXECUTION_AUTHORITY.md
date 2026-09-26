# Execution Authority

This is the canonical human-readable execution contract for `jweter/project-orchestrator`.

## Precedence

1. Explicit current user instruction.
2. Repository-native product authority for the repository being changed.
3. `DoWork.txt` for portfolio execution order and schedule invariants.
4. `engineering/control-plane.json` for machine-enforced control-plane policy.
5. This document for shared orchestrator execution rules.
6. Specialist documents only when their named mechanism is actually invoked.

If two sources conflict, fail closed and use the higher-precedence source. Never invent missing authority.

## Always-on invariants

- Never disable, pause, delete, replace, or reschedule DoWork unless Jeremy explicitly instructs it.
- Existing P0-P3 work precedes new P4/P5 work.
- Zero open product PRs is a build trigger when safe authorized work exists.
- Never merge red, pending, conflicted, review-blocked, policy-blocked, or materially uncertain work.
- Product Reality is evidence, never inference. Never invent readiness percentages or live verification.
- Product repositories retain product architecture, security, privacy, licensing, provenance, and release authority.
- Knowledge Engine is one coordinated product across Core/Web/AI. Rocksmith and Everward remain independent projects.
- Connector/tool failure is not product failure and is not permission to stop the schedule.
- Human escalation is reserved for genuinely human-authority decisions; routine diagnosis and deterministic repair stay automated.

## Execution loop

For each run: observe live state -> diagnose the highest-priority concrete defect -> make one bounded repair -> verify exact head -> record evidence -> rotate while asynchronous gates run.

Use the Fix_It progression deliberately: Repair -> Replacement -> Upgrade -> Redesign -> Evolution. Repeated adjacent repairs are evidence that the subsystem may require redesign rather than more local patches.

Before mutation, use exact repository/branch/head identity. Preserve active ownership and unique dirty work. Ordinary changes use a feature/repair branch from current default head.

Promotion is:

`IMPLEMENTING -> PREFLIGHT_UNVERIFIED -> PREFLIGHT_GREEN -> PR_OPEN -> CI_PENDING -> GREEN -> MERGED -> PRODUCT_REALITY_PENDING/VERIFIED`

A changed head invalidates prior exact-head verification and review clearance. Immediately before merge, re-read current-head review threads/comments.

## Worker and failure handling

Unattended workers are evidence producers, not merge authorities. Deterministic CODE/TEST/QUALITY failures become P1 repair inputs. Diagnose the first meaningful error and add regression protection when practical.

Environment dependence is not human dependence. Use repository-authorized Windows/Ollama/Unreal/LVN paths when required.

Repeated authorized-but-idle cycles are a liveness defect. Do not translate `NO_WORK` into `NO_AUTHORIZED_WORK` when a product remains authorized.

Recovery must preserve unique work and fail closed when ownership, liveness, or exact-head identity is uncertain.

## Product boundaries

Knowledge Engine preserves evidence traceability, privacy, provenance, deterministic/non-fabricated state, and Core/Web/AI boundaries.

Rocksmith never modifies live Rocksmith/NoCableLauncher or commits commercial/private/restricted content. Bass/Lead/Rhythm remain first-class. Subjective musical judgment remains human.

Everward preserves deterministic simulation, roadmap/design pillars, player agency, persistence authority, and separation of simulation truth from visuals.

## On-demand specialist authority

Read these only when the named mechanism is used:

- Evolution / recurring-friction promotion: `docs/EVOLUTION_ENGINE.md`
- Unattended worker evidence handoff: `docs/EVOLUTION_WORKER_HANDOFF.md`
- Pre-action autonomous clearance: `docs/AGENT_ACTION_GUARD.md`
- Experiment-like engineering: `docs/AUTONOMOUS_ENGINEERING_SCIENTIST.md`
- LVN promotion verification: `docs/LVN_PROMOTION_VERIFICATION.md`
- Emerging/experimental compute: `docs/EMERGING_COMPUTE_EVALUATION.md`
- Repository-local learned skills: `docs/REPOSITORY_SKILLS.md`
- Remote preflight when local execution is unavailable: `docs/REMOTE_PREFLIGHT.md`

Machine contracts paired with those documents remain authoritative for their mechanism. They are not mandatory context when the mechanism is not in use.

## Context discipline

`AGENTS.md` and `CLAUDE.md` are bootstrap files, not parallel policy manuals. They must point here rather than restating specialist rules.

Architecture, roadmap, project status, engineering JSON contracts, and historical design documents are retrieved on demand when the task touches them. Being authoritative for a domain does not make a document always-read context.

New always-on policy belongs here only when it applies broadly to portfolio execution. Feature-specific policy belongs with that feature and is loaded only when invoked.
