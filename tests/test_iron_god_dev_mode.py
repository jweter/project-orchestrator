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
    assert plan["needed_gib_for_7b"] == 7.3 and plan["reclaimable_mib"] == 1740 and {c["name"] for c in plan["close"]} >= {"chrome"}


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
