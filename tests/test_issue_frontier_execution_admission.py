"""GitHub issue frontier is not execution authority until fully scoped."""

from __future__ import annotations

import json
from project_orchestrator.planning_dispatch import ready_task_batch

REPO = "jweter/Project-Everward"
HEAD = "3e6ade3a3968fdafadc0848e651b6f6d731a0644"
GRAPH_TASK = {
    "repository": REPO,
    "exact_head": HEAD,
    "capability_id": "phase2-one-probe-development-access",
    "slice_id": "phase2-low-spec-play-mode-next",
    "objective": "Build bounded low-spec presentation tests",
    "acceptance": ["Deterministic tests pass"],
    "allowed_paths": ["Source/**", "tests/**"],
}
ISSUE = {
    "product": "everward",
    "repository": REPO,
    "work_id": "issue:jweter/Project-Everward#159",
    "title": "Outstanding UE acceptance umbrella",
    "next_gate": "TRIAGE",
    "eligible": True,
    "blocked": False,
    "allowed_paths": [],
}


def batch(tmp_path, issues, graph_slices=None):
    state = tmp_path
    (state / "issue-work-frontier.json").write_text(
        json.dumps({"ranked_actionable": issues}), encoding="utf-8"
    )
    graphs = state / "capability-graphs"
    graphs.mkdir(exist_ok=True)
    (graphs / "jweter__Project-Everward.json").write_text("{}", encoding="utf-8")
    activation = {"products": {"everward": {"repositories": {REPO: HEAD}}}}
    return ready_task_batch(
        state,
        activation,
        "everward",
        2,
        derive_slices_fn=lambda *_args, **_kwargs: {
            "status": "READY",
            "slices": graph_slices if graph_slices is not None else [dict(GRAPH_TASK)],
        },
        conflict_check_fn=lambda *_args: None,
    )


def test_real_world_triage_issue_is_not_a_planned_executable_slice(tmp_path):
    result = batch(tmp_path, [ISSUE])
    assert len(result) == 1
    assert result[0][2]["slice_id"] == GRAPH_TASK["slice_id"]
    assert result[0][2]["repository"] == REPO
    assert result[0][2]["exact_head"] == HEAD
    deferred = json.loads(
        (tmp_path / "issue-execution-deferrals" / "everward.json").read_text()
    )
    assert deferred["count"] == 1
    assert deferred["deferred"][0]["work_id"] == ISSUE["work_id"]
    assert deferred["deferred"][0]["reason"] == "ISSUE_EXECUTION_AUTHORITY_REQUIRED"


def test_triage_stays_deferred_even_if_path_and_acceptance_are_filled(tmp_path):
    issue = {**ISSUE, "allowed_paths": ["Source/**"], "acceptance": ["Tests pass"]}
    result = batch(tmp_path, [issue], graph_slices=[])
    assert result == []


def test_explicit_bounded_issue_gets_same_exact_head_as_grant_authority(tmp_path):
    issue = {
        **ISSUE,
        "next_gate": "IMPLEMENT",
        "allowed_paths": ["Source/**"],
        "acceptance": ["Executable regression test covers the bug"],
        "forbidden_paths": [".github/**"],
    }
    result = batch(tmp_path, [issue], graph_slices=[])
    assert len(result) == 1
    repo, head, task = result[0]
    assert task["repository"] == repo == REPO
    assert task["exact_head"] == head == HEAD
    assert task["acceptance"] == issue["acceptance"]
    assert task["allowed_paths"] == issue["allowed_paths"]
    assert task["forbidden_paths"] == issue["forbidden_paths"]
    assert task["source_kind"] == "ISSUE"
    assert (
        json.loads(
            (tmp_path / "issue-execution-deferrals" / "everward.json").read_text()
        )["count"]
        == 0
    )


def test_missing_acceptance_or_paths_cannot_be_invented(tmp_path):
    issue = {**ISSUE, "next_gate": "IMPLEMENT"}
    for missing in ("acceptance", "allowed_paths"):
        candidate = {
            **issue,
            "acceptance": ["tests pass"],
            "allowed_paths": ["tests/**"],
        }
        candidate.pop(missing)
        result = batch(tmp_path, [candidate], graph_slices=[])
        assert result == []
        record = json.loads(
            (tmp_path / "issue-execution-deferrals" / "everward.json").read_text()
        )
        assert record["count"] == 1


def test_blocked_or_ineligible_issue_never_dispatches(tmp_path):
    for key, value in (("eligible", False), ("blocked", True)):
        issue = {
            **ISSUE,
            "next_gate": "IMPLEMENT",
            "allowed_paths": ["tests/**"],
            "acceptance": ["test passes"],
            key: value,
        }
        assert batch(tmp_path, [issue], graph_slices=[]) == []


def test_issue_with_stale_declared_head_never_borrows_current_activation(tmp_path):
    issue = {
        **ISSUE,
        "next_gate": "IMPLEMENT",
        "allowed_paths": ["tests/**"],
        "acceptance": ["Current-head tests pass"],
        "exact_head": "0" * 40,
    }
    assert batch(tmp_path, [issue], graph_slices=[]) == []
    deferred = json.loads(
        (tmp_path / "issue-execution-deferrals" / "everward.json").read_text()
    )
    assert deferred["count"] == 1
