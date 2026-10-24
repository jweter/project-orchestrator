from __future__ import annotations

import pytest

from project_orchestrator.iron_god_capacity_model import (
    CapacityModelError,
    WorkerOccupancy,
    compute_available_capacity,
)


def test_no_occupancy_yields_full_capacity():
    result = compute_available_capacity(6, ())
    assert result.available_development_slots == 6
    assert result.occupied_development_slots == 0
    assert result.excluded_publication_lane_workers == ()


def test_development_worker_reduces_available_slots():
    occupancy = (WorkerOccupancy(worker_id="w1", lane="development"),)
    result = compute_available_capacity(6, occupancy)
    assert result.occupied_development_slots == 1
    assert result.available_development_slots == 5


def test_publication_worker_does_not_reduce_development_capacity():
    occupancy = (WorkerOccupancy(worker_id="w1", lane="publication"),)
    result = compute_available_capacity(6, occupancy)
    assert result.occupied_development_slots == 0
    assert result.available_development_slots == 6
    assert result.excluded_publication_lane_workers == ("w1",)


def test_mixed_lanes_only_development_counts_against_capacity():
    occupancy = (
        WorkerOccupancy(worker_id="w1", lane="development"),
        WorkerOccupancy(worker_id="w2", lane="publication"),
        WorkerOccupancy(worker_id="w3", lane="development"),
    )
    result = compute_available_capacity(6, occupancy)
    assert result.occupied_development_slots == 2
    assert result.available_development_slots == 4
    assert result.excluded_publication_lane_workers == ("w2",)


def test_negative_total_slots_raises():
    with pytest.raises(CapacityModelError):
        compute_available_capacity(-1, ())


def test_unknown_lane_raises():
    occupancy = (WorkerOccupancy(worker_id="w1", lane="idle"),)
    with pytest.raises(CapacityModelError):
        compute_available_capacity(6, occupancy)


def test_duplicate_worker_id_raises():
    occupancy = (
        WorkerOccupancy(worker_id="w1", lane="development"),
        WorkerOccupancy(worker_id="w1", lane="publication"),
    )
    with pytest.raises(CapacityModelError):
        compute_available_capacity(6, occupancy)


def test_occupied_exceeding_total_raises():
    occupancy = (
        WorkerOccupancy(worker_id="w1", lane="development"),
        WorkerOccupancy(worker_id="w2", lane="development"),
    )
    with pytest.raises(CapacityModelError):
        compute_available_capacity(1, occupancy)


def test_fully_occupied_yields_zero_available():
    occupancy = tuple(
        WorkerOccupancy(worker_id=f"w{i}", lane="development") for i in range(6)
    )
    result = compute_available_capacity(6, occupancy)
    assert result.available_development_slots == 0
