"""Fail-closed source-identity checks at the existing planning selection boundary."""

import json

import pytest

from project_orchestrator.planning_dispatch import select_execution_target

ENGINE = "jweter/project-orchestrator"
VOICE = "jweter/VoiceStudio"
HEAD = "a" * 40


def activation():
    return {"products": {
        "iron-god": {"repositories": {ENGINE: HEAD}},
        "voicestudio": {"repositories": {VOICE: HEAD}},
    }}


def test_unavailable_engine_source_is_rejected_before_slice_load(tmp_path):
    calls = []

    def load(*args):
        calls.append(args)
        return {"status": "READY", "slice_id": "must-not-run"}

    with pytest.raises(RuntimeError, match="ACTIVATION_COMMIT_NOT_LOCAL"):
        select_execution_target(
            tmp_path, activation(), ["iron-god"],
            load_execution_slice_fn=load,
            conflict_check_fn=lambda *args: False,
            source_identity_fn=lambda *args: "ACTIVATION_COMMIT_NOT_LOCAL",
        )
    assert calls == []
    decision = json.loads((tmp_path / "dispatch-decision.json").read_text())
    assert decision["selected_repository"] is None
    assert decision["decisions"][0]["outcome"] == "SOURCE_IDENTITY_BLOCKED"
    assert not (tmp_path / "execution-target-cursor.json").exists()


def test_source_failure_rotates_to_independent_product(tmp_path):
    calls = []

    def load(root, repo, head):
        calls.append(repo)
        return {"status": "READY", "slice_id": "valid-product-slice"}

    selected = select_execution_target(
        tmp_path, activation(), ["iron-god", "voicestudio"],
        load_execution_slice_fn=load,
        conflict_check_fn=lambda *args: False,
        source_identity_fn=lambda root, repo, head: (
            "ACTIVATION_COMMIT_NOT_LOCAL" if repo == ENGINE else None
        ),
    )
    assert selected == (VOICE, HEAD)
    assert calls == [VOICE]
    decision = json.loads((tmp_path / "dispatch-decision.json").read_text())
    assert decision["selected_repository"] == VOICE
    assert decision["decisions"][0]["outcome"] == "SOURCE_IDENTITY_BLOCKED"


def test_source_checker_error_fails_closed(tmp_path):
    def broken(*args):
        raise OSError("read-only Git probe unavailable")

    with pytest.raises(RuntimeError, match="SOURCE_IDENTITY_CHECK_FAILED"):
        select_execution_target(
            tmp_path, activation(), ["iron-god"],
            load_execution_slice_fn=lambda *args: pytest.fail("must not load slice"),
            conflict_check_fn=lambda *args: False,
            source_identity_fn=broken,
        )
