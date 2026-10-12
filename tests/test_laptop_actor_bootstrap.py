import importlib.util
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "laptop_actor_bootstrap.py"
SPEC = importlib.util.spec_from_file_location("laptop_actor_bootstrap", MODULE_PATH)
assert SPEC and SPEC.loader
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)

SHA = "a" * 40


def completed(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_update_actor_checkout_tracks_exact_origin_main(monkeypatch, tmp_path):
    actor = tmp_path / "actor"
    (actor / ".git").mkdir(parents=True)
    calls = []

    def fake_run(cmd, cwd=None):
        calls.append((cmd, cwd))
        if cmd[:3] == ["git", "rev-parse", "origin/main"]:
            return completed(stdout=SHA + "\n")
        return completed()

    monkeypatch.setattr(bootstrap, "run", fake_run)
    assert bootstrap.update_actor_checkout(actor) == SHA
    assert ["git", "fetch", "origin", "main"] in [item[0] for item in calls]
    assert ["git", "reset", "--hard", SHA] in [item[0] for item in calls]


def test_update_rejects_non_exact_remote_sha(monkeypatch, tmp_path):
    actor = tmp_path / "actor"
    (actor / ".git").mkdir(parents=True)

    def fake_run(cmd, cwd=None):
        if cmd[:3] == ["git", "rev-parse", "origin/main"]:
            return completed(stdout="main\n")
        return completed()

    monkeypatch.setattr(bootstrap, "run", fake_run)
    with pytest.raises(RuntimeError):
        bootstrap.update_actor_checkout(actor)


def test_bridge_requires_actor_owned_scripts(tmp_path):
    actor = tmp_path / "actor"
    actor.mkdir()
    with pytest.raises(RuntimeError):
        bootstrap.execute_bridge_once(actor, tmp_path / "queue", tmp_path / "work")


def test_idle_session_does_not_start_before_threshold(monkeypatch, tmp_path):
    monkeypatch.setattr(bootstrap, "windows_idle_seconds", lambda: 120.0)
    monkeypatch.setattr(
        bootstrap,
        "update_actor_checkout",
        lambda *_: pytest.fail("checkout must not update while user is active"),
    )
    assert bootstrap.run_idle_session(tmp_path, tmp_path, tmp_path, 300, 60) == 0


def test_idle_session_runs_repeated_cycles_then_yields_on_activity(monkeypatch, tmp_path):
    idle_values = iter([600.0, 600.0, 600.0, 5.0])
    cycles = []
    updates = []
    sleeps = []
    monkeypatch.setattr(bootstrap, "windows_idle_seconds", lambda: next(idle_values))
    monkeypatch.setattr(bootstrap, "update_actor_checkout", lambda *_: updates.append(True) or SHA)
    monkeypatch.setattr(
        bootstrap,
        "execute_bridge_once",
        lambda *_: cycles.append(True) or 0,
    )
    monkeypatch.setattr(bootstrap.time, "sleep", lambda value: sleeps.append(value))
    monkeypatch.setattr(
        bootstrap,
        "system_resource_snapshot",
        lambda: {"memory_percent": 10.0, "disk_free_gb": 100.0},
    )

    assert bootstrap.run_idle_session(tmp_path, tmp_path, tmp_path, 300, 60) == 0
    assert len(updates) == 1
    assert len(cycles) == 2
    assert sleeps == [60, 60]


def test_idle_session_stops_on_bridge_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(bootstrap, "windows_idle_seconds", lambda: 600.0)
    monkeypatch.setattr(bootstrap, "update_actor_checkout", lambda *_: SHA)
    monkeypatch.setattr(bootstrap, "execute_bridge_once", lambda *_: 2)
    monkeypatch.setattr(bootstrap.time, "sleep", lambda _: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        bootstrap.run_idle_session(tmp_path, tmp_path, tmp_path, 300, 60)


def test_installer_uses_dedicated_checkout_and_idle_aware_repeating_task():
    installer = (Path(__file__).resolve().parents[1] / "scripts" / "install_laptop_actor.ps1").read_text(encoding="utf-8")
    assert "actor-repo" in installer
    assert "git clone" not in installer.lower() or "clone --branch main --single-branch" in installer
    assert "reset --hard origin/main" in installer
    assert "laptop_actor_bootstrap.py" in installer
    assert "RepetitionInterval (New-TimeSpan -Minutes 5)" in installer
    assert "--min-idle-seconds 300" in installer
    assert "--poll-seconds 60" in installer
    assert "checks for work every 60 seconds while idle" in installer
    assert "SELF-UPDATING" in installer


def test_single_instance_lock_is_exclusive(tmp_path):
    state = tmp_path / "state"
    first = bootstrap.acquire_single_instance(state)
    assert first is not None
    try:
        assert bootstrap.acquire_single_instance(state) is None
    finally:
        bootstrap.release_single_instance(state, first)
    # Persistent kernel lock file is safe; an old PID is never authority.
    assert (state / "actor.lock").exists()
    second = bootstrap.acquire_single_instance(state)
    assert second is not None
    bootstrap.release_single_instance(state, second)


def test_stale_pid_lock_is_reclaimed_after_owner_crash(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "actor.lock").write_text("6368", encoding="ascii")
    lease = bootstrap.acquire_single_instance(state)
    assert lease is not None
    try:
        # Windows locks the byte against readers as well as writers.
        assert bootstrap.acquire_single_instance(state) is None
    finally:
        bootstrap.release_single_instance(state, lease)
    assert (state / "actor.lock").read_text(encoding="ascii") == str(os.getpid())


def test_emit_state_writes_structured_jsonl(tmp_path):
    state = tmp_path / "state"
    bootstrap.emit_state(state, "EXECUTING", queue_latency_seconds=12)
    import json
    payload = json.loads((state / "actor-telemetry.jsonl").read_text().strip())
    assert payload["state"] == "EXECUTING"
    assert payload["queue_latency_seconds"] == 12
    assert payload["timestamp_utc"]


def test_idle_session_uses_bounded_backoff_then_recovers(monkeypatch, tmp_path):
    idle_values = iter([600.0, 600.0, 600.0, 5.0])
    results = iter([2, 0])
    sleeps = []
    monkeypatch.setattr(bootstrap, "windows_idle_seconds", lambda: next(idle_values))
    monkeypatch.setattr(bootstrap, "update_actor_checkout", lambda *_: SHA)
    monkeypatch.setattr(bootstrap, "execute_bridge_once", lambda *_: next(results))
    monkeypatch.setattr(bootstrap.time, "sleep", lambda value: sleeps.append(value))
    assert bootstrap.run_idle_session(tmp_path, tmp_path, tmp_path, 300, 60, tmp_path / "state", 120) == 0
    assert sleeps == [60, 60]


def test_resource_budget_blocks_high_memory():
    assert not bootstrap.resources_allow_execution({"memory_percent": 91.0, "disk_free_gb": 100.0}, max_memory_percent=85.0, min_disk_free_gb=10.0)


def test_resource_budget_blocks_low_disk():
    assert not bootstrap.resources_allow_execution({"memory_percent": 40.0, "disk_free_gb": 4.0}, max_memory_percent=85.0, min_disk_free_gb=10.0)


def test_resource_budget_allows_safe_capacity():
    assert bootstrap.resources_allow_execution({"memory_percent": 40.0, "disk_free_gb": 100.0}, max_memory_percent=85.0, min_disk_free_gb=10.0)


def test_maintenance_marker_blocks_actor_before_checkout(monkeypatch, tmp_path):
    local = tmp_path / "local"
    marker = local / "ExponentialEngine" / "steward" / "maintenance.json"
    marker.parent.mkdir(parents=True)
    marker.write_text('{"status":"STARTUP_SANITY"}', encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(bootstrap, "windows_idle_seconds", lambda: 600.0)
    monkeypatch.setattr(
        bootstrap,
        "update_actor_checkout",
        lambda *_: pytest.fail("maintenance mode must block checkout/update"),
    )

    assert (
        bootstrap.run_idle_session(
            tmp_path,
            tmp_path / "queue",
            tmp_path / "workspace",
            300,
            60,
            tmp_path / "state",
        )
        == 0
    )


def test_daily_hygiene_is_bounded_to_owned_transient_files(monkeypatch, tmp_path):
    local = tmp_path / "local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    state = local / "ExponentialEngine" / "queue" / "state"
    logs = local / "PortfolioIdleWorker" / "logs"
    logs.mkdir(parents=True)
    state.mkdir(parents=True)

    old_log = logs / "old.log"
    old_log.write_text("old", encoding="utf-8")
    protected = logs / "keep.txt"
    protected.write_text("do not delete", encoding="utf-8")
    recent_log = logs / "recent.log"
    recent_log.write_text("recent", encoding="utf-8")
    old_epoch = datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp()
    os.utime(old_log, (old_epoch, old_epoch))
    os.utime(protected, (old_epoch, old_epoch))

    now = datetime(2026, 9, 22, 18, 0, tzinfo=timezone.utc)
    result = bootstrap.run_daily_hygiene(state, now=now)

    assert result["status"] == "COMPLETED"
    assert not old_log.exists()
    assert protected.exists()
    assert recent_log.exists()
    assert (state / "steward-hygiene.json").exists()

    second = bootstrap.run_daily_hygiene(state, now=now)
    assert second["status"] == "SKIPPED_RECENT"
