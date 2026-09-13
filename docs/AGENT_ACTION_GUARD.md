# Agent Action Guard

## Purpose

Autonomous capability must increase only when context and resource controls increase with it.

This guard is the portfolio's deterministic pre-action boundary for autonomous engineering and experiment-like work. It answers a narrow question before implementation, repair, or a potentially expensive experiment starts:

> Does the agent have enough exact context, exact failure evidence when applicable, and an acceptable resource/cost envelope to act without a reserved human decision?

It grants no execution authority and never replaces repository-native roadmap, architecture, CI, review, security, privacy, licensing/provenance, or Product Reality gates.

## External production lesson

This design incorporates lessons described by Teads Engineering in *We Let AI Agents Orchestrate Our ML Experiments* (2026):

- exposing an existing orchestrator API to agents made action easy but did not provide enough context;
- agents improved after they could inspect datasets, retrieve exact run errors, and gather feature context;
- lower friction sharply increased experiment throughput;
- that throughput created a new cost failure mode, so the system added cost estimation and approval before expensive work.

Reference: https://medium.com/teads-engineering/we-let-ai-agents-orchestrate-our-ml-experiments-fc8606816fde

The portfolio analogue is **context before mutation, exact error before retry, and resource estimate before fan-out**.

## Context package

`build_agent_action_context()` creates a deterministic package containing:

- project and repository;
- exact 40-character head SHA;
- action id and objective;
- authority reference;
- bounded scope references;
- context references such as architecture/control-plane/schema/component metadata;
- evidence references;
- verified prior-lesson references when any apply;
- proof that context lookup and learning lookup were performed;
- exact error reference for retries/failure-driven work;
- attempt number;
- reserved-human, destructive, and irreversible flags.

A context lookup returning no applicable prior lesson is valid only when `learning_lookup_performed=true`. "Nothing relevant found" is different from "we never looked."

## Exact-error rule

Any action that is a retry, or is explicitly responding to a failure, requires `exact_error_ref` before autonomous execution can be READY.

Without it, the guard returns:

```text
GATHER_CONTEXT
```

The agent must retrieve the exact failure evidence rather than infer root cause from a summary.

## Resource envelope

Every proposed autonomous action supplies estimates for:

- compute seconds;
- network calls;
- write operations;
- concurrency;
- whether a paid external service is used;
- estimated external cost in USD when applicable.

Repository/orchestrator policy supplies two levels of limits:

1. **autonomous budget** — exceeding it requires approval;
2. **hard cap** — exceeding it is forbidden by this action path.

Unknown paid external cost always requires approval.

## States

The gate returns exactly one of:

- `READY` — context is complete and resources are within autonomous limits;
- `GATHER_CONTEXT` — required context/evidence is missing;
- `APPROVAL_REQUIRED` — the action reaches cost/resource or reserved-human/destructive/irreversible boundaries;
- `FORBIDDEN` — an explicit hard resource/cost cap is exceeded.

Precedence is fail-safe:

```text
hard-cap violation -> FORBIDDEN
missing context -> GATHER_CONTEXT
approval boundary -> APPROVAL_REQUIRED
otherwise -> READY
```

## CLI

Build and normalize an action context:

```text
project-orchestrator-throughput action-context action-context.json
```

Evaluate the context plus resource envelope against explicit policy:

```text
project-orchestrator-throughput action-gate action-gate.json
```

`action-gate.json` contains:

```json
{
  "context": {},
  "resources": {},
  "policy": {}
}
```

The gate output always includes:

```json
{
  "execution_authority": "GATE_ONLY",
  "commands": [],
  "existing_gates_preserved": true
}
```

The guard does not execute commands.

## Integration with Evolution Engine

Normal autonomous sequence becomes:

```text
SENSE
  -> retrieve exact live state
REMEMBER
  -> retrieve verified relevant lessons
CONTEXT
  -> exact error/evidence + component/schema/authority context
BUDGET
  -> estimate compute/network/write/concurrency/paid cost
ACTION GUARD
  -> READY | GATHER_CONTEXT | APPROVAL_REQUIRED | FORBIDDEN
ACT
  -> only if authorized by the normal engineering lane
VERIFY
  -> independent exact-head evidence
LEARN
  -> persist verified lesson
ADAPT / EVOLVE
```

An Evolution recommendation is not itself permission to execute. The proposed action must still clear this guard and every existing repository gate.

## Secondary-effect monitoring

Greater autonomy can create new bottlenecks even when individual actions are correct. Portfolio reviews should watch for:

- branch/PR proliferation;
- CPU/RAM contention between local workers;
- repeated expensive full preflights when cached exact-head evidence is valid;
- excessive Ollama calls;
- GitHub API/write volume;
- speculative retry loops;
- paid-service usage;
- declining first-pass verification rates as throughput rises.

When a new secondary effect becomes material, it should enter the normal Fix_It / Evolution path: observe -> diagnose -> repair -> verify -> prevent -> measure -> promote.

Related: #57, #56, #51, `docs/EVOLUTION_ENGINE.md`, `docs/THROUGHPUT_GUARD.md`.
