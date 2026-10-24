"""Protected-work audit (roadmap sections 12 and 17; Phase 5 exit
criterion: "Iron God can survive laptop reboot or orchestration-state
loss without losing accepted or unresolved candidate work.")

A final, independent safety-net check meant to run immediately before
any actual reclamation executes on top of an artifact retention
decision (iron_god_artifact_retention.py, IG-053). It never trusts
`eligible_for_reclamation=True` at face value -- it re-derives
protection directly from the artifact's checkpoint class
(iron_god_checkpoint_controller.py, IG-054) and reports a CONFLICT,
never silently correcting or silently proceeding, whenever a decision
claims eligibility for a protected class. This mirrors
iron_god_ref_protection.py's role as a second, independent policy
layer for refs. This module performs no reclamation or deletion
itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from .iron_god_artifact_retention import (
    RECLAIMABLE_CHECKPOINT_CLASSES,
    ArtifactEntry,
    ArtifactRetentionDecision,
)

SAFE = "SAFE"
CONFLICT = "CONFLICT"


class ArtifactIdMismatchError(RuntimeError):
    """Raised when a paired entry/decision do not share an artifact_id."""


@dataclass(frozen=True)
class ProtectedWorkAuditResult:
    artifact_id: str
    status: str
    safe_to_proceed: bool
    reason: str


def audit_protected_work(
    pairs: tuple[tuple[ArtifactEntry, ArtifactRetentionDecision], ...],
) -> tuple[ProtectedWorkAuditResult, ...]:
    """Independently re-verify each (entry, decision) pair before any
    reclamation acts on `decision`.

    Raises ArtifactIdMismatchError if `entry.artifact_id` and
    `decision.artifact_id` disagree, rather than auditing a
    mismatched pair silently.
    """
    results: list[ProtectedWorkAuditResult] = []
    for entry, decision in pairs:
        if entry.artifact_id != decision.artifact_id:
            raise ArtifactIdMismatchError(
                f"entry artifact_id {entry.artifact_id!r} does not match "
                f"decision artifact_id {decision.artifact_id!r}"
            )

        if not decision.eligible_for_reclamation:
            results.append(
                ProtectedWorkAuditResult(
                    artifact_id=entry.artifact_id,
                    status=SAFE,
                    safe_to_proceed=True,
                    reason="decision is not eligible for reclamation; nothing to audit",
                )
            )
            continue

        if entry.checkpoint_class in RECLAIMABLE_CHECKPOINT_CLASSES:
            results.append(
                ProtectedWorkAuditResult(
                    artifact_id=entry.artifact_id,
                    status=SAFE,
                    safe_to_proceed=True,
                    reason=(
                        f"checkpoint class {entry.checkpoint_class} is "
                        "independently confirmed reclaimable"
                    ),
                )
            )
            continue

        results.append(
            ProtectedWorkAuditResult(
                artifact_id=entry.artifact_id,
                status=CONFLICT,
                safe_to_proceed=False,
                reason=(
                    f"decision claims eligibility but checkpoint class "
                    f"{entry.checkpoint_class} is protected; refusing to trust "
                    "the retention decision"
                ),
            )
        )
    return tuple(results)
