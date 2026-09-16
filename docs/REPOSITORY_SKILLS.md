# Verified Repository Skills

## Purpose

Phase D turns **verified engineering history** into narrow task-triggered advisory guidance. It does not import ECC's global skill catalog and it does not allow a generated skill to become repository authority.

The canonical catalog is:

`engineering/repository-skills.json`

The selector/validator is:

`project_orchestrator.skill_extraction`

## Evidence contract

A repository skill is valid only when all of the following hold:

- every `source_lesson_id` already exists in `engineering/learning-memory.json` as `VERIFIED`;
- the source lesson belongs to the exact same repository and project as the skill;
- each `history_ref` is an exact 40-character commit SHA (optionally prefixed `commit:`);
- repository scope is explicit;
- path patterns and task triggers are explicit;
- the prevention rule is explicit;
- deterministic verification commands are explicit;
- residual risk is preserved;
- `product_reality_may_be_inferred=false`;
- authority remains `ADVISORY_ONLY`.

No fuzzy similarity may activate a repository skill. Selection requires an explicit `fnmatch` path hit or literal task-trigger substring.

## States

`PILOT_READY` means the skill's source evidence and contract have passed deterministic validation and the skill may be surfaced as advisory context in a bounded pilot.

`ACTIVE_ADVISORY` may be used after the repository has deliberately adopted the skill for routine task-triggered guidance. It is still not authority.

Every new skill starts with:

`effectiveness_state = UNMEASURED`

A skill does **not** become proven merely because its source lesson is verified. Its impact must later be measured through verified engineering outcomes such as prevented failure, earlier detection, shorter repair, or verified success-pattern reuse.

## First pilot

The initial pilot is `historical-similarity-false-positive-guard` for `jweter/project-orchestrator`.

Its evidence comes from verified lesson `96afd3d27f39a7888240` plus regression-history commit `9675dab700fada6b6fa5c6a02f45b1261dbf7cc4`. The skill is triggered only for the risk-predictor/area-learning paths or explicit task phrases such as `risk predictor` and `path similarity`.

It preserves the verified rule that generic repository/path tokens must not fabricate historical risk evidence.

## CLI

Validate the catalog:

```bash
project-orchestrator-skill-extract validate
```

Report catalog state:

```bash
project-orchestrator-skill-extract report
```

Select relevant advisory skills before an edit:

```bash
project-orchestrator-skill-extract select \
  --repository jweter/project-orchestrator \
  --path src/project_orchestrator/risk_predictor.py \
  --task-summary "change the risk predictor"
```

A no-match result is normal. The selector must not manufacture relevance merely to inject more context.

## Promotion and expansion

Add a repository skill only when a real verified engineering lesson exists and the guidance can be tied to deterministic repository-native verification. Product repositories should receive skills one at a time, starting with repeated high-value failure classes.

Candidate order after this pilot:

1. Knowledge Engine evidence/provenance contract changes;
2. Rocksmith timing/alignment and arrangement invariants;
3. Everward deterministic simulation/persistence boundaries.

Those product skills require verified product-repository lessons/history before they are created. Do not copy this orchestrator skill across repositories.

## Authority boundary

Repository skills may inform an agent before editing. They may never override:

- roadmap or architecture;
- repository CI/preflight;
- review findings;
- security/privacy policy;
- provenance/licensing policy;
- Product Reality;
- explicit human-only decisions.

The intended pipeline is:

`verified history -> task-triggered advisory skill -> engineering change -> independent verification -> measured outcome`

—not:

`generated skill -> authority`.
