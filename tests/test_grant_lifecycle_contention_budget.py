from pathlib import Path

import project_orchestrator.grant_lifecycle as lifecycle


def test_lifecycle_replace_window_is_bounded_for_supervisor_liveness(monkeypatch, tmp_path: Path):
    seen = {}

    def fake_atomic(path, payload, *, replace_attempts):
        seen["path"] = path
        seen["payload"] = payload
        seen["replace_attempts"] = replace_attempts

    monkeypatch.setattr(lifecycle, "atomic_json", fake_atomic)
    path = tmp_path / "hands-off-grant-lifecycle.json"
    lifecycle.atomic_lifecycle_json(path, {"schema_version": 1, "grants": {}})

    assert seen["path"] == path
    assert seen["replace_attempts"] == lifecycle.LIFECYCLE_REPLACE_ATTEMPTS
    assert 5 <= seen["replace_attempts"] <= 20
