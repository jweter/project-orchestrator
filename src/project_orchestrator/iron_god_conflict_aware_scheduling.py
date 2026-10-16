"""Conflict-aware scheduling (roadmap section 22: Conflict Model;
section 24: Rocksmith Slice Selector; Phase 7: Event-Driven
Scheduler).

Composes the existing pairwise conflict check
(iron_god_conflict_detector.py, IG-033's detect_conflict) with
available capacity (iron_god_capacity_model.py, IG-070) to decide,
for an already value-ranked list of candidate slice footprints,
which are SELECTED to run this round -- and, critically, exactly why
every other candidate was not, per the roadmap: "If no slice can
run, the decision record must say exactly why every candidate was
rejected. NO_CONFLICT_FREE_READY_SLICE without per-candidate
explanation is insufficient." This module adds that per-candidate
rejection trace and capacity bounding on top of IG-033's conflict
detection; it does not re-implement pairwise conflict detection and
does not claim or dispatch anything itself -- "claim it atomically"
remains the caller's own atomic-claim mechanism, out of scope here.

2026-10-16 fail-closed fix: selection and conflict attribution must
happen together, one ranked candidate at a time, growing the winner
set only while capacity remains. A candidate is REJECTED_CONFLICT
only when it conflicts with an ALREADY-SELECTED winner; a
conflict-free candidate that simply arrives after capacity is full
is REJECTED_CAPACITY, never REJECTED_CONFLICT naming a candidate
that was itself never selected. This also makes available_slots == 0
correctly reject every candidate as REJECTED_CAPACITY, since no
candidate is ever added to the winner set in that case. Calling
conflict_free_subset() over the full input first (the previous
approach) could not express this, because it computed conflicts
before capacity was known.
"""

from __future__ import annotations

from dataclasses import dataclass

from .iron_god_conflict_detector import SliceChangeFootprint, detect_conflict

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

    Raises SchedulingError if `available_slots` is negative, or if
    `footprints` contains a duplicate slice_id. Every candidate
    receives exactly one decision with a concrete reason -- there is
    no outcome that rejects everything without explaining each one
    individually. A candidate is only ever REJECTED_CONFLICT against
    a slice that is itself SELECTED in this same result; a
    conflict-free candidate arriving after capacity is exhausted is
    REJECTED_CAPACITY, never attributed a conflict it doesn't have
    against a winner.
    """
    if available_slots < 0:
        raise SchedulingError("available_slots must be >= 0")

    seen_ids: set[str] = set()
    for item in footprints:
        if item.slice_id in seen_ids:
            raise SchedulingError(
                f"duplicate slice_id in footprints: {item.slice_id!r}"
            )
        seen_ids.add(item.slice_id)

    selected: list[SliceChangeFootprint] = []
    conflicted_against: dict[str, list[str]] = {}

    for candidate in footprints:
        against = [
            winner.slice_id
            for winner in selected
            if detect_conflict(candidate, winner).conflicts
        ]
        if against:
            conflicted_against[candidate.slice_id] = against
            continue
        if len(selected) < available_slots:
            selected.append(candidate)

    selected_ids = {item.slice_id for item in selected}

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
                    reason=f"conflicts with actually-selected slice(s): {against}",
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
