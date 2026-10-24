from __future__ import annotations

import pytest

from project_orchestrator.iron_god_conflict_aware_scheduling import (
    REJECTED_CAPACITY,
    REJECTED_CONFLICT,
    SELECTED,
    SchedulingError,
    schedule_conflict_aware,
)
from project_orchestrator.iron_god_conflict_detector import footprint


def test_single_conflict_free_candidate_within_capacity_is_selected():
    footprints = [footprint("s1", ["a.py"])]
    (decision,) = schedule_conflict_aware(footprints, available_slots=1)
    assert decision.status == SELECTED


def test_conflicting_lower_ranked_candidate_is_rejected_with_reason():
    footprints = [footprint("s1", ["a.py"]), footprint("s2", ["a.py"])]
    decisions = schedule_conflict_aware(footprints, available_slots=2)
    by_id = {d.slice_id: d for d in decisions}

    assert by_id["s1"].status == SELECTED
    assert by_id["s2"].status == REJECTED_CONFLICT
    assert "s1" in by_id["s2"].reason


def test_conflict_free_candidate_beyond_capacity_is_rejected_capacity():
    footprints = [footprint("s1", ["a.py"]), footprint("s2", ["b.py"])]
    decisions = schedule_conflict_aware(footprints, available_slots=1)
    by_id = {d.slice_id: d for d in decisions}

    assert by_id["s1"].status == SELECTED
    assert by_id["s2"].status == REJECTED_CAPACITY


def test_every_candidate_gets_exactly_one_decision():
    footprints = [
        footprint("s1", ["a.py"]),
        footprint("s2", ["a.py"]),
        footprint("s3", ["b.py"]),
    ]
    decisions = schedule_conflict_aware(footprints, available_slots=1)
    assert {d.slice_id for d in decisions} == {"s1", "s2", "s3"}
    assert len(decisions) == 3


def test_zero_available_slots_rejects_everything_with_capacity_reason():
    footprints = [footprint("s1", ["a.py"]), footprint("s2", ["b.py"])]
    decisions = schedule_conflict_aware(footprints, available_slots=0)
    assert all(d.status == REJECTED_CAPACITY for d in decisions)
    assert all(d.reason for d in decisions)


def test_negative_available_slots_raises():
    with pytest.raises(SchedulingError):
        schedule_conflict_aware([footprint("s1", ["a.py"])], available_slots=-1)


def test_empty_footprints_yields_empty_result():
    assert schedule_conflict_aware([], available_slots=3) == ()


def test_independent_slices_both_selected_within_capacity():
    footprints = [footprint("s1", ["a.py"]), footprint("s2", ["b.py"])]
    decisions = schedule_conflict_aware(footprints, available_slots=2)
    assert all(d.status == SELECTED for d in decisions)


def test_duplicate_slice_id_raises():
    footprints = [footprint("s1", ["a.py"]), footprint("s1", ["b.py"])]
    with pytest.raises(SchedulingError):
        schedule_conflict_aware(footprints, available_slots=2)


# 2026-10-16 fail-closed regressions, per
# IG073_CAPACITY_CONFLICT_AUDIT_20261015.md /
# IRON_GOD_FAIL_CLOSED_REGRESSION_HANDOFF_20261016.md: conflict
# attribution must never name a candidate that was itself rejected
# for capacity, and zero capacity must reject every candidate as
# REJECTED_CAPACITY, never REJECTED_CONFLICT.


def test_zero_capacity_rejects_all_overlapping_candidates_as_capacity():
    footprints = [footprint("A", ["same.py"]), footprint("B", ["same.py"])]
    decisions = schedule_conflict_aware(footprints, available_slots=0)
    assert [d.status for d in decisions] == [REJECTED_CAPACITY, REJECTED_CAPACITY]
    assert all(d.reason for d in decisions)


def test_rejected_capacity_candidate_is_not_a_conflict_winner():
    footprints = [
        footprint("A", ["a.py"]),
        footprint("B", ["b.py"]),
        footprint("C", ["b.py"]),
    ]
    decisions = schedule_conflict_aware(footprints, available_slots=1)
    assert [d.status for d in decisions] == [
        SELECTED,
        REJECTED_CAPACITY,
        REJECTED_CAPACITY,
    ]
    assert "B" not in decisions[2].reason


def test_conflict_with_actually_selected_candidate_is_reported():
    footprints = [footprint("A", ["a.py"]), footprint("B", ["a.py"])]
    decisions = schedule_conflict_aware(footprints, available_slots=1)
    assert [d.status for d in decisions] == [SELECTED, REJECTED_CONFLICT]
    assert "A" in decisions[1].reason
