from __future__ import annotations

import pytest

from project_orchestrator.iron_god_repository_fairness import (
    FairnessPolicyError,
    RepositoryDemand,
    allocate_capacity_fairly,
)


def test_single_repository_gets_min_of_demand_and_available():
    demands = (RepositoryDemand(repository="repo-a", eligible_slice_count=10),)
    (result,) = allocate_capacity_fairly(4, demands)
    assert result.allocated_slots == 4


def test_single_repository_demand_under_available_gets_full_demand():
    demands = (RepositoryDemand(repository="repo-a", eligible_slice_count=2),)
    (result,) = allocate_capacity_fairly(6, demands)
    assert result.allocated_slots == 2


def test_two_repositories_equal_demand_split_evenly():
    demands = (
        RepositoryDemand(repository="repo-a", eligible_slice_count=5),
        RepositoryDemand(repository="repo-b", eligible_slice_count=5),
    )
    results = allocate_capacity_fairly(4, demands)
    by_repo = {r.repository: r.allocated_slots for r in results}
    assert by_repo == {"repo-a": 2, "repo-b": 2}


def test_one_repo_blocked_three_remain_actionable_not_starved():
    demands = (
        RepositoryDemand(repository="repo-blocked", eligible_slice_count=0),
        RepositoryDemand(repository="repo-b", eligible_slice_count=3),
        RepositoryDemand(repository="repo-c", eligible_slice_count=3),
        RepositoryDemand(repository="repo-d", eligible_slice_count=3),
    )
    results = allocate_capacity_fairly(6, demands)
    by_repo = {r.repository: r.allocated_slots for r in results}
    assert by_repo["repo-blocked"] == 0
    assert by_repo["repo-b"] == 2
    assert by_repo["repo-c"] == 2
    assert by_repo["repo-d"] == 2


def test_max_concurrency_cap_is_never_exceeded():
    demands = (
        RepositoryDemand(
            repository="repo-a", eligible_slice_count=10, max_concurrency=2
        ),
        RepositoryDemand(repository="repo-b", eligible_slice_count=10),
    )
    results = allocate_capacity_fairly(8, demands)
    by_repo = {r.repository: r.allocated_slots for r in results}
    assert by_repo["repo-a"] == 2
    assert by_repo["repo-b"] == 6


def test_total_allocated_never_exceeds_available_slots():
    demands = (
        RepositoryDemand(repository="repo-a", eligible_slice_count=100),
        RepositoryDemand(repository="repo-b", eligible_slice_count=100),
    )
    results = allocate_capacity_fairly(7, demands)
    assert sum(r.allocated_slots for r in results) == 7


def test_zero_available_slots_yields_zero_for_all():
    demands = (
        RepositoryDemand(repository="repo-a", eligible_slice_count=5),
        RepositoryDemand(repository="repo-b", eligible_slice_count=5),
    )
    results = allocate_capacity_fairly(0, demands)
    assert all(r.allocated_slots == 0 for r in results)


def test_negative_available_slots_raises():
    with pytest.raises(FairnessPolicyError):
        allocate_capacity_fairly(-1, ())


def test_negative_eligible_slice_count_raises():
    demands = (RepositoryDemand(repository="repo-a", eligible_slice_count=-1),)
    with pytest.raises(FairnessPolicyError):
        allocate_capacity_fairly(4, demands)


def test_negative_max_concurrency_raises():
    demands = (
        RepositoryDemand(
            repository="repo-a", eligible_slice_count=1, max_concurrency=-1
        ),
    )
    with pytest.raises(FairnessPolicyError):
        allocate_capacity_fairly(4, demands)


def test_duplicate_repository_raises():
    demands = (
        RepositoryDemand(repository="repo-a", eligible_slice_count=1),
        RepositoryDemand(repository="repo-a", eligible_slice_count=2),
    )
    with pytest.raises(FairnessPolicyError):
        allocate_capacity_fairly(4, demands)


def test_empty_demands_yields_empty_result():
    assert allocate_capacity_fairly(5, ()) == ()
