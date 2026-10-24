"""Checkpoint controller (roadmap section 12: Checkpoint System).

Given an event kind (a worker local commit, a candidate commit, an
accepted milestone, a recovery checkpoint, or a release), classifies
it into the correct checkpoint class (1-5) and decides the minimum
protection requirement for that class. Decision-only -- this module
never creates, deletes, or mutates any ref or checkpoint itself.
"""

from __future__ import annotations

from dataclasses import dataclass

CHECKPOINT_CLASS_WORKER_COMMIT = 1
CHECKPOINT_CLASS_CANDIDATE_COMMIT = 2
CHECKPOINT_CLASS_ACCEPTED_MILESTONE = 3
CHECKPOINT_CLASS_RECOVERY_CHECKPOINT = 4
CHECKPOINT_CLASS_RELEASE = 5

EVENT_KIND_TO_CLASS = {
    "worker_commit": CHECKPOINT_CLASS_WORKER_COMMIT,
    "candidate_commit": CHECKPOINT_CLASS_CANDIDATE_COMMIT,
    "accepted_milestone": CHECKPOINT_CLASS_ACCEPTED_MILESTONE,
    "recovery_checkpoint": CHECKPOINT_CLASS_RECOVERY_CHECKPOINT,
    "release": CHECKPOINT_CLASS_RELEASE,
}

# Classes at or above this are protected indefinitely (never reclaimed,
# per IG-053's RECLAIMABLE_CHECKPOINT_CLASSES). Class 2 must survive
# worker cleanup but is not "indefinite" like 3+, so it is excluded.
INDEFINITELY_PROTECTED_CLASSES = frozenset(
    {
        CHECKPOINT_CLASS_ACCEPTED_MILESTONE,
        CHECKPOINT_CLASS_RECOVERY_CHECKPOINT,
        CHECKPOINT_CLASS_RELEASE,
    }
)


class UnknownCheckpointEventError(RuntimeError):
    """Raised when an unrecognized checkpoint event kind is classified."""


@dataclass(frozen=True)
class CheckpointClassification:
    event_kind: str
    checkpoint_class: int
    must_survive_worker_cleanup: bool
    protected_indefinitely: bool
    eventually_reclaimable: bool


def classify_checkpoint_event(event_kind: str) -> CheckpointClassification:
    """Classify `event_kind` into its checkpoint class and protection
    requirements. Raises on an unrecognized event kind rather than
    guessing a class."""
    if event_kind not in EVENT_KIND_TO_CLASS:
        raise UnknownCheckpointEventError(
            f"unknown checkpoint event kind: {event_kind!r}"
        )

    checkpoint_class = EVENT_KIND_TO_CLASS[event_kind]
    protected_indefinitely = checkpoint_class in INDEFINITELY_PROTECTED_CLASSES
    must_survive_worker_cleanup = checkpoint_class >= CHECKPOINT_CLASS_CANDIDATE_COMMIT
    eventually_reclaimable = checkpoint_class == CHECKPOINT_CLASS_WORKER_COMMIT

    return CheckpointClassification(
        event_kind=event_kind,
        checkpoint_class=checkpoint_class,
        must_survive_worker_cleanup=must_survive_worker_cleanup,
        protected_indefinitely=protected_indefinitely,
        eventually_reclaimable=eventually_reclaimable,
    )
