from pathlib import Path

import project_orchestrator.grant_lifecycle as lifecycle


def test_worker_lifecycle_keeps_long_contention_window(monkeypatch, tmp_path: Path):
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
    assert seen["replace_attempts"] > lifecycle.RECOVERY_LIFECYCLE_REPLACE_ATTEMPTS


def test_recovery_context_uses_short_bounded_window(monkeypatch, tmp_path: Path):
    seen = []

    def fake_atomic(_path, _payload, *, replace_attempts):
        seen.append(replace_attempts)

    monkeypatch.setattr(lifecycle, "atomic_json", fake_atomic)
    path = tmp_path / "hands-off-grant-lifecycle.json"
    with lifecycle.recovery_lifecycle_write_budget():
        lifecycle.atomic_lifecycle_json(path, {"schema_version": 1, "grants": {}})

    assert seen == [lifecycle.RECOVERY_LIFECYCLE_REPLACE_ATTEMPTS]
    assert 5 <= seen[0] <= 20


def test_recovery_budget_does_not_leak_to_later_worker_writes(monkeypatch, tmp_path: Path):
    seen = []

    def fake_atomic(_path, _payload, *, replace_attempts):
        seen.append(replace_attempts)

    monkeypatch.setattr(lifecycle, "atomic_json", fake_atomic)
    path = tmp_path / "hands-off-grant-lifecycle.json"
    with lifecycle.recovery_lifecycle_write_budget():
        lifecycle.atomic_lifecycle_json(path, {})
    lifecycle.atomic_lifecycle_json(path, {})

    assert seen == [
        lifecycle.RECOVERY_LIFECYCLE_REPLACE_ATTEMPTS,
        lifecycle.LIFECYCLE_REPLACE_ATTEMPTS,
    ]
