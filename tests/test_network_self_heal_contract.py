from pathlib import Path
from types import SimpleNamespace

from scripts import network_self_heal as network


def test_reboot_latch_blocks_repeat_reboots(tmp_path, monkeypatch):
    commands = []

    monkeypatch.setattr(network, "snapshot", lambda _root: None)
    monkeypatch.setattr(network, "human_mode", lambda *_args: False)
    monkeypatch.setattr(network, "internet_ok", lambda: False)
    monkeypatch.setattr(network.time, "sleep", lambda _seconds: None)

    def fake_run(command):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(network, "run", fake_run)

    assert network.recover(tmp_path, allow_reboot=True) is False
    assert network.reboot_latched(tmp_path)
    assert sum(command[0] == "shutdown" for command in commands) == 1

    assert network.recover(tmp_path, allow_reboot=True) is False
    assert sum(command[0] == "shutdown" for command in commands) == 1
    assert "REBOOT_SUPPRESSED_COOLDOWN" in (tmp_path / "network-recovery.jsonl").read_text()


def test_reboot_latch_clears_only_on_explicit_clear(tmp_path):
    network.set_reboot_latch(tmp_path)
    assert network.reboot_latched(tmp_path)
    network.clear_reboot_latch(tmp_path, "CONNECTIVITY_CONFIRMED")
    assert not network.reboot_latched(tmp_path)


def test_human_mode_suppresses_recovery(tmp_path, monkeypatch):
    monkeypatch.setattr(network, "snapshot", lambda _root: None)
    (tmp_path / "human-mode.json").write_text("{}")
    assert network.recover(tmp_path, allow_reboot=True) is False
    assert not network.reboot_latched(tmp_path)


def test_canonical_human_control_marker_suppresses_recovery(tmp_path, monkeypatch):
    state_root = tmp_path / "repo-state"
    local_app_data = tmp_path / "LocalAppData"
    human_root = local_app_data / "ExponentialEngine" / "flow-state"
    human_root.mkdir(parents=True)
    (human_root / "human-mode.json").write_text("{}")
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    assert network.human_mode(state_root)


def test_guardian_singleton_is_atomic_and_os_released(tmp_path):
    first = network.acquire_guardian(tmp_path)
    assert first is not None
    try:
        second = network.acquire_guardian(tmp_path)
        assert second is None
    finally:
        first.close()

    replacement = network.acquire_guardian(tmp_path)
    assert replacement is not None
    replacement.close()


def test_dns_failure_is_not_reported_as_healthy(monkeypatch):
    def fail_dns(*_args, **_kwargs):
        raise network.socket.gaierror("dns unavailable")

    monkeypatch.setattr(network.socket, "getaddrinfo", fail_dns)
    monkeypatch.setattr(
        network.socket,
        "create_connection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("IP probe must not mask DNS failure")),
    )
    assert network.internet_ok() is False


def test_explicit_interactive_human_control_root_is_honored(tmp_path, monkeypatch):
    state_root = tmp_path / "repo-state"
    human_root = tmp_path / "interactive-flow-state"
    human_root.mkdir(parents=True)
    (human_root / "human-mode.json").write_text("{}")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert network.human_mode(state_root, human_root)


def test_queued_reboot_is_aborted_when_human_mode_appears(tmp_path, monkeypatch):
    commands = []
    checks = iter([False, False, True])

    monkeypatch.setattr(network, "snapshot", lambda _root: None)
    monkeypatch.setattr(network, "human_mode", lambda *_args: next(checks))
    monkeypatch.setattr(network, "internet_ok", lambda: False)
    monkeypatch.setattr(network.time, "sleep", lambda _seconds: None)

    def fake_run(command):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(network, "run", fake_run)
    assert network.recover(tmp_path, allow_reboot=True, human_control_root=tmp_path / "human") is False
    assert ["shutdown", "/a"] in commands
    assert any(command[:2] == ["shutdown", "/r"] for command in commands)
    assert "REBOOT_ABORTED_HUMAN_MODE" in (tmp_path / "network-recovery.jsonl").read_text()
