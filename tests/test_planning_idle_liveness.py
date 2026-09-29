import json
from datetime import UTC, datetime, timedelta

import project_orchestrator.planning_dispatch as planning


def write_decision(root, invocation, rows):
    (root / "dispatch-decision.json").write_text(
        json.dumps({"invocation_id": invocation, "decisions": rows}),
        encoding="utf-8",
    )


def test_two_identical_authorized_no_slice_cycles_become_p1_defect(tmp_path):
    rows = [{
        "repository": "jweter/knowledge-engine-core",
        "product": "knowledge-engine",
        "head_sha": "abc",
        "outcome": "NO_AUTHORIZED_SLICE",
        "reason": "NO_WORK",
    }]
    write_decision(tmp_path, "cycle-1", rows)
    first = planning.record_eligible_idle_streaks(
        tmp_path, ["knowledge-engine"], current_invocation_id="cycle-1"
    )
    assert first["status"] == "OK"
    assert first["products"]["knowledge-engine"]["streak"] == 1

    write_decision(tmp_path, "cycle-2", rows)
    second = planning.record_eligible_idle_streaks(
        tmp_path, ["knowledge-engine"], current_invocation_id="cycle-2"
    )
    assert second["status"] == "P1_LIVENESS_DEFECT"
    assert second["p1_liveness_defects"] == ["knowledge-engine"]


def test_head_change_resets_idle_streak(tmp_path):
    write_decision(tmp_path, "a", [{
        "repository": "jweter/knowledge-engine-core",
        "product": "knowledge-engine",
        "head_sha": "old",
        "outcome": "NO_AUTHORIZED_SLICE",
    }])
    planning.record_eligible_idle_streaks(
        tmp_path, ["knowledge-engine"], current_invocation_id="a"
    )
    write_decision(tmp_path, "b", [{
        "repository": "jweter/knowledge-engine-core",
        "product": "knowledge-engine",
        "head_sha": "new",
        "outcome": "NO_AUTHORIZED_SLICE",
    }])
    result = planning.record_eligible_idle_streaks(
        tmp_path, ["knowledge-engine"], current_invocation_id="b"
    )
    assert result["status"] == "OK"
    assert result["products"]["knowledge-engine"]["streak"] == 1


def test_active_grant_clears_idle_streak(tmp_path):
    write_decision(tmp_path, "a", [{
        "repository": "jweter/Project-Everward",
        "product": "everward",
        "head_sha": "abc",
        "outcome": "NO_AUTHORIZED_SLICE",
    }])
    planning.record_eligible_idle_streaks(
        tmp_path, ["everward"], current_invocation_id="a"
    )
    (tmp_path / "hands-off-grant-lifecycle.json").write_text(
        json.dumps({"grants": {"g": {
            "state": "CLAIMED",
            "repository": "jweter/Project-Everward",
            "last_heartbeat_at": datetime.now(UTC).isoformat(),
        }}}),
        encoding="utf-8",
    )
    write_decision(tmp_path, "b", [{
        "repository": "jweter/Project-Everward",
        "product": "everward",
        "head_sha": "abc",
        "outcome": "NO_AUTHORIZED_SLICE",
    }])
    result = planning.record_eligible_idle_streaks(
        tmp_path, ["everward"], current_invocation_id="b"
    )
    assert result["products"]["everward"]["reason"] == "ACTIVE_WORK"
    assert result["products"]["everward"]["streak"] == 0


def test_stale_claimed_grant_does_not_mask_repeated_idle_defect(tmp_path):
    rows = [{
        "repository": "jweter/rocksmith-cdlc-generator",
        "product": "rocksmith",
        "head_sha": "abc",
        "outcome": "NO_AUTHORIZED_SLICE",
    }]
    stale = (datetime.now(UTC) - timedelta(minutes=20)).isoformat()
    (tmp_path / "hands-off-grant-lifecycle.json").write_text(
        json.dumps({"grants": {"old": {
            "state": "CLAIMED",
            "repository": "jweter/rocksmith-cdlc-generator",
            "last_heartbeat_at": stale,
        }}}),
        encoding="utf-8",
    )
    write_decision(tmp_path, "one", rows)
    first = planning.record_eligible_idle_streaks(
        tmp_path, ["rocksmith"], current_invocation_id="one"
    )
    assert first["products"]["rocksmith"]["reason"] == "NO_AUTHORIZED_SLICE"
    assert first["products"]["rocksmith"]["streak"] == 1

    write_decision(tmp_path, "two", rows)
    second = planning.record_eligible_idle_streaks(
        tmp_path, ["rocksmith"], current_invocation_id="two"
    )
    assert second["status"] == "P1_LIVENESS_DEFECT"
    assert second["p1_liveness_defects"] == ["rocksmith"]
