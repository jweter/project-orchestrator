from concurrent.futures import Future
from threading import Timer
from time import monotonic

import pytest

from project_orchestrator.parallel_worker_pool import ParallelWorkerPool, WorkerSlot


class ManualExecutor:
    def __init__(self, *, max_workers, thread_name_prefix):
        self.submissions = []

    def submit(self, fn, *args, **kwargs):
        future = Future()
        self.submissions.append((future, fn, args, kwargs))
        return future

    def shutdown(self, **kwargs):
        return None


def test_available_slots_fills_all_products_before_second_workers():
    pool = ParallelWorkerPool(max_workers_per_product=2, max_total_workers=6, executor_factory=ManualExecutor)
    slots = pool.available_slots(["knowledge-engine", "rocksmith", "everward"])
    assert [slot.worker_id for slot in slots] == [
        "knowledge-engine-worker-1",
        "rocksmith-worker-1",
        "everward-worker-1",
        "knowledge-engine-worker-2",
        "rocksmith-worker-2",
        "everward-worker-2",
    ]


def test_waiting_worker_does_not_hide_capacity_for_other_products():
    pool = ParallelWorkerPool(max_workers_per_product=2, max_total_workers=3, executor_factory=ManualExecutor)
    pool.submit(WorkerSlot("rocksmith", 1), lambda: "slow")
    slots = pool.available_slots(["knowledge-engine", "rocksmith", "everward"])
    assert [slot.worker_id for slot in slots] == [
        "knowledge-engine-worker-1",
        "everward-worker-1",
    ]


def test_harvest_is_nonblocking_and_releases_completed_slot():
    executor = ManualExecutor(max_workers=2, thread_name_prefix="x")
    pool = ParallelWorkerPool(max_workers_per_product=1, max_total_workers=2, executor_factory=lambda **_: executor)
    worker_id = pool.submit(WorkerSlot("rocksmith", 1), lambda: "done")
    assert pool.harvest() == {}
    executor.submissions[0][0].set_result(("PR_OPEN", "invocation"))
    assert pool.harvest() == {worker_id: ("PR_OPEN", "invocation")}
    assert pool.snapshot()["running_total"] == 0


def test_worker_exception_is_harvested_not_raised_into_scheduler():
    executor = ManualExecutor(max_workers=1, thread_name_prefix="x")
    pool = ParallelWorkerPool(max_workers_per_product=1, max_total_workers=1, executor_factory=lambda **_: executor)
    worker_id = pool.submit(WorkerSlot("everward", 1), lambda: None)
    executor.submissions[0][0].set_exception(RuntimeError("boom"))
    result = pool.harvest()[worker_id]
    assert isinstance(result, RuntimeError)


def test_product_limit_and_global_limit_fail_closed():
    pool = ParallelWorkerPool(max_workers_per_product=1, max_total_workers=2, executor_factory=ManualExecutor)
    pool.submit(WorkerSlot("knowledge-engine", 1), lambda: None)
    with pytest.raises(RuntimeError, match="product worker limit"):
        pool.submit(WorkerSlot("knowledge-engine", 2), lambda: None)
    pool.submit(WorkerSlot("rocksmith", 1), lambda: None)
    with pytest.raises(RuntimeError, match="saturated"):
        pool.submit(WorkerSlot("everward", 1), lambda: None)


def test_completion_before_wait_wakes_immediately_for_harvest():
    executor = ManualExecutor(max_workers=1, thread_name_prefix="x")
    pool = ParallelWorkerPool(max_total_workers=1, executor_factory=lambda **_: executor)
    worker_id = pool.submit(WorkerSlot("rocksmith", 1), lambda: None)
    executor.submissions[0][0].set_result("PR_OPEN")
    start = monotonic()
    assert pool.wait_for_completion(5.0)
    assert monotonic() - start < 0.5
    assert pool.harvest() == {worker_id: "PR_OPEN"}
    assert pool.available_slots(["rocksmith"])[0].worker_id == worker_id


def test_completion_during_wait_wakes_before_normal_builder_interval():
    executor = ManualExecutor(max_workers=1, thread_name_prefix="x")
    pool = ParallelWorkerPool(max_total_workers=1, executor_factory=lambda **_: executor)
    worker_id = pool.submit(WorkerSlot("everward", 1), lambda: None)
    future = executor.submissions[0][0]
    timer = Timer(0.03, lambda: future.set_result("CI_GREEN"))
    timer.start()
    try:
        start = monotonic()
        assert pool.wait_for_completion(5.0)
        assert monotonic() - start < 0.5
        assert pool.harvest() == {worker_id: "CI_GREEN"}
    finally:
        timer.join()


def test_idle_pool_retains_normal_delay_without_busy_spin(monkeypatch):
    import project_orchestrator.parallel_worker_pool as workers
    slept = []
    monkeypatch.setattr(workers.time, "sleep", slept.append)
    pool = ParallelWorkerPool(executor_factory=ManualExecutor)
    assert not pool.wait_for_completion(12.0)
    assert slept == [12.0]


def test_snapshot_distinguishes_executing_from_completed_unharvested_workers():
    executor = ManualExecutor(max_workers=2, thread_name_prefix="x")
    pool = ParallelWorkerPool(max_total_workers=2, executor_factory=lambda **_: executor)
    pool.submit(WorkerSlot("everward", 1), lambda: "completed")
    pool.submit(WorkerSlot("iron-god", 1), lambda: "still running")
    executor.submissions[0][0].set_exception(RuntimeError("identity mismatch"))

    snapshot = pool.snapshot()
    assert snapshot["running_total"] == 2  # Occupied slots until harvest.
    assert snapshot["executing_total"] == 1
    assert snapshot["completed_unharvested_total"] == 1
    assert snapshot["executing_by_product"]["everward"] == 0
    assert snapshot["executing_by_product"]["iron-god"] == 1
    assert snapshot["workers"][0]["done"] is True

    pool.harvest()
    snapshot = pool.snapshot()
    assert snapshot["running_total"] == 1
    assert snapshot["executing_total"] == 1
    assert snapshot["completed_unharvested_total"] == 0
