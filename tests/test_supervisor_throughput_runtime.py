"""Integration contracts for extracted resident-supervisor diagnostics."""

from __future__ import annotations

import json
from project_orchestrator import supervisor_throughput_runtime as runtime


def test_memory_gate_persists_fail_closed_capacity(tmp_path, monkeypatch):
    monkeypatch.setattr(
        runtime,
        "memory_admission",
        lambda _running: {
            "status": "CRITICAL_MEMORY_NO_NEW_WORK",
            "new_worker_budget": 0,
            "active_workers": 1,
            "execution_authorized": False,
        },
    )
    assert runtime.publish_worker_memory_admission(tmp_path, 1) == 0
    record = json.loads(
        (tmp_path / "worker-memory-admission.json").read_text(encoding="utf-8")
    )
    assert record["status"] == "CRITICAL_MEMORY_NO_NEW_WORK"
    assert record["execution_authorized"] is False


def test_pool_publication_preserves_occupied_vs_executing_counts(tmp_path):
    snapshot = {
        "running_total": 2,
        "executing_total": 1,
        "completed_pending_harvest": 1,
    }
    runtime.publish_worker_pool_snapshot(
        tmp_path,
        snapshot,
        {"running": {}},
        [],
        [],
        "invocation-1",
        {"status": "RECOVERED"},
    )
    row = json.loads(
        (tmp_path / "parallel-worker-pool.json").read_text(encoding="utf-8")
    )
    assert row["pool"] == snapshot
    assert row["runtime_lease_recovery"] == {"status": "RECOVERED"}


def test_builder_degradation_and_no_delivery_are_reported_without_false_green():
    args = {
        "lifecycle_recovery": {"publication_blocked": False},
        "liveness_published": True,
        "workspace_provisioning": {"status": "OK"},
        "convergence": {"status": "CONVERGED"},
        "idle_streaks": {"status": "OK"},
        "liveness": {"status": "ACTIVE_WORK"},
        "delivery": {
            "status": "VERIFIED_DELIVERY_STALLED",
            "seconds_since_start": 3600,
        },
        "builder_ok": False,
        "builder_status": "WORKER_EXCEPTION",
        "rc": 3,
        "linear_rc": 0,
        "quarantines": {"historical": {}},
    }
    reasons = runtime.degraded_reasons(**args)
    codes = {row["reason"] for row in reasons}
    assert codes == {
        "VERIFIED_DELIVERY_STALLED",
        "BUILDER_FAILED",
        "QUARANTINED_GRANTS",
    }
    args["delivery"] = {"status": "AWAITING_FIRST_VERIFIED_DELIVERY"}
    args["builder_status"] = "NO_READY_EXECUTION_SLICE"
    args["builder_ok"] = True
    args["quarantines"] = {}
    assert runtime.degraded_reasons(**args) == []
