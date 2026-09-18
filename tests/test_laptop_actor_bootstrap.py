import importlib.util
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

    assert bootstrap.run_idle_session(tmp_path, tmp_path, tmp_path, 300, 60) == 0
    assert len(updates) == 1
    assert len(cycles) == 2
    assert sleeps == [60, 60]


def test_idle_session_stops_on_bridge_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(bootstrap, "windows_idle_seconds", lambda: 600.0)
    monkeypatch.setattr(bootstrap, "update_actor_checkout", lambda *_: SHA)
    monkeypatch.setattr(bootstrap, "execute_bridge_once", lambda *_: 2)
    assert bootstrap.run_idle_session(tmp_path, tmp_path, tmp_path, 300, 60) == 2


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
