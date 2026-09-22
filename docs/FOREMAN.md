# Foreman

Foreman is the portfolio lane controller for Project Hands-Off Evolution.

Its job is narrow: keep Knowledge Engine, Rocksmith, and Everward represented as
three independent work-conserving lanes while preserving every repository-local
authority and verification gate.

Foreman is not a fourth product, a merge bot, or a replacement ownership database.

## Authority

Foreman derives its view from:

1. live GitHub PR/issue/branch/commit evidence;
2. repository-local roadmap, architecture, policy, CI, provenance, and Product Reality rules;
3. sanitized terminal worker evidence;
4. authorized candidate slices produced by the existing decomposition/capability machinery.

Its ledger is an ephemeral scheduling projection. GitHub and repository-local
governance remain authoritative.
## Canonical lane model

Every plan contains exactly three parent lanes:

- `knowledge-engine` — coordinated Core/Web/AI family;
- `rocksmith` — Rocksmith CDLC Generator;
- `everward` — Project Everward.

A lane is one of:

- `DISPATCH_READY`
- `ACTIVE_EXISTING`
- `WAITING_ASYNC`
- `BLOCKED_REPAIR`
- `BLOCKED`
- `NO_SAFE_ELIGIBLE_WORK`

Waiting is asynchronous state. It never consumes another product's execution
opportunity. A conflict-free candidate may be selected while another slice in
the same product waits on an independent gate.
## Execution history compaction

Historical grants remain evidence but are not normal scheduler inputs.

Foreman partitions worker/grant records into:

- active execution;
- asynchronous waits;
- explicitly actionable quarantines;
- bounded archived history.

Completed, abandoned, rejected, cancelled, non-actionable quarantined, and other
terminal records are summarized and bounded. This prevents hundreds of old grant
records from participating in every scheduling pass while preserving audit evidence.

Foreman never silently deletes source evidence.
## Dispatch contract

Foreman emits `dispatch_queue`, containing at most one substantial newly selected
slice for each independent product in a run.

The candidate queue is passed through Steward before LaptopActor execution when machine-health evidence is available. Foreman selects eligible work; Steward admits a sustainable subset. Machine-health deferral never becomes product failure. Foreman itself does not modify product code and never merges.

A selected lane still requires:

`IMPLEMENTING -> PREFLIGHT_GREEN -> PR_OPEN -> CI_PENDING -> GREEN -> MERGED`

and then any repository-required Product Reality state.

`product_advancement_rate` remains unknown until verified mutation/promotion
evidence exists. Selection or dispatch alone is not product advancement.
## CLI

Generate a plan from sanitized evidence:

```text
project-orchestrator foreman-plan evidence.json
```

The evidence object contains:

- `live_snapshot`
- `candidates`
- optional `execution_records`
- optional `events`

Terminal merge/worker/verification events request immediate continuation so the
scheduler can select the next safe slice without an arbitrary idle interval.

## Safety invariants

Foreman never invents roadmap work, weakens gates, infers Product Reality, grants
itself merge authority, or turns UNKNOWN evidence into a positive assertion.
