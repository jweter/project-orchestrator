from project_orchestrator.unattended_cycle import run_unattended_cycle


def inventory():
    return {
        "projects": [
            {"id": "knowledge-engine", "repositories": [{"name": "jweter/knowledge-engine-core"}]},
            {"id": "rocksmith", "repositories": [{"name": "jweter/rocksmith-cdlc-generator"}]},
            {"id": "everward", "repositories": [{"name": "jweter/Project-Everward"}]},
            {"id": "voicestudio", "repositories": [{"name": "jweter/VoiceStudio"}]},
        ]
    }


def snapshot():
    return {
        "repositories": [
            {"repository": "jweter/knowledge-engine-core", "default_head": "ke", "active_work": []},
            {"repository": "jweter/rocksmith-cdlc-generator", "default_head": "rs", "active_work": []},
            {"repository": "jweter/Project-Everward", "default_head": "ev", "active_work": []},
            {"repository": "jweter/VoiceStudio", "default_head": "vs", "active_work": []},
        ]
    }


def candidates():
    return [
        {
            "project_id": "everward", "repository": "jweter/Project-Everward",
            "base_sha": "ev", "branch": "feature/a", "title": "A",
            "priority": 1, "paths": ["Source/A.cpp"],
        },
        {
            "project_id": "everward", "repository": "jweter/Project-Everward",
            "base_sha": "ev", "branch": "feature/b", "title": "B",
            "priority": 2, "paths": ["Source/B.cpp"],
        },
    ]


def test_unattended_cycle_records_shadow_before_dispatch(tmp_path):
    seen = []
    result = run_unattended_cycle(
        inventory(), snapshot(), candidates(),
        run_id="run-1",
        ledger_path=tmp_path / "jev.jsonl",
        dispatch=lambda lane: seen.append(lane["lane_id"]),
    )
    assert result["jev_runtime"]["capture"]["created"] == 1
    assert result["dispatched_lane_ids"] == seen
    assert len(seen) == 1


def test_repeated_cycle_is_idempotent_and_reconciles_terminal_result(tmp_path):
    path = tmp_path / "jev.jsonl"
    first = run_unattended_cycle(
        inventory(), snapshot(), candidates(),
        run_id="run-1", ledger_path=path,
    )
    lane = first["foreman_plan"]["projects"][2]["selected_lane"]["lane_id"]

    second = run_unattended_cycle(
        inventory(), snapshot(), candidates(),
        run_id="run-1", ledger_path=path,
        execution_records=[
            {
                "project_id": "everward",
                "lane_id": lane,
                "state": "MERGED",
                "head_sha": "abc",
            }
        ],
    )
    assert second["jev_runtime"]["capture"]["created"] == 0
    assert second["jev_runtime"]["reconciliation"]["closed"] == 1


def test_no_dispatch_callback_means_no_execution_side_effect(tmp_path):
    result = run_unattended_cycle(
        inventory(), snapshot(), candidates(),
        run_id="run-2", ledger_path=tmp_path / "jev.jsonl",
    )
    assert result["dispatched_lane_ids"] == []
    assert result["jev_runtime"]["execution_authority"] is False
