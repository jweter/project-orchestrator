from project_orchestrator.jev_runtime import (
    record_foreman_run,
    reconcile_runtime,
    runtime_cycle,
)


def plan():
    return {
        "projects": [{"project_id": "everward", "selected_lane": {"lane_id": "a"}}],
        "jev_shadow": {
            "everward": {
                "deterministic_legal_candidate_ids": ["a", "b"],
                "decision": {
                    "state": "SHADOW_RECOMMENDATION",
                    "selected_candidate_id": "b",
                    "probabilities": {"a": 0.4, "b": 0.6},
                    "neural_call_made": True,
                },
            }
        },
    }


def test_runtime_capture_is_idempotent(tmp_path):
    path = tmp_path / "decisions.jsonl"
    first = record_foreman_run(path, run_id="run-1", plan=plan())
    second = record_foreman_run(path, run_id="run-1", plan=plan())
    assert first["created"] == 1
    assert second["created"] == 0
    assert second["records_total"] == 1


def test_runtime_reconciliation_persists_terminal_outcome(tmp_path):
    path = tmp_path / "decisions.jsonl"
    record_foreman_run(path, run_id="run-1", plan=plan())
    result = reconcile_runtime(
        path,
        results=[{"lane_id": "a", "state": "MERGED", "head_sha": "abc"}],
    )
    assert result["closed"] == 1
    assert result["evaluation"]["comparable_decisions"] == 1
    assert result["evaluation"]["deterministic_better"] == 1


def test_pending_result_does_not_rewrite_ledger(tmp_path):
    path = tmp_path / "decisions.jsonl"
    record_foreman_run(path, run_id="run-1", plan=plan())
    before = path.read_bytes()
    result = reconcile_runtime(path, results=[{"lane_id": "a", "state": "CI_PENDING"}])
    assert result["closed"] == 0
    assert path.read_bytes() == before


def test_runtime_cycle_remains_non_authoritative(tmp_path):
    result = runtime_cycle(
        tmp_path / "decisions.jsonl",
        run_id="run-1",
        plan=plan(),
        results=[{"lane_id": "a", "state": "MERGED"}],
    )
    assert result["execution_authority"] is False
    assert result["promotion_authority"] is False
    assert result["reconciliation"]["closed"] == 1
