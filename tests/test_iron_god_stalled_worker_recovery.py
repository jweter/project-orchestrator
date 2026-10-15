from __future__ import annotations

from project_orchestrator.iron_god_stalled_worker_recovery import (
    NO_ACTION,
    RECLAIM_AND_REQUEUE,
    decide_stalled_worker_recovery,
)


def test_dead_liveness_reclaims_and_requeues():
    result = decide_stalled_worker_recovery(
        "w1", {}, liveness_probe=lambda worker: "DEAD"
    )
    assert result.status == RECLAIM_AND_REQUEUE
    assert result.liveness == "DEAD"
    assert result.worker_id == "w1"


def test_alive_liveness_is_no_action():
    result = decide_stalled_worker_recovery(
        "w1", {}, liveness_probe=lambda worker: "ALIVE"
    )
    assert result.status == NO_ACTION
    assert result.liveness == "ALIVE"


def test_unknown_liveness_is_no_action_never_reclaim():
    result = decide_stalled_worker_recovery(
        "w1", {}, liveness_probe=lambda worker: "UNKNOWN"
    )
    assert result.status == NO_ACTION
    assert result.liveness == "UNKNOWN"
    assert "ambiguity" in result.reason


def test_default_probe_wires_to_real_worker_liveness_host_mismatch_is_unknown():
    worker = {
        "host": "definitely-not-this-host",
        "pid": 99999,
        "process_start_fingerprint": "fake",
    }
    result = decide_stalled_worker_recovery("w1", worker)
    assert result.liveness == "UNKNOWN"
    assert result.status == NO_ACTION


def test_malformed_worker_dict_via_real_probe_is_unknown_not_dead():
    result = decide_stalled_worker_recovery("w1", None)
    assert result.liveness == "UNKNOWN"
    assert result.status == NO_ACTION
