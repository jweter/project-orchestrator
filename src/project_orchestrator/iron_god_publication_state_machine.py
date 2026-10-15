"""Publication state machine (roadmap section 10).

Defines the legal transitions between PublicationStates and a pure
transition function. Performs no GitHub, network, or Git operation --
it only decides whether a proposed state change is legal, and if so,
returns a new PublicationJob reflecting it.
"""

from __future__ import annotations

from dataclasses import replace

from .iron_god_publication_job import PUBLICATION_STATES, PublicationJob

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "READY_TO_PUBLISH": frozenset({"PUSHING", "MANUAL_POLICY_GATE"}),
    "PUSHING": frozenset({"BRANCH_PUBLISHED", "TRANSPORT_BLOCKED"}),
    "BRANCH_PUBLISHED": frozenset({"PR_OPEN", "TRANSPORT_BLOCKED"}),
    "PR_OPEN": frozenset({"CI_PENDING"}),
    "CI_PENDING": frozenset({"REVIEW_PENDING", "CI_FAILED"}),
    "REVIEW_PENDING": frozenset({"MERGE_READY", "REVIEW_CHANGES_REQUIRED"}),
    "MERGE_READY": frozenset({"MERGED", "BASE_DIVERGED"}),
    "MERGED": frozenset({"RELEASE_SYNCED"}),
    "RELEASE_SYNCED": frozenset(),
    "PUBLICATION_DEFERRED": frozenset({"READY_TO_PUBLISH"}),
    "CI_FAILED": frozenset({"PUBLICATION_DEFERRED"}),
    "REVIEW_CHANGES_REQUIRED": frozenset({"PUBLICATION_DEFERRED"}),
    "BASE_DIVERGED": frozenset({"PUBLICATION_DEFERRED"}),
    "TRANSPORT_BLOCKED": frozenset({"PUBLICATION_DEFERRED"}),
    "MANUAL_POLICY_GATE": frozenset({"READY_TO_PUBLISH", "PUBLICATION_DEFERRED"}),
}

assert set(ALLOWED_TRANSITIONS) == set(PUBLICATION_STATES), (
    "every publication state must have a transition-table entry, "
    "even if empty for a terminal state"
)


class IllegalTransitionError(RuntimeError):
    """Raised when a proposed publication-state transition is not legal."""


def legal_next_states(current_state: str) -> frozenset[str]:
    """Return the set of states `current_state` may legally move to."""
    if current_state not in PUBLICATION_STATES:
        raise IllegalTransitionError(f"unknown current_state: {current_state!r}")
    return ALLOWED_TRANSITIONS[current_state]


def transition(job: PublicationJob, next_state: str) -> PublicationJob:
    """Return a new PublicationJob with current_state advanced to
    `next_state`, if and only if that move is legal from the job's
    current state. Never mutates `job`; performs no GitHub, network,
    or Git operation."""
    allowed = legal_next_states(job.current_state)
    if next_state not in allowed:
        raise IllegalTransitionError(
            f"{job.current_state!r} -> {next_state!r} is not a legal transition "
            f"(allowed: {sorted(allowed)})"
        )
    return replace(job, current_state=next_state)
