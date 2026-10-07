# Iron God Issue Closure Engine

GitHub Issues are first-class product work, not a cleanup queue.

## Lifecycle

DISCOVER -> TRIAGE -> REPRODUCE -> CONFIRMED -> IMPLEMENT -> VERIFY -> PR -> CI -> MERGE -> DEFAULT-BRANCH PROOF -> CLOSE -> LEARN.

An issue remains visible until it has an explicit disposition. Merge is not proof of closure.

## Scheduling

Issue candidates enter the existing portfolio scheduler only after repository authority, ownership, dependency, and safety gates make them legal. Existing P0-P4 work keeps precedence. Within a legal issue frontier, deterministic ranking prefers severity, usability impact, roadmap/dependency unlock, and age, while penalizing risk and effort.

Roadmap work and issue work therefore share one product frontier rather than competing hidden queues.

## Closure invariant

CLOSED_VERIFIED requires explicit evidence for reproduction/disposition, implementation verification, regression protection (or proven non-applicability), required CI, default-branch merge, default-branch proof, and Product Reality when required.

No actionable issue may be orphaned: it must expose a state and exact next gate.

## Telemetry

Per repository/product report: open actionable issue debt, critical issue debt, lifecycle-state counts, and orphaned actionable work. Arrival and verified-closure rates may be added only from durable timestamped evidence; UNKNOWN is preferred to inference.
