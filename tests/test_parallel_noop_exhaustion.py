"""The parallel worker scheduler must honor the existing NO_OP exhaustion gate."""
import json

from project_orchestrator.planning_dispatch import ready_task_batch


REPO = "jweter/VoiceStudio"
HEAD = "a" * 40
SLICE = {
    "repository": REPO,
    "capability_id": "singing-s1-capability-schema",
    "slice_id": "s1-capability-schema-catalogue-filtering",
    "objective": "Add singing capability catalogue filtering",
    "acceptance": ["Engine filtering distinguishes singing from speech"],
    "allowed_paths": ["backend/**", "tests/**"],
    "forbidden_paths": [".env"],
}


def _grant(sequence, status, task=SLICE):
    return {
        "state": "FAILED" if status == "NO_OP" else "COMPLETED",
        "result_status": status,
        "issued_at": f"2026-10-18T20:{sequence:02d}:00+00:00",
        "execution_slice": task,
    }


def _batch(tmp_path, grants, slices):
    graph_dir = tmp_path / "capability-graphs"
    graph_dir.mkdir()
    (graph_dir / "jweter__VoiceStudio.json").write_text("{}", encoding="utf-8")
    (tmp_path / "hands-off-grant-lifecycle.json").write_text(
        json.dumps({"grants": grants}), encoding="utf-8"
    )
    activation = {"products": {"voicestudio": {"repositories": {REPO: HEAD}}}}
    return ready_task_batch(
        tmp_path, activation, "voicestudio", 1,
        derive_slices_fn=lambda *args, **kwargs: {"status": "READY", "slices": slices},
        conflict_check_fn=lambda *args: None,
    )


def test_parallel_scheduler_rejects_exhausted_slice_despite_new_claim(tmp_path):
    selected = _batch(tmp_path, {
        "first": _grant(1, "NO_OP"),
        "second": _grant(2, "NO_OP"),
        "pending": {
            **_grant(3, None),
            "state": "CLAIMED",
        },
    }, [SLICE])
    assert selected == []
    request = json.loads(
        (tmp_path / "direction-requests" / "jweter__VoiceStudio.json").read_text()
    )
    assert request["reason"] == "REPEATED_NO_OP_SLICE_EXHAUSTED"
    assert request["issues"][0]["slice_id"] == SLICE["slice_id"]


def test_parallel_scheduler_can_select_different_unexhausted_slice(tmp_path):
    alternative = {**SLICE, "slice_id": "s2-new-slice", "objective": "Different implementation"}
    selected = _batch(tmp_path, {
        "first": _grant(1, "NO_OP"),
        "second": _grant(2, "NO_OP"),
    }, [SLICE, alternative])
    assert len(selected) == 1
    assert selected[0][2]["slice_id"] == "s2-new-slice"


def test_parallel_scheduler_allows_first_retry(tmp_path):
    selected = _batch(tmp_path, {"first": _grant(1, "NO_OP")}, [SLICE])
    assert len(selected) == 1
    assert selected[0][2]["slice_id"] == SLICE["slice_id"]
