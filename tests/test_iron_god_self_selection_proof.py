"""Proof that Iron God can select its own repository without launching workers."""
import json
import pytest
from project_orchestrator.planning_dispatch import select_execution_target

ENGINE = "jweter/project-orchestrator"
HEAD = "a" * 40


def test_self_selection_exact_head(tmp_path):
    activation = {"products": {"iron-god": {"repositories": {ENGINE: HEAD}}}}
    observed = []

    def load(root, repo, head):
        observed.append((repo, head))
        return {"status": "READY", "slice_id": "iron-god-self-development"}

    selected = select_execution_target(
        tmp_path, activation, ["iron-god"],
        load_execution_slice_fn=load,
        conflict_check_fn=lambda *args: False,
    )
    assert selected == (ENGINE, HEAD)
    assert observed == [(ENGINE, HEAD)]
    decision = json.loads((tmp_path / "dispatch-decision.json").read_text())
    assert decision["selected_repository"] == ENGINE
    assert decision["selected_slice_id"] == "iron-god-self-development"


def test_self_selection_refuses_occupied_slice(tmp_path):
    activation = {"products": {"iron-god": {"repositories": {ENGINE: HEAD}}}}
    with pytest.raises(RuntimeError, match="NO_READY_EXECUTION_SLICE"):
        select_execution_target(
            tmp_path, activation, ["iron-god"],
            load_execution_slice_fn=lambda *args: {"status": "READY", "slice_id": "owned"},
            conflict_check_fn=lambda *args: {"grant_id": "active-claim", "state": "RUNNING"},
        )
    decision = json.loads((tmp_path / "dispatch-decision.json").read_text())
    assert decision["selected_repository"] is None
    assert decision["decisions"][0]["outcome"] == "ALREADY_OWNED"
