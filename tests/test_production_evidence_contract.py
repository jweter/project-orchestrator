import json
from datetime import UTC, datetime
from pathlib import Path

from project_orchestrator.planning_dispatch import productive_liveness
from scripts import autonomous_slice_builder as builder


def _write_lifecycle(root: Path, record: dict) -> None:
    (root / "hands-off-grant-lifecycle.json").write_text(
        json.dumps({"grants": {"g1": record}}), encoding="utf-8"
    )


def test_null_head_completion_is_not_productive(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    _write_lifecycle(tmp_path, {
        "state": "COMPLETED",
        "repository": "jweter/Project-Everward",
        "exact_head": "a" * 40,
        "head_sha": None,
        "finished_at": now.isoformat(),
        "execution_stage": "EXECUTION_FINISHED",
    })
    result = productive_liveness(tmp_path, ["everward"], now=now)
    assert result["productive"] is False
    assert result["status"] == "ELIGIBLE_IDLE"
    assert result["grant_evidence"][0]["produced_candidate"] is False


def test_candidate_head_completion_is_productive(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    _write_lifecycle(tmp_path, {
        "state": "COMPLETED",
        "repository": "jweter/Project-Everward",
        "exact_head": "a" * 40,
        "head_sha": "b" * 40,
        "finished_at": now.isoformat(),
        "execution_stage": "PR_OPEN",
    })
    result = productive_liveness(tmp_path, ["everward"], now=now)
    assert result["productive"] is True
    assert result["status"] == "PRODUCTIVE"
    assert result["grant_evidence"][0]["produced_candidate"] is True


def test_active_worker_is_activity_not_productivity(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    _write_lifecycle(tmp_path, {
        "state": "RUNNING",
        "repository": "jweter/VoiceStudio",
        "exact_head": "a" * 40,
        "last_heartbeat_at": now.isoformat(),
        "execution_stage": "EXECUTING",
    })
    result = productive_liveness(tmp_path, ["voicestudio"], now=now)
    assert result["productive"] is False
    assert result["status"] == "ACTIVE_WORK"


def test_no_op_finishes_as_nonproductive_failure(tmp_path: Path, monkeypatch) -> None:
    grant = tmp_path / "grant.json"
    grant.write_text(json.dumps({"grant_id": "g1"}), encoding="utf-8")
    captured = {}

    monkeypatch.setattr(builder, "lifecycle_path_for_grant", lambda _: tmp_path / "lifecycle.json")

    def mutate(_path, _grant_id, update):
        record = {"state": "RUNNING"}
        update(record)
        captured.update(record)
        return True

    monkeypatch.setattr(builder, "mutate_grant_lifecycle", mutate)
    builder.finish_grant_lifecycle(grant, {"status": "NO_OP", "repository": "jweter/VoiceStudio"})
    assert captured["state"] == "FAILED"
    assert captured["failure_class"] == "NONPRODUCTIVE_COMPLETION"
    assert captured["result_status"] == "NO_OP"
