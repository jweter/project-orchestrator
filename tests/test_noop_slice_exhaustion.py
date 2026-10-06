import json

import pytest

import scripts.continuous_flow_supervisor as supervisor


def _write_graph(root, *, objective="do bounded work"):
    graph = {
        "schema_version": 1,
        "repository": "o/r",
        "capabilities": [
            {
                "id": "cap",
                "capability": "bounded capability",
                "state": "PLANNED",
                "depends_on": [],
                "completion_evidence": ["merged verified implementation"],
                "slices": [
                    {
                        "id": "slice-v1",
                        "state": "PLANNED",
                        "objective": objective,
                        "acceptance": ["verified"],
                        "allowed_paths": ["src"],
                        "forbidden_paths": [".github"],
                    }
                ],
            }
        ],
        "roadmap": ["cap"],
    }
    path = root / "capability-graphs" / "o__r.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(graph), encoding="utf-8")


def _write_noop_lifecycle(root, *, count=2, objective="do bounded work"):
    authority = {
        "capability_id": "cap",
        "slice_id": "slice-v1",
        "repository": "o/r",
        "exact_head": "head",
        "objective": objective,
        "acceptance": ["verified"],
        "allowed_paths": ["src"],
        "forbidden_paths": [".github"],
    }
    grants = {}
    for index in range(count):
        grants[f"g{index}"] = {
            "state": "COMPLETED",
            "issued_at": f"2026-09-23T00:0{index}:00+00:00",
            "finished_at": f"2026-09-23T00:0{index}:30+00:00",
            "result_status": "NO_OP",
            "execution_slice": authority,
        }
    (root / "hands-off-grant-lifecycle.json").write_text(
        json.dumps({"grants": grants}), encoding="utf-8"
    )


def test_one_noop_does_not_exhaust_slice(tmp_path):
    _write_graph(tmp_path)
    _write_noop_lifecycle(tmp_path, count=1)
    selected = supervisor.load_execution_slice(tmp_path, "o/r", "head")
    assert selected["slice_id"] == "slice-v1"


def test_repeated_noop_exhausts_exact_slice_and_requests_replan(tmp_path):
    _write_graph(tmp_path)
    _write_noop_lifecycle(tmp_path, count=2)

    with pytest.raises(RuntimeError, match="EXECUTION_SLICE_EXHAUSTED:o/r:slice-v1"):
        supervisor.load_execution_slice(tmp_path, "o/r", "head")

    request = json.loads(
        (tmp_path / "direction-requests" / "o__r.json").read_text(encoding="utf-8")
    )
    issue = request["issues"][0]
    assert request["reason"] == "REPEATED_NO_OP_SLICE_EXHAUSTED"
    assert issue["next_action"] == "RECOMPILE_CAPABILITY_GRAPH_FROM_CURRENT_REPOSITORY_AUTHORITY"
    assert issue["threshold"] == 2
    assert [item["grant_id"] for item in issue["recent_attempts"]] == ["g0", "g1"]


def test_failed_nonproductive_noops_still_exhaust_exact_slice(tmp_path):
    _write_graph(tmp_path)
    _write_noop_lifecycle(tmp_path, count=2)
    lifecycle_path = tmp_path / "hands-off-grant-lifecycle.json"
    payload = json.loads(lifecycle_path.read_text(encoding="utf-8"))
    for record in payload["grants"].values():
        record["state"] = "FAILED"
        record["failure_class"] = "NONPRODUCTIVE_COMPLETION"
    lifecycle_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuntimeError, match="EXECUTION_SLICE_EXHAUSTED:o/r:slice-v1"):
        supervisor.load_execution_slice(tmp_path, "o/r", "head")


def test_materially_changed_slice_authority_gets_fresh_attempt(tmp_path):
    _write_graph(tmp_path, objective="new bounded work")
    _write_noop_lifecycle(tmp_path, count=2, objective="old bounded work")

    selected = supervisor.load_execution_slice(tmp_path, "o/r", "head")
    assert selected["objective"] == "new bounded work"


def test_productive_attempt_breaks_consecutive_noop_exhaustion(tmp_path):
    _write_graph(tmp_path)
    _write_noop_lifecycle(tmp_path, count=2)
    lifecycle_path = tmp_path / "hands-off-grant-lifecycle.json"
    payload = json.loads(lifecycle_path.read_text(encoding="utf-8"))
    payload["grants"]["g2"] = {
        **payload["grants"]["g1"],
        "issued_at": "2026-09-23T00:02:00+00:00",
        "finished_at": "2026-09-23T00:02:30+00:00",
        "result_status": "PR_OPEN",
    }
    lifecycle_path.write_text(json.dumps(payload), encoding="utf-8")

    selected = supervisor.load_execution_slice(tmp_path, "o/r", "head")
    assert selected["slice_id"] == "slice-v1"
