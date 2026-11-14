"""No test may close or launch a real program on the machine (Dev Mode does both for the engine)."""

from __future__ import annotations

import pytest

from project_orchestrator import iron_god_dev_mode as dev
from project_orchestrator import iron_god_product_lane as product_lane

ON = {"auto": True, "stall_minutes": 0, "exit_idle_minutes": 15, "cooldown_minutes": 30, "max_per_day": 2, "margin_gib": 0.3}


@pytest.fixture()
def no_subprocess(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("a test started a real process from Dev Mode")

    monkeypatch.setattr(dev.subprocess, "run", forbidden)


def test_a_test_sees_no_real_processes_and_cannot_close_or_launch_one(no_subprocess):
    # tests/conftest.py does this for every test; the preflight runs this on the owner's own desktop
    assert dev.snapshot_processes() == []
    assert dev.plan() == []
    with pytest.raises(AssertionError, match="real program"):
        dev.system_closer(4242, force=False)
    with pytest.raises(AssertionError, match="real program"):
        dev.launcher("C:/Program Files/Google/Chrome/Application/chrome.exe", [])


def test_entering_without_an_injected_closer_uses_the_guarded_one(tmp_path, no_subprocess):
    # the default used to be bound when the function was defined, so replacing system_closer protected nothing
    browser = dev.plan(rows=[{"pid": 4242, "ppid": 1, "name": "chrome", "path": "C:/x/chrome.exe", "cmd": "chrome",
                              "mib": 900, "window": True}])
    assert [group.action for group in browser] == [dev.CLOSE]
    with pytest.raises(AssertionError, match="real program"):
        dev.enter(browser, tmp_path / dev.STATE_NAME, confirmed=True, sleep=lambda _seconds: None, grace=0)


def test_restoring_without_an_injected_launcher_uses_the_guarded_one(tmp_path, no_subprocess):
    state_path = tmp_path / dev.STATE_NAME
    program = tmp_path / "chrome.exe"
    program.write_bytes(b"")                                          # restore only relaunches a program file that exists
    dev.enter(dev.plan(rows=[{"pid": 4242, "ppid": 1, "name": "chrome", "path": str(program), "cmd": "chrome",
                              "mib": 900, "window": True}]),
              state_path, confirmed=True, closer=lambda pid, *, force: True, alive=lambda pids: set(),
              sleep=lambda _seconds: None, grace=0)
    with pytest.raises(AssertionError, match="real program"):
        dev.restore(state_path, running=lambda: set())


def test_the_lanes_memory_wait_with_the_shipped_policy_closes_nothing_in_a_test(tmp_path, no_subprocess):
    # the path the preflight took: a lane test reports a memory wait, the shipped policy says enter at once
    class Lane:
        state_root = tmp_path
        root = tmp_path
        dev_mode_config = ON
        planning_memory_wait = None

    product_lane._dev_mode_tick(Lane(), {"status": "WAITING_FOR_MEMORY", "work_waiting": True,
                                         "route": {"required_gib": 5.3, "available_gib": 3.0}})
    assert not (tmp_path / dev.STATE_NAME).exists()


def test_no_test_can_reach_the_claude_login_or_start_the_in_engine_capable_runner(tmp_path) -> None:
    """2026-11-14: with the shipped policy a lane test started a real runner process. It must fail loudly instead."""
    import pytest

    from project_orchestrator import iron_god_capable_builder as capable
    from project_orchestrator import iron_god_product_lane as lane

    assert capable.cli_path() is None
    request = {"id": "RM-x", "repository": "jweter/x", "route": {"builder": "CAPABLE"}}
    verdict = capable.decide(request, lane_root=tmp_path, enabled=True, transport=capable.ENGINE_TRANSPORT, environ={})
    assert verdict["reason"] == "CAPABLE_BUILDER_UNCONFIGURED"
    with pytest.raises(AssertionError, match="real program"):
        lane.spawn_capable_engine(tmp_path, "0" * 40, tmp_path / "job.json", tmp_path / "log.txt")
