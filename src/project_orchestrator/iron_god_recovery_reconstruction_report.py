"""Recovery reconstruction report (roadmap section 32: Definition of
Done -- "Reboot Azathoth. The engine reconstructs: accepted baseline,
queued publication, candidates, active work, leases -- without
duplicate execution.").

Closes the composition gap found in the 2026-10-16 Definition-of-Done
gap analysis: the individual reconstruction primitives all exist and
are tested in isolation -- local_orphan_worktree_scan.py's read-only
worktree inventory (IG-026), iron_god_state_reconstruction.py's
per-slice Git-ground-truth reconstruction (IG-056),
iron_god_crash_bootstrap.py's KNOWN/ORPHANED worktree classification
against the known-slice registry (IG-057) -- but nothing had
composed them into one consolidated report answering the whole
Definition-of-Done sentence in a single call. This module is that
composition.

It performs no reboot, no Git mutation, and no deletion -- it is a
pure, read-only, dry-run proof: run it any time (not only after an
actual reboot) to answer whether reconstruction would be correct
right now. Ambiguous or orphaned state is always surfaced, never
silently resolved, per the roadmap's own quarantine principle.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .iron_god_crash_bootstrap import ORPHANED, BootstrapEntry, bootstrap_classify
from .iron_god_state_reconstruction import (
    AMBIGUOUS,
    ReconstructedSliceState,
    reconstruct_slice_state,
)
from .local_orphan_worktree_scan import WorktreeClassification, scan_worktrees


@dataclass(frozen=True)
class SliceReconstructionRequest:
    slice_id: str
    milestone_id: str


@dataclass(frozen=True)
class RecoveryReconstructionReport:
    slice_states: tuple[ReconstructedSliceState, ...]
    worktree_bootstrap: tuple[BootstrapEntry, ...]
    worktree_classifications: tuple[WorktreeClassification, ...]

    @property
    def has_ambiguous_slice(self) -> bool:
        return any(state.status == AMBIGUOUS for state in self.slice_states)

    @property
    def has_orphaned_worktree(self) -> bool:
        return any(
            entry.classification == ORPHANED for entry in self.worktree_bootstrap
        )

    @property
    def clean(self) -> bool:
        """True only when nothing ambiguous or orphaned was found --
        the proof that reconstruction would be unambiguous right now."""
        return not self.has_ambiguous_slice and not self.has_orphaned_worktree


def build_recovery_reconstruction_report(
    repo_root: Path,
    state_root: Path,
    slice_requests: tuple[SliceReconstructionRequest, ...],
    known_worktree_names: frozenset[str],
) -> RecoveryReconstructionReport:
    """Compose a full recovery-reconstruction report.

    Reconstructs every slice in `slice_requests` purely from Git
    ground truth (IG-056), scans all registered worktrees (IG-026),
    and classifies each discovered worktree against
    `known_worktree_names` (IG-057). Nothing here mutates anything --
    this is always safe to run as a dry-run proof, with or without an
    actual reboot having occurred. Propagates scan_worktrees' own
    OwnerControlActive unchanged if owner control is active or
    unverified for `state_root`.
    """
    slice_states = tuple(
        reconstruct_slice_state(repo_root, request.slice_id, request.milestone_id)
        for request in slice_requests
    )
    worktree_classifications = tuple(scan_worktrees(repo_root, state_root))
    worktree_bootstrap = bootstrap_classify(
        worktree_classifications, known_worktree_names
    )

    return RecoveryReconstructionReport(
        slice_states=slice_states,
        worktree_bootstrap=worktree_bootstrap,
        worktree_classifications=worktree_classifications,
    )
