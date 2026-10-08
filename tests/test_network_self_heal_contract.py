from pathlib import Path
from types import SimpleNamespace

from scripts import network_self_heal as network


def test_reboot_latch_blocks_repeat_reboots(tmp_path, monkeypatch):
    commands = []

    monkeypatch.setattr(network, "snapshot", lambda _root: None)
    monkeypatch.setattr(network, "human_mode", lambda _root: False)
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


def test_mid_recovery_human_handoff_interrupts_network_operations(tmp_path, monkeypatch):
    commands = []
    # Keep the actual laptop owner marker out of this isolated fixture.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-localappdata"))
    monkeypatch.setattr(network, "snapshot", lambda _root: None)
    monkeypatch.setattr(network, "internet_ok", lambda: False)
    monkeypatch.setattr(network.time, "sleep", lambda _seconds: None)

    def fake_run(command):
        commands.append(command)
        (tmp_path / "human-mode.json").write_text('{"enabled":true}')
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(network, "run", fake_run)
    assert network.recover(tmp_path, allow_reboot=True) is False
    assert len(commands) == 1
    assert commands[0][:2] == ["netsh", "wlan"]
    assert "RECOVERY_INTERRUPTED_HUMAN_MODE" in (tmp_path / "network-recovery.jsonl").read_text()
    assert not network.reboot_latched(tmp_path)


def test_preexisting_human_marker_prevents_even_a_recovery_snapshot(tmp_path, monkeypatch):
    (tmp_path / "human-mode.json").write_text("{}")
    monkeypatch.setattr(network, "snapshot", lambda _root: (_ for _ in ()).throw(AssertionError("snapshot ran")))
    assert network.recover(tmp_path, allow_reboot=True) is False