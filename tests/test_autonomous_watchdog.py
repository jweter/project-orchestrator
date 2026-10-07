import json

from project_orchestrator.autonomous_watchdog import run_persistent_watchdog, run_watchdog_iteration


def stalled():
    return {"knowledge-engine": {"candidate_tasks": [{"task_key": "x", "repository": "jweter/knowledge-engine-core", "exact_head": "abc"}]}}


def args(tmp_path):
    return {
        "state_path": tmp_path / "state.json",
        "telemetry_path": tmp_path / "t.jsonl",
        "claude_state_dir": tmp_path / "c",
        "laptop_queue_dir": tmp_path / "q",
    }


def test_second_consecutive_stall_promotes_p1_and_requests_continuation(tmp_path):
    call_args = args(tmp_path)
    first = run_watchdog_iteration(lanes=stalled(), **call_args)
    second = run_watchdog_iteration(lanes=stalled(), **call_args)
    assert first["watchdog"]["status"] == "STALL_OBSERVED"
    assert second["watchdog"]["status"] == "P1_PRODUCTION_STALL"
    assert second["watchdog"]["consecutive_stalls"] == 2
    continuation = json.loads((tmp_path / "continuation-request.json").read_text())
    assert continuation["reason"] == "PRODUCTION_STALL"
    assert continuation["trigger"] == "WATCHDOG"\n    assert continuation["repository"] == "jweter/knowledge-engine-core"\n    assert continuation["exact_head"] == "abc"


def test_progress_resets_stall_counter(tmp_path):
    call_args = args(tmp_path)
    run_watchdog_iteration(lanes=stalled(), **call_args)
    out = run_watchdog_iteration(lanes={}, **call_args)
    assert out["watchdog"]["status"] == "HEALTHY"
    assert out["watchdog"]["consecutive_stalls"] == 0


def test_persistent_runner_repeats_without_human_prompt(tmp_path):
    sleeps = []
    out = run_persistent_watchdog(
        lane_provider=lambda: {},
        max_iterations=3,
        interval_seconds=1,
        sleep=lambda seconds: sleeps.append(seconds),
        **args(tmp_path),
    )
    assert len(out) == 3
    assert sleeps == [1, 1]
    assert json.loads((tmp_path / "state.json").read_text())["status"] == "HEALTHY"


def test_stall_does_not_overwrite_existing_continuation(tmp_path):
    call_args = args(tmp_path)
    continuation_path = tmp_path / "continuation-request.json"
    original = {"repository": "jweter/Project-Everward", "exact_head": "keep-me"}
    continuation_path.write_text(json.dumps(original), encoding="utf-8")
    run_watchdog_iteration(lanes=stalled(), **call_args)
    run_watchdog_iteration(lanes=stalled(), **call_args)
    assert json.loads(continuation_path.read_text()) == original
