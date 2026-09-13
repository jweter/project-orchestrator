# Evolution Worker Handoff

## Purpose

Unattended workers are independent evidence producers. They do not become autonomous product authors merely because the Evolution Engine exists.

The normal handoff is:

```text
unattended worker / CI / local verifier
  -> exact-head sanitized result
  -> Evolution worker bridge
  -> classify next action
     -> VERIFIED_PASS
     -> SAFE_AUTO_REPAIR_AVAILABLE
     -> ENGINEERING_REPAIR_REQUIRED
     -> GATHER_EVIDENCE
     -> ENVIRONMENT_RETRY
     -> PRODUCT_REALITY_REQUIRED
     -> HUMAN_DECISION_REQUIRED
     -> MEASURE_PREVENTION
     -> EVOLUTION_VERIFIED
  -> repair/engineering lane when needed
  -> independent verification
  -> verified learning memory
  -> Evolution stage reassessment
```

This is the operational connection between the portfolio's unattended verification policy and `docs/EVOLUTION_ENGINE.md`.

## Worker result contract

Every result must identify:

- project;
- repository;
- branch when applicable;
- exact 40-character commit SHA;
- probe/test id;
- stable failure/success `pattern_id`;
- evidence reference;
- timezone-aware observation timestamp;
- canonical status;
- sanitized summary;
- sanitized log text when relevant.

Canonical statuses are:

- `PASS`
- `FAIL`
- `REVIEW_REQUIRED`
- `PRODUCT_REALITY_REQUIRED`
- `ENVIRONMENT_FAILURE`

The bridge also accepts `PRODUCT REALITY REQUIRED` at input and normalizes it to the machine spelling.

The bridge refuses project/repository mismatches. Knowledge Engine results can come only from Core/Web/AI; Rocksmith only from its repository; Everward only from its repository. This prevents a portfolio process from becoming an accidental cross-project product-requirement channel.

Raw private payloads, credentials, tokens, secrets, or private Product Reality artifacts are forbidden. Evidence must already be sanitized before handoff.

## PASS

A PASS is an exact-head verification fact, not blanket product approval.

The bridge records the result as a verified success-pattern event for the named pattern. If prior evidence shows a prevention/upgrade candidate and comparable before/after measurements prove improvement with no measured degradation, the Evolution Engine may return `EVOLUTION_VERIFIED`.

Otherwise PASS returns either:

- `VERIFIED_PASS`; or
- `MEASURE_PREVENTION` when a verified improvement exists but needs outcome measurement before promotion.

## FAIL

A verified worker FAIL is routed through the existing deterministic repair engine.

The Evolution bridge does not define a second repair authority.

Only repository-explicit deterministic repair classes already allowed by `repair.py` may become `SAFE_AUTO_REPAIR_AVAILABLE`:

- FORMAT
- IMPORT_SORT
- LINT_SAFE_FIX

Everything else becomes `ENGINEERING_REPAIR_REQUIRED` unless the evidence is actually an environment failure.

This includes test failures, security, dependencies, typing, governance, provenance, Product Reality, and UNKNOWN failures. Those require bounded engineering diagnosis; no speculative mutation is authorized by the bridge.

## Safe repair execution

`project-orchestrator-evolve worker-handoff --execute-safe-repair` may execute only an already-approved deterministic repair plan against an explicit local repo root.

After commands run, the result is **not PASS**. The next state is `VERIFY_REPAIR`.

The repair must still be verified independently with the repository's canonical exact-head preflight/CI/worker lane before promotion or learning-memory persistence.

This separation is intentional:

> the fixer is not the authority that proves its own repair succeeded.

## REVIEW_REQUIRED

`REVIEW_REQUIRED` returns `GATHER_EVIDENCE`.

No root cause, product defect, or repair is invented merely because the worker could not decide.

## ENVIRONMENT_FAILURE

`ENVIRONMENT_FAILURE` returns `ENVIRONMENT_RETRY`.

An environment failure is lane-scoped and is not silently converted into a product-code defect. Independent safe development remains eligible under the normal anti-stall rules.

A `FAIL` whose sanitized deterministic fingerprint is clearly `ENVIRONMENT` is also routed to `ENVIRONMENT_RETRY`.

## PRODUCT_REALITY_REQUIRED

Product Reality remains separate authority. The bridge returns `PRODUCT_REALITY_REQUIRED` and does not clear, automate away, or reinterpret subjective/engine/runtime acceptance debt.

This does not freeze unrelated development lanes.

## Reserved human decisions

When evidence explicitly reaches a reserved human boundary, `HUMAN_DECISION_REQUIRED` outranks automatic repair or Evolution promotion.

Reserved boundaries remain those in the portfolio governance: fundamental product direction, unauthorized major architecture changes, paid-service decisions, licenses, destructive migrations, security/privacy boundaries, owner credentials, publishing/release, irreversible actions, materially ambiguous product outcomes, and irreducibly subjective Product Reality.

## Verified repair learning

After an independently verified repair, use:

```text
project-orchestrator-evolve record-verified-lesson --input <lesson.json>
```

The lesson must include:

- symptom;
- verified root cause;
- fix;
- independent verification;
- regression protection;
- residual risk.

Only sanitized `VERIFIED` lessons are accepted by the existing learning-memory contract.

The result can then be persisted through the repository's normal governed learning-memory update path.

## Evolution reassessment

Later worker results for the same stable `pattern_id` are compared with prior verified events.

Examples:

```text
first verified defect
  -> REPAIR

same defect recurs and mechanism is proven brittle
  -> REPLACEMENT candidate

verified prevention moves the check earlier
  -> UPGRADE

failure still recurs after prevention
  -> REDESIGN candidate

before/after evidence proves better outcomes with no degradation
  -> EVOLUTION
```

Earlier learning can also be marked `PREVENTED`, `SHORTENED`, `NO_CHANGE`, or `UNKNOWN` when later evidence supports that judgment.

## CLI

Build a worker handoff:

```text
project-orchestrator-evolve worker-handoff --input worker.json
```

Execute only an already-approved deterministic safe repair:

```text
project-orchestrator-evolve worker-handoff \
  --input worker.json \
  --execute-safe-repair \
  --repo-root <local-checkout>
```

Record a verified lesson into JSON output:

```text
project-orchestrator-evolve record-verified-lesson --input lesson.json
```

## DoWork integration

During the hourly closed loop:

- **SENSE** consumes live GitHub state plus worker evidence;
- **REMEMBER** retrieves relevant verified lessons;
- **DECIDE** uses priority/project authority plus the Evolution handoff;
- **ACT** performs only authorized repair or engineering work;
- **VERIFY** requires independent exact-head evidence;
- **LEARN** records the verified lesson when a meaningful lesson exists;
- **ADAPT** asks whether the pattern remains Repair, should become Replacement/Upgrade/Redesign, or has earned Evolution.

No stage is skipped merely to create throughput.

Related: #51, #54, `docs/EVOLUTION_ENGINE.md`.
