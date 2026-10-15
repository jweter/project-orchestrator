"""Artifact retention rules (roadmap sections 12 and 17: Checkpoint
System and Storage Governor).

Given the current storage-pressure classification
(local_storage_pressure.py, IG-050) and an artifact's checkpoint class,
decides whether that artifact is eligible for reclamation. Decision-
only -- this module never deletes anything itself.

Checkpoint classes, per the roadmap's Checkpoint System:
1 Worker Commit (local only, protected while relevant)
2 Candidate Commit (protected local ref, must survive worker cleanup)
3 Accepted Milestone (protected indefinitely until safely represented
  off-device)
4 Recovery Checkpoint (off-device backup when available)
5 Release (GitHub main, tags, releases)

Only Class 1 artifacts may ever be reclaimed by this policy -- Class 2
and above are protected regardless of pressure.
"""

from __future__ import annotations

from dataclasses import dataclass

from .local_storage_pressure import BLOCKED, HEALTHY, STORAGE_PRESSURE

CHECKPOINT_CLASS_WORKER_COMMIT = 1
CHECKPOINT_CLASS_CANDIDATE_COMMIT = 2
CHECKPOINT_CLASS_ACCEPTED_MILESTONE = 3
CHECKPOINT_CLASS_RECOVERY_CHECKPOINT = 4
CHECKPOINT_CLASS_RELEASE = 5

KNOWN_CHECKPOINT_CLASSES = frozenset(
    {
        CHECKPOINT_CLASS_WORKER_COMMIT,
        CHECKPOINT_CLASS_CANDIDATE_COMMIT,
        CHECKPOINT_CLASS_ACCEPTED_MILESTONE,
        CHECKPOINT_CLASS_RECOVERY_CHECKPOINT,
        CHECKPOINT_CLASS_RELEASE,
    }
)
KNOWN_PRESSURE_CLASSIFICATIONS = frozenset({HEALTHY, STORAGE_PRESSURE, BLOCKED})
RECLAIMABLE_CHECKPOINT_CLASSES = frozenset({CHECKPOINT_CLASS_WORKER_COMMIT})


class UnknownCheckpointClassError(RuntimeError):
    """Raised when an unrecognized checkpoint class is evaluated."""


class UnknownPressureClassificationError(RuntimeError):
    """Raised when an unrecognized storage-pressure classification is given."""


@dataclass(frozen=True)
class ArtifactEntry:
    artifact_id: str
    checkpoint_class: int
    in_active_use: bool


@dataclass(frozen=True)
class ArtifactRetentionDecision:
    artifact_id: str
    eligible_for_reclamation: bool
    reason: str


def decide_artifact_retention(
    entry: ArtifactEntry,
    *,
    pressure: str,
) -> ArtifactRetentionDecision:
    """Decide whether `entry` is eligible for reclamation under `pressure`.

    Only checkpoint Class 1 (Worker Commit) artifacts may ever be
    reclaimed. Even a Class 1 artifact is never eligible while HEALTHY
    or while in active use.
    """
    if entry.checkpoint_class not in KNOWN_CHECKPOINT_CLASSES:
        raise UnknownCheckpointClassError(
            f"unknown checkpoint class: {entry.checkpoint_class!r}"
        )
    if pressure not in KNOWN_PRESSURE_CLASSIFICATIONS:
        raise UnknownPressureClassificationError(
            f"unknown pressure classification: {pressure!r}"
        )

    if entry.checkpoint_class not in RECLAIMABLE_CHECKPOINT_CLASSES:
        return ArtifactRetentionDecision(
            artifact_id=entry.artifact_id,
            eligible_for_reclamation=False,
            reason=f"checkpoint class {entry.checkpoint_class} is protected; never eligible",
        )

    if entry.in_active_use:
        return ArtifactRetentionDecision(
            artifact_id=entry.artifact_id,
            eligible_for_reclamation=False,
            reason="artifact is in active use; never eligible regardless of pressure",
        )

    if pressure == HEALTHY:
        return ArtifactRetentionDecision(
            artifact_id=entry.artifact_id,
            eligible_for_reclamation=False,
            reason="storage is HEALTHY; no reclamation pressure",
        )

    return ArtifactRetentionDecision(
        artifact_id=entry.artifact_id,
        eligible_for_reclamation=True,
        reason=f"storage pressure is {pressure}; Class 1 artifact is idle and eligible",
    )
