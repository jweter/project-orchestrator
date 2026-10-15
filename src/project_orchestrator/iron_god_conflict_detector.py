"""Conflict detection between candidate slices, based on changed-path sets.

Implements the roadmap's Conflict Model (section 22): each slice declares
its changed-path footprint, and two slices are independent only if those
path sets are completely disjoint. This is a lightweight, declarative
pre-check -- it answers "can these be integrated in parallel?" before any
actual content-level merge is attempted. It performs no Git operation and
can still miss a conflict that only shows up at the content level (see
iron_god_integration_worktree.py's apply_candidate for that check).

This module does not decide which of two conflicting slices should
proceed -- that is integration policy, a decision for the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class SliceChangeFootprint:
    slice_id: str
    changed_paths: frozenset[str]


def footprint(slice_id: str, changed_paths: Iterable[str]) -> SliceChangeFootprint:
    """Build a validated footprint. Rejects an empty slice_id or an empty
    changed-paths set -- every slice must declare what it touches."""
    if not slice_id:
        raise ValueError("slice_id must be non-empty")
    paths = frozenset(changed_paths)
    if not paths:
        raise ValueError(f"{slice_id!r} must declare at least one changed path")
    return SliceChangeFootprint(slice_id=slice_id, changed_paths=paths)


@dataclass(frozen=True)
class ConflictReport:
    slice_a: str
    slice_b: str
    conflicting_paths: frozenset[str]

    @property
    def conflicts(self) -> bool:
        return bool(self.conflicting_paths)


def detect_conflict(a: SliceChangeFootprint, b: SliceChangeFootprint) -> ConflictReport:
    """Report the exact overlapping paths between two footprints."""
    if a.slice_id == b.slice_id:
        raise ValueError(f"cannot compare slice {a.slice_id!r} against itself")
    overlap = a.changed_paths & b.changed_paths
    return ConflictReport(
        slice_a=a.slice_id, slice_b=b.slice_id, conflicting_paths=overlap
    )


def conflict_free_subset(
    footprints: list[SliceChangeFootprint],
) -> tuple[list[SliceChangeFootprint], list[ConflictReport]]:
    """Select a deterministic, order-preserving, mutually independent
    subset of `footprints`, and separately report every pairwise conflict
    found against already-selected footprints.

    This does not decide which conflicting slice should proceed -- it
    only reports conflicts and offers one order-preserving selection
    (first-seen wins); the actual integration policy is the caller's
    decision, not this function's.
    """
    seen_ids: set[str] = set()
    for item in footprints:
        if item.slice_id in seen_ids:
            raise ValueError(f"duplicate slice_id in footprints: {item.slice_id!r}")
        seen_ids.add(item.slice_id)

    selected: list[SliceChangeFootprint] = []
    reports: list[ConflictReport] = []
    for candidate in footprints:
        conflicted = False
        for already in selected:
            report = detect_conflict(candidate, already)
            if report.conflicts:
                reports.append(report)
                conflicted = True
        if not conflicted:
            selected.append(candidate)
    return selected, reports
