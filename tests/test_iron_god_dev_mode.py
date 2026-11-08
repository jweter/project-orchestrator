from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from project_orchestrator import iron_god_dev_mode as dev

ROOT = Path(__file__).resolve().parents[1]


def row(pid, name, ppid=1, mib=100, window=False, path=None, cmd=None):
    return {"pid": pid, "ppid": ppid, "name": name, "mib": mib, "window": window,
            "path": path if path is not None else rf"C:\Apps\{name}\{name}.exe", "cmd": cmd or f'"C:\\Apps\\{name}\\{name}.exe"'}


MACHINE = [
    row(10, "explorer", ppid=0, window=True, path=r"C:\WINDOWS\Explorer.EXE"),
    row(20, "claude", ppid=10, path=r"C:\Users\jerem\.local\bin\claude.exe"),
    row(21, "python", ppid=20, path=r"C:\Users\jerem\AppData\Local\Programs\Python\Python312\python.exe"),
    row(30, "chrome", ppid=10, mib=500, window=True, path=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        cmd='"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"'),
    row(31, "chrome", ppid=30, mib=400, path=r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    row(32, "chrome", ppid=30, mib=300, path=r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    row(40, "msedgewebview2", ppid=10, mib=300, path=r"C:\Program Files (x86)\Microsoft\EdgeWebView\msedgewebview2.exe"),
    row(50, "OneDrive", ppid=10, mib=240, path=r"C:\Program Files\Microsoft OneDrive\OneDrive.exe"),
    row(60, "NortonUI", ppid=10, mib=240, path=r"C:\Program Files\Norton\Suite\NortonUI.exe"),
    row(61, "tailscale-ipn", ppid=10, mib=36, path=r"C:\Program Files\Tailscale\tailscale-ipn.exe"),
    row(62, "ollama", ppid=10, mib=900, path=r"C:\Users\jerem\AppData\Local\Programs\Ollama\ollama.exe"),
    row(70, "MyIndieGame", ppid=10, mib=800, window=True),
    row(80, "svchost", ppid=0, mib=160, path=r"C:\WINDOWS\system32\svchost.exe"),
]


def by_name(groups):
    return {g.name.lower(): g for g in groups}


def test_the_plan_closes_only_the_named_hogs_and_protects_everything_essential() -> None:
    groups = by_name(dev.plan(MACHINE, self_pid=21))
    for name in ("chrome", "msedgewebview2", "onedrive"):
        assert groups[name].action == dev.CLOSE, name
    for name in ("explorer", "claude", "python", "nortonui", "tailscale-ipn", "ollama", "svchost"):
        assert groups[name].action == dev.KEEP, name
    assert groups["myindiegame"].action == dev.UNCLASSIFIED            # nobody classified it: left running, only reported
    chrome = groups["chrome"]
    assert chrome.mib == 1200 and len(chrome.pids) == 3 and chrome.windows == 1 and chrome.guarded is True
    assert chrome.root_pid == 30 and "chrome.exe" in chrome.root_command    # the top process is the one relaunched on restore
    assert groups["msedgewebview2"].guarded is False                    # window-less helper: may be force-ended
    assert dev.summary(list(groups.values()))["reclaimable_mib"] == 1200 + 300 + 240


@pytest.mark.parametrize("name", ["explorer", "claude", "ollama", "python", "pythonw", "svchost", "NortonUI", "tailscale-ipn",
                                  "powershell", "node", "git", "bash", "UnrealEditor", "msbuild", "RtkAudUService64", "dwm", "System"])
def test_nothing_can_put_a_protected_program_on_the_close_list_not_even_the_owners_config(name, tmp_path: Path) -> None:
    (tmp_path / dev.CONFIG_NAME).write_text(json.dumps({"close_extra": [name]}), encoding="utf-8")
    action, reason, _ = dev.classify(name, rf"C:\Apps\{name}.exe", config=dev.load_config(tmp_path))
    assert action == dev.KEEP and "protected" in reason


def test_anything_in_a_windows_driver_or_engine_location_is_protected_whatever_its_name() -> None:
    for path in (r"C:\Windows\System32\Whatever.exe", r"C:\Windows\SystemApps\X\y.exe", r"C:\Users\jerem\AppData\Local\ExponentialEngine\z.exe",
                 r"C:\Windows\System32\DriverStore\FileRepository\hp\foo.exe", r"C:\Program Files\Norton\Suite\q.exe"):
        assert dev.classify("chrome", path)[0] == dev.KEEP, path


def test_the_owners_close_extra_can_add_a_program_but_only_with_a_polite_close(tmp_path: Path) -> None:
    (tmp_path / dev.CONFIG_NAME).write_text(json.dumps({"close_extra": ["MyIndieGame"], "keep_extra": ["OneDrive"]}), encoding="utf-8")
    config = dev.load_config(tmp_path)
    assert dev.classify("MyIndieGame", r"C:\Games\g.exe", config=config) == (dev.CLOSE, "on the Dev Mode close list", True)
    assert dev.classify("OneDrive", r"C:\Program Files\Microsoft OneDrive\OneDrive.exe", config=config)[0] == dev.KEEP
    (tmp_path / dev.CONFIG_NAME).write_text("not json {", encoding="utf-8")
    assert dev.load_config(tmp_path) == {"close_extra": [], "keep_extra": []}                # a broken config means defaults


def test_a_program_in_the_commands_own_process_tree_is_never_closed_even_if_it_is_on_the_close_list() -> None:
    rows = [row(10, "chrome", ppid=0, window=True), row(20, "bash", ppid=10), row(21, "python", ppid=20)]
    group = by_name(dev.plan(rows, self_pid=21))["chrome"]
    assert group.action == dev.KEEP and "process tree" in group.reason


class Fake:
    """A pretend machine: tracks which pids are alive and what each close request did."""

    def __init__(self, stubborn=()):
        self.alive_set = {r["pid"] for r in MACHINE}
        self.stubborn = set(stubborn)                  # apps that ignore a polite close (e.g. a save prompt is open)
        self.calls = []

    def closer(self, pid, *, force):
        self.calls.append((pid, force))
        if force:
            self.alive_set.discard(pid)
            return True
        if pid in self.stubborn:
            return False
        window_pids = {r["pid"] for r in MACHINE if r["window"]}
        if pid in window_pids:
            self.alive_set.discard(pid)
            return True
        return False                                    # a window-less helper ignores a polite request

    def alive(self, pids):
        return {p for p in pids if p in self.alive_set}


def run_enter(tmp_path, fake, **kw):
    groups = dev.plan(MACHINE, self_pid=21)
    return dev.enter(groups, tmp_path / dev.STATE_NAME, confirmed=True, closer=fake.closer, alive=fake.alive,
                     sleep=lambda s: None, grace=0.0, free_gib=lambda: 9.0, **kw)


def test_entering_requires_explicit_confirmation(tmp_path: Path) -> None:
    with pytest.raises(PermissionError):
        dev.enter(dev.plan(MACHINE, self_pid=21), tmp_path / dev.STATE_NAME, confirmed=False)


def test_enter_closes_politely_forces_only_window_less_helpers_and_records_everything(tmp_path: Path) -> None:
    fake = Fake()
    state = run_enter(tmp_path, fake)
    closed = {c["name"] for c in state["closed"]}
    assert {"chrome", "msedgewebview2", "OneDrive"} <= closed
    assert 20 not in {pid for pid, _ in fake.calls} and 62 not in {pid for pid, _ in fake.calls}      # claude, ollama untouched
    assert all(pid in {30, 31, 32, 40, 50} for pid, _ in fake.calls)                                  # only the planned programs
    assert fake.alive_set >= {10, 20, 21, 60, 61, 62, 70, 80}                                         # everything else still runs
    forced = set(state["forced_helper_pids"])
    assert 40 in forced and 50 in forced and not ({30} & forced)                                      # helpers yes, the browser window no
    assert state["active"] is True and state["free_gib_before"] == 9.0
    assert json.loads((tmp_path / dev.STATE_NAME).read_text(encoding="utf-8"))["closed"] == state["closed"]


def test_an_app_that_ignores_the_polite_request_is_never_forced_and_is_reported(tmp_path: Path) -> None:
    fake = Fake(stubborn={30})
    state = run_enter(tmp_path, fake)
    assert 30 in fake.alive_set and (30, True) not in fake.calls                       # the browser asked to save: respected
    refused = {r["name"] for r in state["refused"]}
    assert "chrome" in refused and "not forced" in state["refused"][0]["why"]


def test_entering_twice_is_refused_until_restored(tmp_path: Path) -> None:
    run_enter(tmp_path, Fake())
    with pytest.raises(RuntimeError, match="already active"):
        run_enter(tmp_path, Fake())


def test_restore_relaunches_the_main_program_with_session_restore_and_is_safe_to_repeat(tmp_path: Path) -> None:
    run_enter(tmp_path, Fake())
    started = []
    for record in json.loads((tmp_path / dev.STATE_NAME).read_text(encoding="utf-8"))["closed"]:
        record["path"] = str(tmp_path / (record["name"] + ".exe"))                 # pretend the program files exist
        (tmp_path / (record["name"] + ".exe")).write_text("", encoding="utf-8")
    state = json.loads((tmp_path / dev.STATE_NAME).read_text(encoding="utf-8"))
    for record in state["closed"]:
        record["path"] = str(tmp_path / (record["name"] + ".exe"))
    (tmp_path / dev.STATE_NAME).write_text(json.dumps(state), encoding="utf-8")
    out = dev.restore(tmp_path / dev.STATE_NAME, running=lambda: set(), start=lambda path, args: started.append((Path(path).stem, args)) or True)
    assert ("chrome", ["--restore-last-session"]) in started and ("OneDrive", []) in started
    assert {r["name"] for r in out["restored"]} >= {"chrome", "OneDrive"} and dev.status(tmp_path / dev.STATE_NAME)["active"] is False
    again = dev.restore(tmp_path / dev.STATE_NAME, running=lambda: set(), start=lambda *a: pytest.fail("second restore must do nothing"))
    assert again["restored"] == [] and "not active" in again["note"]


def test_restore_skips_what_is_already_running_or_no_longer_allowed_or_missing(tmp_path: Path) -> None:
    state = {"active": True, "closed": [
        {"name": "chrome", "path": str(tmp_path / "chrome.exe")},                    # missing file
        {"name": "explorer", "path": r"C:\WINDOWS\explorer.exe"},                     # protected: never relaunched by us
        {"name": "OneDrive", "path": str(tmp_path / "OneDrive.exe")}]}                # already running
    (tmp_path / "OneDrive.exe").write_text("", encoding="utf-8")
    (tmp_path / dev.STATE_NAME).write_text(json.dumps(state), encoding="utf-8")
    out = dev.restore(tmp_path / dev.STATE_NAME, running=lambda: {"onedrive"}, start=lambda *a: pytest.fail("nothing should launch"))
    assert {s["name"] for s in out["skipped"]} == {"chrome", "explorer", "OneDrive"} and out["restored"] == []


def test_the_command_line_tool_refuses_to_enter_without_yes_and_plans_without_changing_anything(monkeypatch, capsys, tmp_path) -> None:
    spec = importlib.util.spec_from_file_location("iron_god_dev_mode_cli", ROOT / "scripts" / "iron_god_dev_mode.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(dev, "snapshot_processes", lambda: MACHINE)
    monkeypatch.setattr(cli.lm, "available_gib", lambda: 5.0)
    monkeypatch.setattr(dev.os, "getpid", lambda: 21)
    assert cli.main(["enter", "--state-root", str(tmp_path)]) == 2                      # no --yes: nothing happens
    assert not (tmp_path / dev.STATE_NAME).exists()
    monkeypatch.setattr(dev, "system_closer", lambda *a, **k: pytest.fail("plan must not close anything"))
    assert cli.main(["plan", "--state-root", str(tmp_path), "--json"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["needed_gib_for_7b"] == 5.3 and plan["reclaimable_mib"] == 1740 and {c["name"] for c in plan["close"]} >= {"chrome"}


def test_the_dashboard_tags_each_big_memory_user_with_what_dev_mode_would_do_and_shows_when_it_is_on() -> None:
    spec = importlib.util.spec_from_file_location("foundry_dashboard_server", ROOT / "scripts" / "foundry_dashboard_server.py")
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    server._TOP_CACHE.update(at=0.0, rows=[])
    out = json.dumps([{"name": "chrome", "count": 12, "mib": 1277}, {"name": "ollama", "count": 1, "mib": 900},
                      {"name": "MyIndieGame", "count": 1, "mib": 800}])
    tags = {r["name"]: r["dev_mode"] for r in server._top_memory_users(runner=lambda: out, now=1000.0)}
    assert tags == {"chrome": "CLOSE", "ollama": "KEEP", "MyIndieGame": "UNCLASSIFIED"}
    script = (ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")
    assert "Dev Mode closes" in script and "Dev Mode is ON" in script


def test_the_launchers_always_show_the_plan_first_and_only_enter_with_an_explicit_yes() -> None:
    folder = ROOT / "scripts" / "desktop-control"
    enter = (folder / "Enter-Dev-Mode.bat").read_text(encoding="utf-8")
    assert enter.index("dev_mode.py plan") < enter.index("pause") < enter.index("enter --yes")     # plan, confirm, then act
    assert "restore" in (folder / "Exit-Dev-Mode.bat").read_text(encoding="utf-8")
    assert " plan" in (folder / "Dev-Mode-Plan.bat").read_text(encoding="utf-8")


# --- the owner's standing rule (2026-11-08): stalled on memory -> enter Dev Mode and carry on --------------------------------
ON = {"auto": True, "stall_minutes": 3, "exit_idle_minutes": 15, "cooldown_minutes": 30, "max_per_day": 2, "margin_gib": 0.3}


def test_the_rule_is_off_unless_the_policy_explicitly_turns_it_on() -> None:
    for raw in (None, {}, "yes", {"auto": "true"}, {"auto": 1}, {"auto": False}):
        assert dev.auto_config(raw)["auto"] is False, raw
    assert dev.auto_config(ON)["auto"] is True
    messy = dev.auto_config({"auto": True, "stall_minutes": -5, "max_per_day": True, "cooldown_minutes": "soon"})
    assert messy["stall_minutes"] == 3.0 and messy["max_per_day"] == 2 and messy["cooldown_minutes"] == 30.0   # bad values fall back to safe defaults


def decide(**over):
    base = dict(now=10_000.0, stalled_since=10_000.0 - 4 * 60, active=False, last_exit=None, entries_today=0,
                reclaimable_gib=2.6, free_gib=5.0, required_gib=7.3)
    base.update(over)
    return dev.auto_decide(dev.auto_config(ON), **base)


def test_every_condition_must_hold_before_the_engine_closes_anything() -> None:
    assert decide().action == "ENTER"
    assert dev.auto_decide(dev.auto_config(None), now=1, stalled_since=0, active=False, last_exit=None, entries_today=0,
                           reclaimable_gib=9, free_gib=1, required_gib=2).action == "OFF"
    assert decide(active=True).action == "ALREADY_ACTIVE"
    assert decide(stalled_since=None).action == "WAIT"                                    # memory is not what is blocking
    assert decide(stalled_since=10_000.0 - 2 * 60).action == "WAIT"                       # not stalled long enough yet
    assert decide(entries_today=2).action == "LIMIT"
    assert decide(last_exit=10_000.0 - 10 * 60).action == "COOLDOWN"
    assert decide(free_gib=None).action == "WAIT" and decide(required_gib=None).action == "WAIT"   # unknown cannot be shown to help


def test_it_never_closes_programs_when_that_would_not_help() -> None:
    result = decide(free_gib=3.0, reclaimable_gib=2.6, required_gib=7.3)                  # 5.6 GiB even with everything closed
    assert result.action == "NO_HELP" and "Nothing was closed" in result.reason and "5.6" in result.reason
    assert decide(free_gib=4.8, reclaimable_gib=2.6, required_gib=7.3).action == "NO_HELP"   # 7.4 < 7.3 + 0.3 margin
    assert decide(free_gib=5.0, reclaimable_gib=2.6, required_gib=7.3).action == "ENTER"     # 7.6 >= 7.6


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def step(self, minutes):
        self.t += minutes * 60
        return self.t


class Hooks:
    def __init__(self):
        self.entered, self.restored = [], []

    def plan(self):
        return dev.plan(MACHINE, self_pid=21)

    def enter(self, groups, state_path, **kw):
        self.entered.append(kw)
        state = {"active": True, "closed": [{"name": "chrome"}, {"name": "OneDrive"}], "refused": []}
        state_path.write_text(json.dumps(state), encoding="utf-8")
        return state

    def restore(self, state_path, **kw):
        self.restored.append(1)
        state_path.write_text(json.dumps({"active": False}), encoding="utf-8")
        return {"restored": [{"name": "chrome"}]}


def tick(tmp_path, clock, *, waiting=False, idle=False, free=6.0, required=7.3, hooks=None, config=ON):
    hooks = hooks or Hooks()
    out = dev.engine_step(tmp_path, config, waiting_for_memory=waiting, idle=idle, required_gib=required, free_gib=free,
                          now=clock.t, plan_fn=hooks.plan, enter_fn=hooks.enter, restore_fn=hooks.restore)
    return out, hooks


def test_the_stall_clock_enters_exactly_once_after_the_policy_time_and_records_who_did_it(tmp_path: Path) -> None:
    clock, hooks = Clock(), Hooks()
    assert tick(tmp_path, clock, waiting=True, hooks=hooks)[0]["action"] == "WAIT"
    clock.step(2)
    assert tick(tmp_path, clock, waiting=True, hooks=hooks)[0]["action"] == "WAIT" and hooks.entered == []
    clock.step(2)
    out, _ = tick(tmp_path, clock, waiting=True, hooks=hooks)
    assert out["action"] == "ENTER" and out["closed"] == ["chrome", "OneDrive"] and len(hooks.entered) == 1
    assert hooks.entered[0]["entered_by"] == "engine-auto" and "stalled" in hooks.entered[0]["reason"] and hooks.entered[0]["confirmed"] is True
    clock.step(5)
    tick(tmp_path, clock, waiting=True, hooks=hooks)
    assert len(hooks.entered) == 1                                                       # already on: never a second entry


def test_a_stall_that_clears_resets_the_clock(tmp_path: Path) -> None:
    clock, hooks = Clock(), Hooks()
    tick(tmp_path, clock, waiting=True, hooks=hooks)
    clock.step(2)
    tick(tmp_path, clock, waiting=False, hooks=hooks)                                    # the engine did something else: memory was fine
    clock.step(2)
    out, _ = tick(tmp_path, clock, waiting=True, hooks=hooks)
    assert out["action"] == "WAIT" and hooks.entered == []                               # the 2 earlier minutes do not count


def test_when_closing_the_list_would_not_help_nothing_is_closed_and_the_reason_is_recorded(tmp_path: Path) -> None:
    clock, hooks = Clock(), Hooks()
    tick(tmp_path, clock, waiting=True, free=2.0, hooks=hooks)
    clock.step(4)
    out, _ = tick(tmp_path, clock, waiting=True, free=2.0, hooks=hooks)
    assert out["action"] == "NO_HELP" and hooks.entered == []
    assert "Nothing was closed" in json.loads((tmp_path / dev.AUTO_NAME).read_text(encoding="utf-8"))["last_decision"]["reason"]


def test_it_restores_after_the_idle_time_then_respects_the_cooldown_and_the_daily_limit(tmp_path: Path) -> None:
    clock, hooks = Clock(), Hooks()
    tick(tmp_path, clock, waiting=True, hooks=hooks)
    clock.step(4)
    assert tick(tmp_path, clock, waiting=True, hooks=hooks)[0]["action"] == "ENTER"
    tick(tmp_path, clock, idle=True, hooks=hooks)                                         # nothing to build
    clock.step(14)
    assert tick(tmp_path, clock, idle=True, hooks=hooks)[0] is None and hooks.restored == []
    clock.step(2)
    out, _ = tick(tmp_path, clock, idle=True, hooks=hooks)
    assert out["action"] == "RESTORED" and hooks.restored == [1] and out["restored"] == ["chrome"]
    clock.step(1)
    tick(tmp_path, clock, waiting=True, hooks=hooks)
    clock.step(4)
    assert tick(tmp_path, clock, waiting=True, hooks=hooks)[0]["action"] == "COOLDOWN"    # left only a few minutes ago
    clock.step(40)                                                                        # cooldown over and still stalled: back in
    assert tick(tmp_path, clock, waiting=True, hooks=hooks)[0]["action"] == "ENTER" and len(hooks.entered) == 2
    tick(tmp_path, clock, idle=True, hooks=hooks)
    clock.step(16)
    tick(tmp_path, clock, idle=True, hooks=hooks)
    clock.step(35)
    tick(tmp_path, clock, waiting=True, hooks=hooks)
    clock.step(4)
    assert tick(tmp_path, clock, waiting=True, hooks=hooks)[0]["action"] == "LIMIT" and len(hooks.entered) == 2   # 2 per day


def test_a_disabled_rule_does_nothing_and_writes_nothing(tmp_path: Path) -> None:
    out, hooks = tick(tmp_path, Clock(), waiting=True, config=None)
    assert out is None and hooks.entered == [] and not (tmp_path / dev.AUTO_NAME).exists()


def test_the_shipped_policy_records_the_owners_standing_rule_with_its_date_and_limits() -> None:
    policy = json.loads((ROOT / "engineering" / "iron-god-commissioning.json").read_text(encoding="utf-8"))["portfolio_rotation"]
    config = dev.auto_config(policy["dev_mode"])
    assert config["auto"] is True and config["stall_minutes"] == 0 and config["max_per_day"] == 2
    text = policy["dev_mode_decision"]
    assert text.startswith("2026-11-08") and "never touched" in text and "politely" in text and "Take the Wheel" in text


def lane_for(tmp_path):
    from types import SimpleNamespace
    return SimpleNamespace(state_root=tmp_path / "flow", root=tmp_path / "flow" / "iron-god-products", active=lambda: None)


def test_the_lane_feeds_the_rule_only_when_approved_work_is_actually_waiting_on_memory(tmp_path, monkeypatch) -> None:
    from project_orchestrator import iron_god_product_lane as product_lane

    seen = []
    monkeypatch.setattr(product_lane.dev_mode, "engine_step", lambda root, cfg, **kw: seen.append(kw) or None)
    lane = lane_for(tmp_path)
    lane.dev_mode_config = ON
    product_lane._report(lane, {"status": "WAITING_FOR_MEMORY", "work_waiting": True, "route": {"required_gib": 7.3, "available_gib": 5.0}})
    product_lane._report(lane, {"status": "WAITING_FOR_MEMORY", "work_waiting": False, "route": {"required_gib": 3.9, "available_gib": 2.0}})
    product_lane._report(lane, {"status": "IDLE_NO_APPROVED_BACKLOG_ITEM"})
    product_lane._report(lane, {"status": "DISPATCHED"})
    assert [(k["waiting_for_memory"], k["idle"]) for k in seen] == [(True, False), (False, False), (False, True), (False, False)]
    assert seen[0]["required_gib"] == 7.3 and seen[0]["free_gib"] == 5.0
    lane.dev_mode_config = None                                                          # the rule not configured: never consulted
    seen.clear()
    product_lane._report(lane, {"status": "WAITING_FOR_MEMORY", "work_waiting": True})
    assert seen == []


def test_a_failing_rule_can_never_break_the_lane(tmp_path, monkeypatch) -> None:
    from project_orchestrator import iron_god_product_lane as product_lane

    def boom(*a, **k):
        raise RuntimeError("process list unavailable")

    monkeypatch.setattr(product_lane.dev_mode, "engine_step", boom)
    lane = lane_for(tmp_path)
    lane.dev_mode_config = ON
    assert product_lane._report(lane, {"status": "WAITING_FOR_MEMORY", "work_waiting": True})["status"] == "WAITING_FOR_MEMORY"


def test_with_the_owners_zero_stall_time_the_first_memory_blocked_tick_enters_when_it_would_help(tmp_path: Path) -> None:
    """Owner, 2026-11-08: Take the Wheel is the signal to develop; there is no waiting period before Dev Mode."""
    policy = json.loads((ROOT / "engineering" / "iron-god-commissioning.json").read_text(encoding="utf-8"))["portfolio_rotation"]["dev_mode"]
    clock, hooks = Clock(), Hooks()
    out, _ = tick(tmp_path, clock, waiting=True, hooks=hooks, config=policy)
    assert out["action"] == "ENTER" and len(hooks.entered) == 1
    hopeless = Hooks()
    out, _ = tick(tmp_path / "other", clock, waiting=True, free=2.0, hooks=hopeless, config=policy)
    assert out["action"] == "NO_HELP" and hopeless.entered == []                         # no delay, but still never when it cannot help


def test_a_program_that_fails_to_relaunch_keeps_dev_mode_open_for_a_retry(tmp_path: Path) -> None:
    """Review finding: restore used to end Dev Mode even when a relaunch failed, so the program could never be retried."""
    state = {"active": True, "closed": [{"name": "chrome", "path": str(tmp_path / "chrome.exe")},
                                        {"name": "OneDrive", "path": str(tmp_path / "OneDrive.exe")}]}
    for name in ("chrome", "OneDrive"):
        (tmp_path / f"{name}.exe").write_text("", encoding="utf-8")
    path = tmp_path / dev.STATE_NAME
    path.write_text(json.dumps(state), encoding="utf-8")
    flaky = {"chrome": False}
    first = dev.restore(path, running=lambda: set(), start=lambda p, a: flaky.get(Path(p).stem, True))
    assert [r["name"] for r in first["restored"]] == ["OneDrive"] and first["still_active"] is True
    kept = dev.status(path)
    assert kept["active"] is True and [c["name"] for c in kept["closed"]] == ["chrome"] and kept["pending_relaunch"] == ["chrome"]
    started = []
    second = dev.restore(path, running=lambda: set(), start=lambda p, a: started.append(Path(p).stem) or True)
    assert started == ["chrome"] and second["still_active"] is False and second["note"] == "Dev Mode ended"   # only the failed one is retried
    assert dev.status(path)["active"] is False and sorted(dev.status(path)["restored"]) == ["OneDrive", "chrome"]


def test_the_engine_is_not_out_of_dev_mode_until_every_program_is_back(tmp_path: Path) -> None:
    clock, hooks = Clock(), Hooks()
    tick(tmp_path, clock, waiting=True, hooks=hooks)
    (tmp_path / dev.STATE_NAME).write_text(json.dumps({"active": True, "closed": [{"name": "chrome"}]}), encoding="utf-8")
    tick(tmp_path, clock, idle=True, hooks=hooks)
    clock.step(16)

    def partial_restore(state_path, **kw):
        return {"restored": [{"name": "OneDrive"}], "still_active": True}      # state file still says active

    out = dev.engine_step(tmp_path, ON, waiting_for_memory=False, idle=True, required_gib=None, free_gib=None, now=clock.t,
                          plan_fn=hooks.plan, enter_fn=hooks.enter, restore_fn=partial_restore)
    assert out["action"] == "RESTORE_PENDING" and "retried" in out["reason"]
    assert "last_exit" not in json.loads((tmp_path / dev.AUTO_NAME).read_text(encoding="utf-8"))   # no cooldown for a half-finished exit


def test_the_approved_work_check_uses_the_backlogs_qualified_repository_names(tmp_path, monkeypatch) -> None:
    """Review finding: stripping 'owner/' made the helper always answer no work, so the rule could never fire on that path."""
    from types import SimpleNamespace

    from project_orchestrator import iron_god_product_lane as product_lane

    asked = []
    monkeypatch.setattr(product_lane.backlog, "load_backlog", lambda root: ([{"id": "x"}], {}))
    monkeypatch.setattr(product_lane.backlog, "load_state", lambda root: {})
    monkeypatch.setattr(product_lane, "product_repositories", lambda root, cfg: ["jweter/knowledge-engine-core", "jweter/VoiceStudio"])
    monkeypatch.setattr(product_lane.backlog, "next_item",
                        lambda approved, state, repo, max_level: asked.append(repo) or ({"id": "x"} if repo == "jweter/VoiceStudio" else None))
    lane = SimpleNamespace(root=tmp_path)
    assert product_lane._approved_work_waiting(lane, tmp_path, {"development_level": 2}) is True
    assert asked == ["jweter/knowledge-engine-core", "jweter/VoiceStudio"]             # fully qualified, exactly as the backlog keys them
