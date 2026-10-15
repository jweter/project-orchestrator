"""Crash/reboot bootstrap (roadmap section 16: State Reconstruction).

Cross-references the orphan-worktree scan (local_orphan_worktree_scan.py,
IG-026) against the set of worktree directory names the current
known-slice registry expects. A worktree whose name is not in that
registry is reported ORPHANED, and its only recommended action is
ANCHOR_AND_REVIEW: anchor the work with a recovery ref (IG-023) and
queue it for human/future review. This module never recommends
deletion, regardless of a worktree's own missing/dirty/ambiguous
state, per the roadmap: "Ambiguous work is quarantined. It is never
silently deleted." It performs no Git or ref mutation itself -- it
only classifies already-observed worktree state.
"""

from __future__ import annotations

from dataclasses import dataclass

from .local_orphan_worktree_scan import WorktreeClassification

KNOWN = "KNOWN"
ORPHANED = "ORPHANED"

NO_ACTION = "NO_ACTION"
ANCHOR_AND_REVIEW = "ANCHOR_AND_REVIEW"


@dataclass(frozen=True)
class BootstrapEntry:
    worktree_name: str
    classification: str
    recovery_action: str
    reason: str


def bootstrap_classify(
    worktree_classifications: tuple[WorktreeClassification, ...],
    known_worktree_names: frozenset[str],
) -> tuple[BootstrapEntry, ...]:
    """Classify each discovered worktree as KNOWN or ORPHANED.

    `known_worktree_names` is the set of worktree directory names the
    current known-slice registry expects. A name present there is
    KNOWN with NO_ACTION. A name absent is ORPHANED with
    ANCHOR_AND_REVIEW -- always, regardless of the worktree's own
    missing/dirty/ambiguous classification. This function never
    produces a delete/discard recommendation.
    """
    entries: list[BootstrapEntry] = []
    for classification in worktree_classifications:
        name = classification.entry.path.name
        if name in known_worktree_names:
            entries.append(
                BootstrapEntry(
                    worktree_name=name,
                    classification=KNOWN,
                    recovery_action=NO_ACTION,
                    reason="worktree directory name matches the known-slice registry",
                )
            )
        else:
            entries.append(
                BootstrapEntry(
                    worktree_name=name,
                    classification=ORPHANED,
                    recovery_action=ANCHOR_AND_REVIEW,
                    reason=(
                        "worktree directory name is not in the known-slice "
                        "registry; anchor with a recovery ref and queue for "
                        "human/future review, never delete"
                    ),
                )
            )
    return tuple(entries)
