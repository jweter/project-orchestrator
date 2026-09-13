# Autonomous Engineering Scientist

## Mission

The portfolio is evolving from an autonomous coding agent into an **autonomous engineering scientist**.

The operating loop is:

> **Observe -> formulate hypothesis -> retrieve prior evidence -> design bounded experiment -> estimate cost -> execute -> measure -> compare -> reject or retain -> independently verify -> record -> generalize -> repeat.**

The purpose is not to maximize commits, branches, experiments, or tests. The purpose is to make evidence-backed product and engineering capability improve while reducing preventable repeat failures and routine human intervention.

This model sits inside the existing `SENSE -> REMEMBER -> DECIDE -> ACT -> VERIFY -> LEARN -> ADAPT` control loop. It does not replace DoWork, project authority, the Agent Action Guard, canonical preflight, fresh PR CI, review, security/privacy, licensing/provenance, Product Reality, or reserved-human decisions.

## Scientific invariants

1. **A code change is a hypothesis, not a conclusion.**
2. **A retry must have ancestry.** Anonymous retries are not allowed to masquerade as new information.
3. **Competing fixes are compared only under the same measurement contract and baseline.**
4. **The fixer never proves its own fix.** Candidate results require independent verification before promotion.
5. **Unknown evidence remains unknown.** Missing measurements are never converted to zero, green, low risk, or no impact.
6. **Autonomy is resource-bounded.** Work is scheduled only after Agent Action Guard clearance and explicit capacity accounting.
7. **Opportunity discovery is evidence-driven.** The system may convert known gaps into experiments; it may not invent ROI, probability, percent complete, or product need.
8. **Cross-repo learning is earned.** Domain-specific product requirements never transfer across projects; only repeatedly verified domain-independent engineering mechanisms may become portfolio candidates.
9. **Human attention is reserved for human decisions.** Subjective Product Reality, product direction, destructive/irreversible action, credentials, releases, and other reserved-human boundaries remain outside autonomous authority.

## 1. Experiment Lineage

Every experiment record binds:

- project and repository;
- exact baseline head SHA;
- exact candidate head SHA;
- hypothesis id and hypothesis text;
- baseline evidence reference;
- measurement contract;
- bounded scope;
- evidence references;
- resource estimate;
- Agent Action Guard state;
- attempt number;
- parent experiment/hypothesis when applicable;
- execution state;
- independent verification state;
- measured results;
- verification evidence;
- safety violations.

A follow-up attempt (`attempt > 1`) requires `parent_experiment_id`. This creates a durable ancestry graph:

```text
observed failure
  -> hypothesis A / experiment A1
      -> failed measurement
      -> refined hypothesis A2 / experiment A2
  -> hypothesis B / experiment B1
      -> verified improvement
```

The lineage allows later analysis to distinguish:

- repeating the same attempt;
- refining an existing hypothesis;
- testing a genuinely different mechanism;
- abandoning a dead end;
- reproducing a prior result.

## 2. Bounded Hypothesis Competition

Multiple candidate fixes may be evaluated when the problem is sufficiently deterministic and the resource budget permits it.

A comparison is valid only when all candidates share:

- the same repository;
- the same exact baseline SHA and baseline reference;
- the same measurement contract.

The contract declares:

- `contract_id`;
- `primary_metric`;
- direction (`MINIMIZE` or `MAXIMIZE`);
- measurement tolerance;
- required metrics.

Candidates are ineligible for promotion when:

- the experiment is incomplete;
- independent verification did not pass;
- required metrics are missing;
- a safety violation is recorded.

If the leading candidate does not beat the runner-up beyond the declared tolerance, the result is `NO_CLEAR_WINNER`. The system does not invent a tie-breaker.

Possible comparison states:

- `PROMOTE_CANDIDATE`
- `NO_CLEAR_WINNER`
- `INCONCLUSIVE`
- `INCOMPARABLE`

`PROMOTE_CANDIDATE` is comparison evidence only. Normal product promotion still requires all repository gates.

### Hypothesis count

More parallel candidates are not automatically better. Candidate fan-out must be justified by the Agent Action Guard and Portfolio Resource Scheduler. Default policy should keep bounded competition small; large search spaces require an explicit resource decision.

## 3. Portfolio Resource Scheduler

The scheduler exists because successful autonomy can create a new failure mode: excessive simultaneous work.

It accepts only actions already classified by the Agent Action Guard. Non-`READY` actions are deferred.

Capacity is explicit for:

- compute seconds;
- network calls;
- write operations;
- concurrency;
- external paid cost;
- named exclusive resources such as `ollama`, `unreal`, or another single-owner local service.

### Priority and fairness

P0-P6 remains authoritative.

Within the **same priority**, work is scheduled round-robin by project so one project cannot monopolize all capacity merely because it has a larger queue.

Across **different priorities**, higher priority wins. Fairness never permits P5 work to preempt P0-P4 work.

The scheduler outputs `SCHEDULED`, `DEFERRED_RESOURCE`, or `BLOCKED_BY_ACTION_GUARD`; it executes nothing.

This layer is designed to coordinate the portfolio idle worker, dedicated Rocksmith/Everward workers, Ollama-heavy jobs, CI-heavy work, and future experiment lanes without allowing independent automation loops to fight over the same machine or external capacity.

## 4. Opportunity Sweeper

The Opportunity Sweeper does not replace issue triage, reinvestment planning, throughput optimization, reality checks, or roadmaps.

Those systems identify **what matters**. The sweeper asks:

> Which already-evidenced gap can be converted into a cheap, reversible, measurable experiment now?

Accepted source categories may include:

- recurring failure evidence;
- Product Reality debt;
- stale valuable work;
- reality-check gaps;
- low coverage or missing regression protection;
- measured performance regressions;
- TODO/roadmap evidence;
- reinvestment candidates;
- repeated Jeremy intervention;
- evidence-observability gaps.

An opportunity is eligible for autonomous experiment proposal only when it has:

- explicit evidence;
- bounded scope;
- a hypothesis;
- a measurement contract;
- reversibility;
- deterministic measurement.

It is deduplicated when active work or an equivalent active experiment already covers the same repository/scope/measurement contract.

Ranking is deliberately conservative:

1. existing portfolio priority;
2. breadth of explicit evidence;
3. deterministic tie ordering.

It does **not** rank by invented ROI, probability, severity, time saved, or percent complete.

## 5. Cross-Repo Pattern Distillation

Learning memory is local by default. Portfolio promotion is earned.

Pattern distillation accepts verified lesson records with:

- explicit `pattern_key`;
- exact repository and project;
- engineering mechanism;
- applicability conditions;
- counterexamples;
- evidence references;
- verified outcome and outcome evidence;
- explicit domain-independence flag;
- explicit declaration that no product requirement is being transferred.

Only positive verified outcomes are eligible, such as:

- `PREVENTED_FAILURE`
- `EARLIER_DETECTION`
- `SHORTENED_REPAIR`
- `SUCCESSFUL_PATTERN_REUSE`
- `VERIFIED_CORRECTNESS_IMPROVEMENT`

Default portfolio promotion requires evidence from at least **two independent repositories and two independent projects**.

The distiller groups only by an explicit exact `pattern_key`; it does not perform fuzzy semantic generalization. This prevents an LLM from deciding that two superficially similar product behaviors are the same engineering requirement.

Possible states:

- `PORTFOLIO_PATTERN_CANDIDATE`
- `LOCAL_OR_PROJECT_ONLY`
- `CONFLICTING_MECHANISM`
- `REJECT_DOMAIN_COUPLED`

A portfolio pattern candidate is still only a candidate. Adoption into the orchestrator follows normal branch -> preflight -> PR -> fresh CI -> review -> merge governance.

## 6. Scientist Cycle Evidence

`validate_scientist_cycle()` validates the following stages:

1. `OBSERVE`
2. `FORMULATE_HYPOTHESIS`
3. `RETRIEVE_PRIOR_EVIDENCE`
4. `DESIGN_BOUNDED_EXPERIMENT`
5. `ESTIMATE_COST`
6. `ACTION_GUARD`
7. `EXECUTE`
8. `MEASURE`
9. `COMPARE`
10. `INDEPENDENT_VERIFY`
11. `RECORD`
12. `GENERALIZE`
13. `REPEAT`

`EXECUTE=COMPLETE` requires:

- `action_guard_state=READY`;
- `schedule_state=SCHEDULED`.

`GENERALIZE=COMPLETE` requires `INDEPENDENT_VERIFY=COMPLETE`.

`COMPARE=COMPLETE` requires an explicit measurement-contract reference.

A cycle is closed only when every stage is explicitly `COMPLETE` or `NOT_APPLICABLE`. `UNKNOWN` never closes a stage.

## 7. CLI

The deterministic planner is exposed as:

```text
project-orchestrator-scientist experiment-record <input.json>
project-orchestrator-scientist compare-hypotheses <experiments.json>
project-orchestrator-scientist resource-schedule <input.json>
project-orchestrator-scientist opportunity-sweep <input.json>
project-orchestrator-scientist pattern-distill <input.json>
project-orchestrator-scientist validate-cycle <input.json>
```

All commands are pure planning/validation operations. They do not execute shell commands, mutate product repositories, launch experiments, merge PRs, delete branches, spend money, or claim Product Reality.

## 8. Relationship to Fix_It and Evolution

The scientist loop gives Fix_It a stronger experimental substrate:

```text
Repair
  -> was the repair verified?
  -> did another hypothesis perform better?
Replacement
  -> is the old mechanism itself the recurring failure source?
Upgrade
  -> can the verified mechanism prevent the class earlier?
Redesign
  -> does repeated evidence show the architecture is the bottleneck?
Evolution
  -> has the better mechanism generalized across independent projects?
```

Evolution therefore becomes an evidence-backed promotion process, not a synonym for "make a bigger change."

## 9. Human role

The system should systematically remove Jeremy from deterministic transport and verification work while preserving his attention for decisions that require human judgment.

The desired human interaction becomes:

- approve or reject genuine product-direction alternatives;
- evaluate subjective Product Reality when machines cannot;
- approve resource/cost/destructive/irreversible boundaries;
- choose among strategically different but technically valid directions.

Routine test execution, retry transport, evidence collection, deterministic comparison, failure ancestry, branch-safe experimentation, and pattern bookkeeping should not require Jeremy to sit at the laptop.

## 10. Success criteria

This capability is successful only if evidence later shows outcomes such as:

- fewer preventable repeat failures;
- fewer anonymous retries;
- more failures diagnosed from exact evidence before mutation;
- higher first-pass verification rates without weaker gates;
- bounded resource contention between workers;
- fewer duplicate experiments/branches;
- verified useful opportunities discovered outside the most-polished areas;
- local lessons promoted only after independent cross-project evidence;
- lower routine Jeremy intervention.

Experiment count, PR count, branch count, and test count are not success metrics by themselves.

Related: #59, #57, #56, `docs/EVOLUTION_ENGINE.md`, `docs/AGENT_ACTION_GUARD.md`, `docs/AUTONOMOUS_ENGINEERING_AGENT.md`.
