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


def test_unpinned_actor_refuses_origin_main_fallback(monkeypatch, tmp_path):
    actor = tmp_path / "actor"
    (actor / ".git").mkdir(parents=True)
    monkeypatch.delenv("IRON_GOD_ACCEPTED_REPO", raising=False)
    monkeypatch.delenv("IRON_GOD_ACCEPTED_SHA", raising=False)
    monkeypatch.setattr(bootstrap, "run", lambda *a, **k: pytest.fail(
        "no GitHub fetch or reset allowed without a local accepted release"
    ))
    with pytest.raises(RuntimeError, match="LOCAL_ACCEPTED_RELEASE_REQUIRED"):
        bootstrap.update_actor_checkout(actor)


def test_local_release_rejects_non_exact_pin_before_git(monkeypatch, tmp_path):
    actor = tmp_path / "actor"
    (actor / ".git").mkdir(parents=True)
    monkeypatch.setenv("IRON_GOD_ACCEPTED_REPO", str(tmp_path / "source"))
    monkeypatch.setenv("IRON_GOD_ACCEPTED_SHA", "main")
    monkeypatch.setattr(bootstrap, "run", lambda *a, **k: pytest.fail(
        "invalid pin must never reach Git"
    ))
    with pytest.raises(RuntimeError, match="LOCAL_ACCEPTED_SHA_INVALID"):
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


def test_retired_github_actor_installer_cannot_reactivate_stale_main():
    installer = (Path(__file__).resolve().parents[1] / "scripts" / "install_laptop_actor.ps1").read_text(encoding="utf-8")
    assert "LEGACY_GITHUB_ACTOR_INSTALLER_DISABLED_USE_PINNED_LOCAL_RELEASE" in installer
    assert "throw " in installer
    assert "fetch origin main" not in installer
    assert "reset --hard origin/main" not in installer
    assert "clone --branch main" not in installer
    assert "Start-ScheduledTask" not in installer
    assert "Register-ScheduledTask" not in installer


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

def test_local_exact_head_promotion(monkeypatch, tmp_path):
    actor = tmp_path / 'actor'
    source = tmp_path / 'accepted'
    (actor / '.git').mkdir(parents=True)
    (source / '.git').mkdir(parents=True)
    monkeypatch.setenv('IRON_GOD_ACCEPTED_REPO', str(source))
    monkeypatch.setenv('IRON_GOD_ACCEPTED_SHA', SHA)
    calls = []

    def fake_run(cmd, cwd=None):
        calls.append((cmd, cwd))
        if cmd == ['git', 'rev-parse', 'HEAD']:
            return completed(stdout=SHA + '\n')
        if cmd == ['git', 'rev-parse', 'FETCH_HEAD']:
            return completed(stdout=SHA + '\n')
        return completed()

    monkeypatch.setattr(bootstrap, 'run', fake_run)
    assert bootstrap.update_actor_checkout(actor) == SHA
    assert ['git', 'fetch', '--no-tags', str(source), SHA] in [c[0] for c in calls]
    assert not any(c[0] == ['git', 'fetch', 'origin', 'main'] for c in calls)


def test_local_promotion_rejects_mismatched_head_before_fetch(monkeypatch, tmp_path):
    actor = tmp_path / 'actor'
    source = tmp_path / 'accepted'
    (actor / '.git').mkdir(parents=True)
    (source / '.git').mkdir(parents=True)
    monkeypatch.setenv('IRON_GOD_ACCEPTED_REPO', str(source))
    monkeypatch.setenv('IRON_GOD_ACCEPTED_SHA', SHA)
    calls = []

    def fake_run(cmd, cwd=None):
        calls.append(cmd)
        if cmd == ['git', 'rev-parse', 'HEAD']:
            return completed(stdout='b' * 40 + '\n')
        return completed()

    monkeypatch.setattr(bootstrap, 'run', fake_run)
    with pytest.raises(RuntimeError, match='clean exact HEAD'):
        bootstrap.update_actor_checkout(actor)
    assert not any(cmd[:2] == ['git', 'fetch'] for cmd in calls)
    assert not any(cmd[:3] == ['git', 'reset', '--hard'] for cmd in calls)


def test_local_promotion_rejects_partial_configuration(monkeypatch, tmp_path):
    actor = tmp_path / 'actor'
    (actor / '.git').mkdir(parents=True)
    monkeypatch.setenv('IRON_GOD_ACCEPTED_REPO', str(tmp_path))
    monkeypatch.delenv('IRON_GOD_ACCEPTED_SHA', raising=False)
    monkeypatch.setattr(bootstrap, 'run', lambda *args: pytest.fail('no Git mutation allowed'))
    with pytest.raises(RuntimeError, match='both repository and exact SHA'):
        bootstrap.update_actor_checkout(actor)


def test_unpinned_origin_main_can_never_roll_back_or_advance_actor(monkeypatch, tmp_path):
    actor = tmp_path / "actor"
    (actor / ".git").mkdir(parents=True)
    monkeypatch.delenv("IRON_GOD_ACCEPTED_REPO", raising=False)
    monkeypatch.delenv("IRON_GOD_ACCEPTED_SHA", raising=False)
    monkeypatch.setattr(bootstrap, "run", lambda *a, **k: pytest.fail(
        "unverified origin must not be fetched"
    ))
    with pytest.raises(RuntimeError, match="LOCAL_ACCEPTED_RELEASE_REQUIRED"):
        bootstrap.update_actor_checkout(actor)


def test_actor_update_refuses_to_erase_dirty_actor_before_reset(monkeypatch, tmp_path):
    actor = tmp_path / "actor"
    source = tmp_path / "accepted"
    (actor / ".git").mkdir(parents=True)
    (source / ".git").mkdir(parents=True)
    monkeypatch.setenv("IRON_GOD_ACCEPTED_REPO", str(source))
    monkeypatch.setenv("IRON_GOD_ACCEPTED_SHA", SHA)
    calls = []

    def fake_run(cmd, cwd=None):
        calls.append(cmd)
        if cmd == ["git", "rev-parse", "HEAD"]:
            return completed(stdout=(SHA if cwd == source else "b" * 40) + "\n")
        if cmd == ["git", "rev-parse", "FETCH_HEAD"]:
            return completed(stdout=SHA + "\n")
        if cmd == ["git", "status", "--porcelain"] and cwd == actor:
            return completed(stdout=" M unique-user-work.txt\n")
        return completed()

    monkeypatch.setattr(bootstrap, "run", fake_run)
    with pytest.raises(RuntimeError, match="actor checkout is dirty"):
        bootstrap.update_actor_checkout(actor)
    assert not any(cmd[:3] == ["git", "reset", "--hard"] for cmd in calls)


def test_pinned_actor_update_is_noop_if_revision_already_loaded(monkeypatch, tmp_path):
    actor = tmp_path / "actor"
    source = tmp_path / "accepted"
    (actor / ".git").mkdir(parents=True)
    (source / ".git").mkdir(parents=True)
    monkeypatch.setenv("IRON_GOD_ACCEPTED_REPO", str(source))
    monkeypatch.setenv("IRON_GOD_ACCEPTED_SHA", SHA)
    calls = []

    def fake_run(cmd, cwd=None):
        calls.append(cmd)
        if cmd in (["git", "rev-parse", "HEAD"], ["git", "rev-parse", "FETCH_HEAD"]):
            return completed(stdout=SHA + "\n")
        return completed()

    monkeypatch.setattr(bootstrap, "run", fake_run)
    assert bootstrap.update_actor_checkout(actor) == SHA
    assert not any(cmd[:3] == ["git", "reset", "--hard"] for cmd in calls)


def test_local_exact_pin_allows_intentional_divergent_upgrade(monkeypatch, tmp_path):
    actor = tmp_path / "actor"
    source = tmp_path / "accepted"
    (actor / ".git").mkdir(parents=True)
    (source / ".git").mkdir(parents=True)
    monkeypatch.setenv("IRON_GOD_ACCEPTED_REPO", str(source))
    monkeypatch.setenv("IRON_GOD_ACCEPTED_SHA", SHA)
    calls = []
    did_reset = {"value": False}

    def fake_run(cmd, cwd=None):
        calls.append((cmd, cwd))
        if cmd[:3] == ["git", "reset", "--hard"]:
            did_reset["value"] = True
        if cmd == ["git", "rev-parse", "HEAD"]:
            return completed(stdout=(SHA if cwd == source.resolve() or did_reset["value"] else "b" * 40) + "\n")
        if cmd == ["git", "rev-parse", "FETCH_HEAD"]:
            return completed(stdout=SHA + "\n")
        return completed()

    monkeypatch.setattr(bootstrap, "run", fake_run)
    assert bootstrap.update_actor_checkout(actor) == SHA
    assert ["git", "reset", "--hard", SHA] in [c[0] for c in calls]
    assert not any(c[0][:3] == ["git", "merge-base", "--is-ancestor"] for c in calls)


def test_unpinned_origin_main_must_not_replace_advanced_actor(monkeypatch, tmp_path):
    actor = tmp_path / "actor"
    (actor / ".git").mkdir(parents=True)
    monkeypatch.delenv("IRON_GOD_ACCEPTED_REPO", raising=False)
    monkeypatch.delenv("IRON_GOD_ACCEPTED_SHA", raising=False)
    monkeypatch.setattr(bootstrap, "run", lambda *a, **k: pytest.fail(
        "a stale origin may never replace the actor"
    ))
    with pytest.raises(RuntimeError, match="LOCAL_ACCEPTED_RELEASE_REQUIRED"):
        bootstrap.update_actor_checkout(actor)


def test_pinned_local_actor_refuses_preexisting_checkout_changes(monkeypatch, tmp_path):
    actor = tmp_path / "actor"
    source = tmp_path / "accepted"
    (actor / ".git").mkdir(parents=True)
    (source / ".git").mkdir(parents=True)
    monkeypatch.setenv("IRON_GOD_ACCEPTED_REPO", str(source))
    monkeypatch.setenv("IRON_GOD_ACCEPTED_SHA", SHA)
    calls = []

    def fake_run(cmd, cwd=None):
        calls.append(cmd)
        if cmd == ["git", "rev-parse", "HEAD"]:
            return completed(stdout=(SHA if cwd == source else "b" * 40) + "\n")
        if cmd == ["git", "rev-parse", "FETCH_HEAD"]:
            return completed(stdout=SHA + "\n")
        if cmd == ["git", "status", "--porcelain"] and cwd == actor:
            return completed(stdout=" M scripts/continuous_flow_supervisor.py\n")
        return completed()

    monkeypatch.setattr(bootstrap, "run", fake_run)
    with pytest.raises(RuntimeError, match="actor checkout is dirty"):
        bootstrap.update_actor_checkout(actor)
    assert not any(cmd[:3] == ["git", "reset", "--hard"] for cmd in calls)


def test_pinned_local_release_can_update_divergent_clean_actor(monkeypatch, tmp_path):
    actor = tmp_path / "actor"
    source = tmp_path / "source"
    (actor / ".git").mkdir(parents=True)
    (source / ".git").mkdir(parents=True)
    monkeypatch.setenv("IRON_GOD_ACCEPTED_REPO", str(source))
    monkeypatch.setenv("IRON_GOD_ACCEPTED_SHA", SHA)
    calls = []
    did_reset = {"value": False}

    def fake_run(cmd, cwd=None):
        calls.append((cmd, cwd))
        if cmd[:3] == ["git", "reset", "--hard"]:
            did_reset["value"] = True
        if cmd == ["git", "rev-parse", "HEAD"]:
            return completed(stdout=(SHA if cwd == source or did_reset["value"] else "b" * 40) + "\n")
        if cmd == ["git", "rev-parse", "FETCH_HEAD"]:
            return completed(stdout=SHA + "\n")
        return completed()

    monkeypatch.setattr(bootstrap, "run", fake_run)
    assert bootstrap.update_actor_checkout(actor) == SHA
    assert ["git", "reset", "--hard", SHA] in [c[0] for c in calls]
    assert not any(c[0][:2] == ["git", "merge-base"] for c in calls)

def test_local_source_requires_absolute_path_before_git(monkeypatch, tmp_path):
    actor = tmp_path / "actor"
    (actor / ".git").mkdir(parents=True)
    monkeypatch.setenv("IRON_GOD_ACCEPTED_REPO", "relative-release")
    monkeypatch.setenv("IRON_GOD_ACCEPTED_SHA", SHA)
    monkeypatch.setattr(bootstrap, "run", lambda *a, **k: pytest.fail("no git"))
    with pytest.raises(RuntimeError, match="LOCAL_ACCEPTED_SOURCE_NOT_ABSOLUTE"):
        bootstrap.update_actor_checkout(actor)


def test_actor_and_source_tree_cannot_overlap(monkeypatch, tmp_path):
    actor = tmp_path / "actor"
    (actor / ".git").mkdir(parents=True)
    monkeypatch.setenv("IRON_GOD_ACCEPTED_REPO", str(actor))
    monkeypatch.setenv("IRON_GOD_ACCEPTED_SHA", SHA)
    monkeypatch.setattr(bootstrap, "run", lambda *a, **k: pytest.fail("no reset"))
    with pytest.raises(RuntimeError, match="LOCAL_ACTOR_SOURCE_OVERLAP"):
        bootstrap.update_actor_checkout(actor)


def test_local_release_with_untracked_source_files_refuses_promotion(monkeypatch, tmp_path):
    actor = tmp_path / "actor"
    source = tmp_path / "release"
    (actor / ".git").mkdir(parents=True)
    (source / ".git").mkdir(parents=True)
    monkeypatch.setenv("IRON_GOD_ACCEPTED_REPO", str(source))
    monkeypatch.setenv("IRON_GOD_ACCEPTED_SHA", SHA)
    calls = []

    def fake_run(cmd, cwd=None):
        calls.append(cmd)
        if cmd == ["git", "rev-parse", "HEAD"]:
            return completed(stdout=SHA + "\n")
        if cmd == ["git", "status", "--porcelain", "--untracked-files=normal"]:
            return completed(stdout="?? untracked-control-code.py\n")
        return completed()

    monkeypatch.setattr(bootstrap, "run", fake_run)
    with pytest.raises(RuntimeError, match="clean exact HEAD"):
        bootstrap.update_actor_checkout(actor)
    assert not any(cmd[:2] == ["git", "fetch"] for cmd in calls)
    assert not any(cmd[:3] == ["git", "reset", "--hard"] for cmd in calls)


def test_fetched_sha_must_equal_local_accepted_sha_before_reset(monkeypatch, tmp_path):
    actor = tmp_path / "actor"
    source = tmp_path / "release"
    (actor / ".git").mkdir(parents=True)
    (source / ".git").mkdir(parents=True)
    monkeypatch.setenv("IRON_GOD_ACCEPTED_REPO", str(source))
    monkeypatch.setenv("IRON_GOD_ACCEPTED_SHA", SHA)
    calls = []

    def fake_run(cmd, cwd=None):
        calls.append(cmd)
        if cmd == ["git", "rev-parse", "HEAD"]:
            return completed(stdout=SHA + "\n")
        if cmd == ["git", "rev-parse", "FETCH_HEAD"]:
            return completed(stdout="b" * 40 + "\n")
        return completed()

    monkeypatch.setattr(bootstrap, "run", fake_run)
    with pytest.raises(RuntimeError, match="LOCAL_FETCHED_HEAD_MISMATCH"):
        bootstrap.update_actor_checkout(actor)
    assert not any(cmd[:3] == ["git", "reset", "--hard"] for cmd in calls)


def test_post_reset_actor_head_must_match_local_release(monkeypatch, tmp_path):
    actor = tmp_path / "actor"
    source = tmp_path / "release"
    (actor / ".git").mkdir(parents=True)
    (source / ".git").mkdir(parents=True)
    monkeypatch.setenv("IRON_GOD_ACCEPTED_REPO", str(source))
    monkeypatch.setenv("IRON_GOD_ACCEPTED_SHA", SHA)

    def fake_run(cmd, cwd=None):
        if cmd == ["git", "rev-parse", "HEAD"]:
            return completed(stdout=(SHA if cwd == source else "b" * 40) + "\n")
        if cmd == ["git", "rev-parse", "FETCH_HEAD"]:
            return completed(stdout=SHA + "\n")
        return completed()

    monkeypatch.setattr(bootstrap, "run", fake_run)
    with pytest.raises(RuntimeError, match="LOCAL_ACTOR_POST_RESET_HEAD_MISMATCH"):
        bootstrap.update_actor_checkout(actor)


def test_local_promotion_real_git_moves_actor_to_current_sha_without_origin_main(
    monkeypatch, tmp_path
):
    import subprocess

    def git(repo, *args):
        result = subprocess.run(
            ["git", "-C", str(repo), *args], text=True, capture_output=True, check=True
        )
        return result.stdout.strip()

    source = tmp_path / "accepted"
    source.mkdir()
    git(source, "init", "-q", "-b", "main")
    git(source, "config", "user.email", "test@example.invalid")
    git(source, "config", "user.name", "Test")
    (source / "version.txt").write_text("old\n", encoding="utf-8")
    git(source, "add", "version.txt")
    git(source, "commit", "-q", "-m", "old accepted baseline")
    old_sha = git(source, "rev-parse", "HEAD")

    actor = tmp_path / "runtime-actor"
    subprocess.run(
        ["git", "clone", "-q", str(source), str(actor)],
        text=True, capture_output=True, check=True,
    )
    assert git(actor, "rev-parse", "HEAD") == old_sha
    (source / "version.txt").write_text("new\n", encoding="utf-8")
    git(source, "add", "version.txt")
    git(source, "commit", "-q", "-m", "locally accepted improvement")
    new_sha = git(source, "rev-parse", "HEAD")
    assert new_sha != old_sha

    monkeypatch.setenv("IRON_GOD_ACCEPTED_REPO", str(source))
    monkeypatch.setenv("IRON_GOD_ACCEPTED_SHA", new_sha)
    assert bootstrap.update_actor_checkout(actor) == new_sha
    assert git(actor, "rev-parse", "HEAD") == new_sha
    assert (actor / "version.txt").read_text(encoding="utf-8") == "new\n"
    # The remote-tracking branch is still stale: correct behavior must not
    # be achieved by falling back to origin/main.
    assert git(actor, "rev-parse", "refs/remotes/origin/main") == old_sha
