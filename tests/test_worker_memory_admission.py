"""Local AI-worker memory admission; never terminate running workers."""

from __future__ import annotations

import json

import pytest

from project_orchestrator.worker_memory_admission import memory_admission
from project_orchestrator.parallel_worker_pool import ParallelWorkerPool
from tests.test_parallel_worker_pool import ManualExecutor
import scripts.continuous_flow_supervisor as supervisor


@pytest.mark.parametrize(
    ("free", "running", "expected_budget", "reason"),
    [
        (200, 0, 0, "CRITICAL_MEMORY_NO_NEW_WORK"),
        (3071, 0, 0, "CRITICAL_MEMORY_NO_NEW_WORK"),
        (3072, 0, 1, "LOW_MEMORY_SINGLE_LANE"),
        (5000, 0, 1, "LOW_MEMORY_SINGLE_LANE"),
        (5000, 1, 0, "LOW_MEMORY_SINGLE_LANE"),
        (6144, 0, 2, "MEMORY_WITHIN_BOUNDED_CAPACITY"),
        (9000, 1, 1, "MEMORY_WITHIN_BOUNDED_CAPACITY"),
        (9000, 2, 0, "MEMORY_WITHIN_BOUNDED_CAPACITY"),
        (9000, 5, 0, "MEMORY_WITHIN_BOUNDED_CAPACITY"),
    ],
)
def test_memory_admission_never_overallocates_ai_builders(
    free, running, expected_budget, reason
):
    plan = memory_admission(running, sample_fn=lambda: (free, 16000))
    assert plan["status"] == reason
    assert plan["new_worker_budget"] == expected_budget
    assert plan["execution_authorized"] is False
    assert plan["active_workers"] == running


@pytest.mark.parametrize(
    "sample",
    [
        lambda: (_ for _ in ()).throw(OSError("unavailable")),
        lambda: (-1, 16000),
        lambda: (True, 16000),
        lambda: (17000, 16000),
        lambda: ("5000", 16000),
    ],
)
def test_missing_or_corrupt_memory_probe_fails_closed(sample):
    plan = memory_admission(0, sample_fn=sample)
    assert plan["status"] == "MEMORY_PROBE_UNVERIFIED"
    assert plan["new_worker_budget"] == 0
    assert plan["execution_authorized"] is False


def test_invalid_running_count_is_never_accepted():
    for invalid in (-1, True, None, 1.5):
        with pytest.raises(ValueError):
            memory_admission(invalid, sample_fn=lambda: (8000, 16000))


def test_dispatch_zero_budget_never_submits_worker(tmp_path, monkeypatch):
    from tests.test_task_grant_dispatch import graph

    activation = {"products": {"rocksmith": {"repositories": {"jweter/repo": "abc"}}}}
    (tmp_path / "hands-off-activation.json").write_text(json.dumps(activation))
    graphs = tmp_path / "capability-graphs"
    graphs.mkdir()
    (graphs / "jweter__repo.json").write_text(json.dumps(graph()))
    monkeypatch.setattr(
        supervisor,
        "RUNTIME_LEASES",
        supervisor.RuntimeLeasePool(supervisor.DEFAULT_RUNTIME_CAPACITIES),
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("never execute a worker under zero memory budget")

    monkeypatch.setattr(supervisor, "run_builder_for_product", forbidden)
    pool = ParallelWorkerPool(
        max_workers_per_product=2,
        max_total_workers=2,
        executor_factory=ManualExecutor,
    )
    try:
        result = supervisor.dispatch_parallel_builders(
            pool,
            tmp_path,
            tmp_path,
            tmp_path,
            ["rocksmith"],
            new_worker_budget=0,
        )
        assert result == []
        assert pool.snapshot()["running_total"] == 0
    finally:
        pool.shutdown(wait=False)


def test_bad_dispatch_budget_cannot_issue_grants(tmp_path):
    pool = ParallelWorkerPool(
        max_workers_per_product=2,
        max_total_workers=2,
        executor_factory=ManualExecutor,
    )
    try:
        for budget in (-1, True, "2"):
            with pytest.raises(ValueError, match="new_worker_budget"):
                supervisor.dispatch_parallel_builders(
                    pool,
                    tmp_path,
                    tmp_path,
                    tmp_path,
                    ["everward"],
                    new_worker_budget=budget,
                )
    finally:
        pool.shutdown(wait=False)
