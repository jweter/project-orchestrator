# Jev Shadow Decision Fabric

## Purpose

Jev is integrated as a bounded **shadow decision layer**, not as another scheduler, agent, merge authority, or source of repository truth.

The governing order is:

```text
live repository evidence
  -> deterministic priority / eligibility / authority
  -> explicit legal candidate set
  -> Jev shadow judgment
  -> deterministic validation of Jev output
  -> existing scheduler decision and execution
  -> normal verification / promotion gates
```

Jev is allowed to answer a narrow question only after ordinary software has established what is legal. It cannot make an illegal action legal.

## Why this exists

The portfolio has observed liveness defects where legal work exists but orchestration can still fall into false-idle or poor candidate ordering. A typed probabilistic decision model is potentially useful at that semantic boundary.

The experiment is deliberately falsifiable. We must measure whether Jev improves candidate selection, retry routing, or false-idle avoidance. If it does not, the integration remains unused or is removed.

## Implementation

The implementation is `project_orchestrator.jev_shadow`.

It:

- accepts sanitized run state plus explicit candidate objects;
- discards every candidate not already marked `ELIGIBLE`;
- skips all neural inference when zero or one legal candidate exists;
- constructs a TypeSafe System One Choice request only when there are multiple legal candidates;
- validates the returned choice against the exact deterministic legal set;
- fails closed on malformed or out-of-set output;
- returns `execution_authority=false` for every result;
- never treats Jev output as preflight, CI, merge, review, or Product Reality evidence;
- never serializes credentials into requests, results, or telemetry.

The implementation uses Python's standard library HTTP client so this pilot adds no runtime package dependency.

## Credentials and cost

No credential is committed.

A real provider call requires all three conditions:

1. the caller passes `--live`;
2. `JEV_SHADOW_LIVE=1`;
3. `TYPESAFE_API_KEY` exists in the runtime environment.

This prevents an ordinary preflight, unit test, scheduler inspection, or replay from creating external spend. The current slice does **not** make a paid live call.

## CLI

Given `decision.json`:

```json
{
  "state": {
    "run_id": "example",
    "priority_class": "P5",
    "current_gate": "candidate-selection"
  },
  "candidates": [
    {
      "candidate_id": "rocksmith-eof-parity",
      "project_id": "rocksmith",
      "repository": "jweter/rocksmith-cdlc-generator",
      "title": "Continue authorized EOF parity slice",
      "priority": 5,
      "eligibility_state": "ELIGIBLE"
    },
    {
      "candidate_id": "rocksmith-validator-hardening",
      "project_id": "rocksmith",
      "repository": "jweter/rocksmith-cdlc-generator",
      "title": "Harden deterministic validator",
      "priority": 5,
      "eligibility_state": "ELIGIBLE"
    }
  ]
}
```

Generate the shadow request without any network activity:

```bash
python -m project_orchestrator.jev_shadow decision.json
```

A live shadow call is intentionally double-gated:

```bash
JEV_SHADOW_LIVE=1 TYPESAFE_API_KEY=... \
  python -m project_orchestrator.jev_shadow decision.json --live
```

The returned recommendation remains advisory.

## Initial decision points

The first supported experiments are:

1. **P5 candidate ordering** after deterministic eligibility has admitted two or more candidates inside one independent product lane.
2. **False-idle challenge** when deterministic state says safe work exists but the scheduler would otherwise emit no candidate.
3. **Retry/rotate judgment** only after failure class, legal retry options, and resource constraints have already been determined deterministically.

Jev must not be used where a deterministic answer already exists.

## Evaluation

Shadow telemetry should compare Jev's recommendation against actual later outcomes without letting Jev affect them initially.

Useful measurements include:

- false-idle avoided in replay;
- chosen candidate later reached verified advancement;
- avoidable retries;
- unnecessary verification churn;
- recommendation disagreement with deterministic baseline;
- calibration of returned legal-set probabilities;
- provider latency and external cost;
- policy or quality regressions (must remain zero).

No single successful decision promotes authority.

## Promotion rule

Promotion beyond shadow mode requires comparable repeated evidence showing a useful improvement with no quality, policy, security/privacy, licensing/provenance, Product Reality, cost-boundary, or human-boundary regression.

Even after promotion, deterministic software continues to define the legal action space and validate the result. Jev never receives merge authority.
