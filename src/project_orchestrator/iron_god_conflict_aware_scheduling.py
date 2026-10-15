"""Conflict-aware scheduling (roadmap section 22: Conflict Model;
section 24: Rocksmith Slice Selector; Phase 7: Event-Driven
Scheduler).

Composes the existing conflict-free subset selection
(iron_god_conflict_detector.py, IG-033) with available capacity
(iron_god_capacity_model.py, IG-070) to decide, for an already
value-ranked list of candidate slice footprints, which are SELECTED
to run this round -- and, critically, exactly why every other
candidate was not, per the roadmap: "If no slice can run, the
decision record must say exactly why every candidate was rejected.
NO_CONFLICT_FREE_READY_SLICE without per-candidate explanation is
insufficient." This module adds that per-candidate rejection trace
and capacity bounding on top of IG-033's conflict detection; it does
not re-implement conflict detection and does not claim or dispatch
anything itself -- "claim it atomically" remains the caller's own
atomic-claim mechanism, out of scope here.
"""

from __future__ import annotations

from dataclasses import dataclass

from .iron_god_conflict_detector import SliceChangeFootprint, conflict_free_subset

SELECTED = "SELECTED"
REJECTED_CONFLICT = "REJECTED_CONFLICT"
REJECTED_CAPACITY = "REJECTED_CAPACITY"


class SchedulingError(RuntimeError):
    """Raised on invalid scheduling inputs."""


@dataclass(frozen=True)
class SchedulingDecision:
    slice_id: str
    status: str
    reason: str


def schedule_conflict_aware(
    footprints: list[SliceChangeFootprint],
    available_slots: int,
) -> tuple[SchedulingDecision, ...]:
    """Decide SELECTED/REJECTED_CONFLICT/REJECTED_CAPACITY for every
    footprint in `footprints`, in its given (value-ranked) order.

    Raises SchedulingError if `available_slots` is negative. Every
    candidate receives exactly one decision with a concrete reason --
    there is no outcome that rejects everything without explaining
    each one individually.
    """
    if available_slots < 0:
        raise SchedulingError("available_slots must be >= 0")

    conflict_free, conflict_reports = conflict_free_subset(footprints)

    conflicted_against: dict[str, list[str]] = {}
    for report in conflict_reports:
        conflicted_against.setdefault(report.slice_a, []).append(report.slice_b)

    selected_ids = {f.slice_id for f in conflict_free[:available_slots]}

    decisions: list[SchedulingDecision] = []
    for candidate in footprints:
        if candidate.slice_id in selected_ids:
            decisions.append(
                SchedulingDecision(
                    slice_id=candidate.slice_id,
                    status=SELECTED,
                    reason="conflict-free and within available capacity",
                )
            )
        elif candidate.slice_id in conflicted_against:
            against = ", ".join(sorted(conflicted_against[candidate.slice_id]))
            decisions.append(
                SchedulingDecision(
                    slice_id=candidate.slice_id,
                    status=REJECTED_CONFLICT,
                    reason=f"conflicts with already-selected slice(s): {against}",
                )
            )
        else:
            decisions.append(
                SchedulingDecision(
                    slice_id=candidate.slice_id,
                    status=REJECTED_CAPACITY,
                    reason=(
                        "conflict-free but no capacity remained "
                        f"(available_slots={available_slots})"
                    ),
                )
            )
    return tuple(decisions)
