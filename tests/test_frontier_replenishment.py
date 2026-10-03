import json

from project_orchestrator.frontier_replenishment import replenish_frontiers


def test_continuation_request_replenishes_from_compiled_authority(tmp_path):
    (tmp_path / "continuation-request.json").write_text(json.dumps({
        "repository": "o/r", "exact_head": "old", "reason": "REFRESH_AUTHORIZED_CANDIDATES"
    }))

    def compile_fn(_workspace, _state):
        return {"repositories": {"o/r": {"status": "COMPILED", "exact_head": "new"}}}

    def load_fn(_state, repo, head):
        assert (repo, head) == ("o/r", "new")
        return {"slice_id": "next", "capability_id": "cap-next"}

    result = replenish_frontiers(tmp_path / "ws", tmp_path, compile_fn=compile_fn, load_slice_fn=load_fn)
    assert result["repositories"]["o/r"]["status"] == "REPLENISHED"
    assert result["repositories"]["o/r"]["slice_id"] == "next"
    assert not (tmp_path / "continuation-request.json").exists()


def test_direction_request_can_self_heal_when_current_authority_now_has_slice(tmp_path):
    root = tmp_path / "direction-requests"; root.mkdir()
    path = root / "o__r.json"
    path.write_text(json.dumps({"status": "DIRECTION_REQUIRED", "repository": "o/r", "exact_head": "old"}))

    def compile_fn(_workspace, _state):
        return {"repositories": {"o/r": {"status": "COMPILED", "exact_head": "new"}}}

    result = replenish_frontiers(
        tmp_path / "ws", tmp_path, compile_fn=compile_fn,
        load_slice_fn=lambda *_: {"slice_id": "s2", "capability_id": "c2"},
    )
    assert result["repositories"]["o/r"]["status"] == "REPLENISHED"
    assert not path.exists()


def test_missing_or_insufficient_authority_stays_direction_required(tmp_path):
    (tmp_path / "continuation-request.json").write_text(json.dumps({
        "repository": "o/r", "exact_head": "head"
    }))
    result = replenish_frontiers(
        tmp_path / "ws", tmp_path,
        compile_fn=lambda *_: {"repositories": {"o/r": {"status": "DIRECTION_REQUIRED", "reason": "NO_DECLARATION"}}},
        load_slice_fn=lambda *_: (_ for _ in ()).throw(AssertionError("must not derive without authority")),
    )
    assert result["repositories"]["o/r"]["status"] == "DIRECTION_REQUIRED"
    assert (tmp_path / "continuation-request.json").exists()


def test_no_requests_does_not_compile(tmp_path):
    result = replenish_frontiers(
        tmp_path / "ws", tmp_path,
        compile_fn=lambda *_: (_ for _ in ()).throw(AssertionError("must stay idle")),
        load_slice_fn=lambda *_: {},
    )
    assert result["status"] == "NO_REQUESTS"
