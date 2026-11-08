"""The command center is read-only, invents nothing, and never shows a plan or an attempt as a delivery."""

from __future__ import annotations

import ast
import importlib.util
import json
from datetime import UTC, datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.request import urlopen

import pytest

from project_orchestrator import human_ownership
from project_orchestrator import iron_god_command_center as cc

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 8, 20, 0, tzinfo=UTC)
RECENT = (NOW - timedelta(hours=2)).isoformat()
OLD = (NOW - timedelta(days=5)).isoformat()


def write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


def engine_state(tmp_path: Path, *, merged_recently: bool, failures: int = 2) -> Path:
    root = tmp_path / "flow-state"
    lane = root / cc.LANE
    write(root / "supervisor-generation-heartbeat.json", {"heartbeat_at_utc": (NOW - timedelta(seconds=40)).isoformat()})
    write(root / "supervisor-pinned-release.json", {"accepted_sha": "5fbbab6b7af7a02b528873a582f49a3c78708641"})
    write(root / "control-switch.log", f"{OLD} TAKE_THE_WHEEL_CONFIRMED revision=abc generation=56\n")
    pubs = {"p-old": {"state": "MERGED", "repository": "jweter/knowledge-engine-core", "pr": {"number": 571, "url": "https://x/571"},
                      "title": "old work", "merged_at_utc": OLD, "merged_sha": "67951756aaaa"},
            "p-open": {"state": "AWAITING_CI", "repository": "jweter/knowledge-engine-ai", "pr": {"number": 200, "url": "https://x/200"},
                       "title": "open work"}}
    if merged_recently:
        pubs["p-new"] = {"state": "MERGED", "repository": "jweter/knowledge-engine-ai", "pr": {"number": 199, "url": "https://x/199"},
                         "title": "fresh work", "merged_at_utc": RECENT, "merged_sha": "deadbeef0000"}
    write(lane / "publications.json", {"items": pubs})
    write(lane / "backlog-state.json", {"items": {f"TASK-{i}": {"attempts": 2, "outcome": "FAIL: SLICE_TESTS_FAILED",
                                                              "updated_at_utc": RECENT} for i in range(failures)}})
    write(lane / "lane-status.json", {"status": "BLOCKED", "repository": "jweter/knowledge-engine-ai", "packet_id": "PKT-1",
                                      "worker_status": "FAIL", "worker_summary": "SLICE_TESTS_FAILED", "observed_at_utc": RECENT})
    write(lane / "decompositions" / "PARENT.json", {"parent_id": "PARENT", "repository": "jweter/knowledge-engine-ai", "status": "ACTIVE",
                                                    "subtasks": [{"function_name": "filter_unavailable_providers"}],
                                                    "residual": ["no Ollama or local corpus is required"], "created_at": RECENT})
    write(lane / "pipeline-report.json", {"observed_at_utc": RECENT, "by_blocker": {"PROGRESSING": 4, "DECOMPOSITION_NOT_STARTED": 2, "WAITING": 1},
                                          "slices": [{"repository": "jweter/knowledge-engine-ai", "slice": "s1", "status": "WAITING",
                                                      "blocker": {"code": "DECOMPOSITION_NOT_STARTED", "text": "A big slice is waiting"}},
                                                     {"repository": "jweter/knowledge-engine-web", "slice": "s2", "status": "WAITING",
                                                      "blocker": {"code": "DECOMPOSITION_NOT_STARTED", "text": "A big slice is waiting"}},
                                                     {"repository": "jweter/knowledge-engine-ai", "slice": "s3", "status": "PROGRESSING",
                                                      "stage": "LOCAL_BUILD", "level": 1, "blocker": None}]})
    events = "".join(json.dumps({"at_utc": (NOW - timedelta(minutes=m)).isoformat(), "code": "DECOMPOSITION_NOT_STARTED",
                                 "text": "A big slice is waiting", "repository": "jweter/knowledge-engine-ai", "slice": "s1"}) + "\n"
                     for m in (30, 20, 10))
    write(lane / "pipeline-events.jsonl", events)
    write(lane / "roadmap-refill.json", {"ledger": [{"at": RECENT, "kind": "LOCAL_DRAFT_DRAFTED", "repository": "jweter/knowledge-engine-ai",
                                                     "id": "TASK-0", "detail": {"status": "DRAFTED"}}]})
    write(lane / "ledger.json", {"packets": {"PKT-1": {"task": "TASK-0", "repository": "jweter/knowledge-engine-ai", "worker_status": "FAIL"}},
                                 "events": [{"packet_id": "PKT-1", "state_after": "BLOCKED", "status": "FAIL",
                                             "observed_at": RECENT, "repository": "jweter/knowledge-engine-ai"}]})
    return root


@pytest.fixture(autouse=True)
def authorized(monkeypatch):
    monkeypatch.setattr(human_ownership, "owner_control_status", lambda root: human_ownership.AUTHORIZED)


def snap(root: Path, **kw):
    kw.setdefault("ollama", lambda: [])
    kw.setdefault("machine", lambda: {"ram_free_gib": 6.6, "ram_total_gib": 15.7, "cpu_percent": 30, "disk_free_gib": 49.3, "disk_total_gib": 475.0})
    kw.setdefault("engine_processes", lambda: 2)
    return cc.snapshot(root, now=NOW, **kw)


def test_productive_requires_a_verified_merge_inside_the_window(tmp_path):
    root = engine_state(tmp_path, merged_recently=True)
    s = snap(root)
    assert s["verdict"]["state"] == "PRODUCTIVE" and "1 verified delivery" in s["verdict"]["headline"]
    assert s["progress"]["verified_in_window"] == 1


def test_busy_with_failing_builds_and_nothing_merged_is_not_productive(tmp_path):
    s = snap(engine_state(tmp_path, merged_recently=False, failures=3))
    assert s["verdict"]["state"] == "NOT_DELIVERING" and "3 tasks failed" in s["verdict"]["headline"]
    assert "SLICE_TESTS_FAILED" in s["work"]["text"]               # says exactly why: the last build's own result


def test_plans_and_attempts_are_never_counted_as_deliveries(tmp_path):
    p = snap(engine_state(tmp_path, merged_recently=False))["progress"]
    assert p["verified_in_window"] == 0                           # an old merge is history, not this window's delivery
    assert [d["evidence"] for d in p["verified_deliveries"]] == ["VERIFIED"] and p["verified_deliveries"][0]["pr"] == 571
    assert p["plans"][0]["evidence"] == "PLANNED" and p["plans"][0]["outstanding_criteria"] == ["no Ollama or local corpus is required"]
    assert {f["evidence"] for f in p["recent_failures"]} == {"FAILED"}
    assert p["open_pull_requests"][0]["evidence"] == "ATTEMPTED" and p["open_pull_requests"][0]["pr"] == 200


def test_owner_control_and_a_dead_engine_are_named(tmp_path, monkeypatch):
    root = engine_state(tmp_path, merged_recently=True)
    assert snap(root, engine_processes=lambda: 0)["verdict"]["state"] == "DOWN"
    monkeypatch.setattr(human_ownership, "owner_control_status", lambda r: human_ownership.OWNER_CONTROL_ACTIVE)
    assert snap(root)["verdict"]["state"] == "PAUSED"


@pytest.mark.parametrize("ollama,busy_job,state", [
    (lambda: None, False, "UNKNOWN"),
    (lambda: [], False, "UNLOADED"),
    (lambda: [], True, "BETWEEN_CALLS"),
    (lambda: [{"name": "qwen2.5-coder:7b", "size": 5.6e9}], False, "LOADED_IDLE"),
    (lambda: [{"name": "qwen2.5-coder:7b", "size": 5.6e9}], True, "GENERATING"),
])
def test_the_model_state_is_reported_as_it_is(tmp_path, ollama, busy_job, state):
    root = engine_state(tmp_path, merged_recently=False)
    if busy_job:
        write(root / cc.LANE / "draft-jobs" / "TASK-9.a0.json", {"id": "TASK-9", "repository": "jweter/knowledge-engine-ai",
                                                                "stage": "DRAFTING", "pid": 4242, "started_at": RECENT})
    s = snap(root, ollama=ollama)
    assert s["model"]["state"] == state
    if busy_job:
        assert s["work"]["busy"] and "TASK-9" in s["work"]["text"] and s["work"]["worker_pid"] == 4242


def test_an_empty_engine_state_is_unknown_not_zero_and_never_crashes(tmp_path):
    s = cc.snapshot(tmp_path / "nothing-here", now=NOW, ollama=lambda: None, machine=lambda: {}, engine_processes=lambda: None)
    assert s["read_only"] is True and s["verdict"]["state"] != "PRODUCTIVE"
    assert s["engine"]["heartbeat_age_s"] is None and s["engine"]["release"] is None and s["model"]["state"] == "UNKNOWN"
    assert s["waiting"]["by_reason"] is None and s["activity"] == [] and s["projects"] == []


def test_waiting_lists_only_named_blockers_never_moving_work(tmp_path):
    w = snap(engine_state(tmp_path, merged_recently=False))["waiting"]
    assert w["by_reason"] == {"DECOMPOSITION_NOT_STARTED": 2}     # not PROGRESSING, not a bare WAITING status
    assert {i["code"] for i in w["items"]} == {"DECOMPOSITION_NOT_STARTED"} and w["items"][0]["why"]


def test_the_feed_collapses_a_repeated_blocker_and_shows_build_failures(tmp_path):
    feed = snap(engine_state(tmp_path, merged_recently=False))["activity"]
    blockers = [a for a in feed if a["kind"] == "DECOMPOSITION_NOT_STARTED"]
    assert len(blockers) == 1 and "unchanged since" in blockers[0]["detail"]   # three identical reports -> one line
    failed = [a for a in feed if a["kind"] == "BUILD_BLOCKED"]
    assert failed and failed[0]["class"] == "failure" and failed[0]["item"] == "TASK-0"
    assert feed == sorted(feed, key=lambda a: a["at_utc"], reverse=True)


def test_projects_name_the_work_in_progress_and_their_verified_merges(tmp_path):
    projects = {p["repository"]: p for p in snap(engine_state(tmp_path, merged_recently=True))["projects"]}
    assert projects["knowledge-engine-ai"]["in_progress"]["slice"] == "s3"
    assert projects["knowledge-engine-ai"]["verified_merged_prs"] == 1
    assert projects["knowledge-engine-web"]["top_blocker"] == "DECOMPOSITION_NOT_STARTED"


def test_the_read_model_writes_nothing_and_starts_nothing():
    tree = ast.parse((ROOT / "src/project_orchestrator/iron_god_command_center.py").read_text(encoding="utf-8"))
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not imported & {"subprocess", "socketserver", "multiprocessing"}
    attrs = [n.func for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
    # file and process changes on any object (str.replace is harmless, so it is checked only on os / shutil below)
    assert not {a.attr for a in attrs} & {"write_text", "write_bytes", "unlink", "mkdir", "rmtree", "kill", "terminate", "touch"}
    module_calls = {(a.value.id, a.attr) for a in attrs if isinstance(a.value, ast.Name) and a.value.id in ("os", "shutil")}
    assert not {attr for _mod, attr in module_calls} & {"replace", "rename", "remove", "unlink", "makedirs", "rmtree", "move",
                                                        "copy", "copyfile", "system", "startfile", "kill"}
    opens = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "open"]
    assert not opens


def test_the_page_is_read_only_and_keeps_the_guardians_marker():
    html = (ROOT / "dashboard/iron-god.html").read_text(encoding="utf-8")
    js = (ROOT / "dashboard/iron-god.js").read_text(encoding="utf-8")
    assert "IRON GOD" in html                                     # the dashboard guardian's health check looks for this
    for forbidden in ("<form", "<button", "method=\"post\"", "/api/control"):
        assert forbidden not in html.lower()
    for forbidden in ("POST", "/api/control", "method:", "Authorization"):
        assert forbidden not in js
    assert "/api/command-center" in js


def test_the_server_serves_the_command_center(tmp_path):
    spec = importlib.util.spec_from_file_location("dashboard_server_cc", ROOT / "scripts" / "foundry_dashboard_server.py")
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    root = engine_state(tmp_path, merged_recently=True)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.handler_for(root, ROOT / "dashboard"))
    Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        data = json.load(urlopen(base + "/api/command-center"))
        assert data["read_only"] is True and data["verdict"]["state"] in {"PRODUCTIVE", "DOWN", "PAUSED", "NOT_DELIVERING", "IDLE", "WORKING"}
        assert "IRON GOD" in urlopen(base + "/iron-god.html").read().decode()
    finally:
        httpd.shutdown()


def test_promotion_installs_every_dashboard_file_from_the_release_with_rollback():
    script = (ROOT / "scripts/desktop-control/Promote-DisabledLocalRelease.ps1").read_text(encoding="utf-8")
    listed = script.split("$dashboardFiles=@(", 1)[1].split(")", 1)[0]
    files = [part.strip().strip("'") for part in listed.replace("\n", " ").split(",") if part.strip()]
    assert r"dashboard\iron-god.html" in files and r"scripts\foundry_dashboard_server.py" in files
    for file in files:                                            # a missing asset would abort a real promotion midway
        assert (ROOT / file.replace("\\", "/")).is_file(), file
    for required in ("DASHBOARD_INSTALL_UNVERIFIED", "VERIFIED_RELEASE_MISSING_DASHBOARD_FILE", "$dashboardBackup", "'DASHBOARD '"):
        assert required in script                                 # hash-verified install and restored on rollback


def test_a_bare_live_copy_serves_the_pinned_releases_command_center(tmp_path):
    """The guardian runs the server from Dashboard/live, which holds no project code: it must use the pinned release."""
    import shutil

    base = tmp_path / "ExponentialEngine"
    live = base / "Dashboard" / "live"
    (live / "scripts").mkdir(parents=True)
    shutil.copy2(ROOT / "scripts" / "foundry_dashboard_server.py", live / "scripts" / "foundry_dashboard_server.py")
    shutil.copytree(ROOT / "dashboard", live / "dashboard")
    sha = "a" * 40
    shutil.copytree(ROOT / "src" / "project_orchestrator", base / "releases" / sha / "src" / "project_orchestrator",
                    ignore=shutil.ignore_patterns("__pycache__"))
    state = engine_state(base, merged_recently=True)               # base/flow-state
    write(state / "supervisor-pinned-release.json", {"accepted_sha": sha})
    spec = importlib.util.spec_from_file_location("live_dashboard_server", live / "scripts" / "foundry_dashboard_server.py")
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    assert not (server._ROOT / "src").exists()                    # the live copy really has no code of its own
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.handler_for(state, live / "dashboard"))
    Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        base_url = f"http://127.0.0.1:{httpd.server_address[1]}"
        data = json.load(urlopen(base_url + "/api/command-center"))
        assert data.get("read_only") is True and data["verdict"]["headline"] != "Dashboard data unavailable"
        assert "IRON GOD" in urlopen(base_url + "/").read().decode()   # the front page is the command center
    finally:
        httpd.shutdown()

# --- review findings on #667: each of these failed before its fix ---------------------------------------------------------
def test_unverified_owner_control_is_unknown_and_does_not_mask_a_dead_engine(tmp_path, monkeypatch):
    root = engine_state(tmp_path, merged_recently=True)
    monkeypatch.setattr(human_ownership, "owner_control_status", lambda r: human_ownership.OWNER_CONTROL_UNVERIFIED)
    s = snap(root)
    assert s["engine"]["mode"] == "UNKNOWN" and s["verdict"]["state"] == "UNKNOWN"
    assert snap(root, engine_processes=lambda: 0)["verdict"]["state"] == "DOWN"       # the dead engine is still reported


def test_a_running_native_job_and_epoch_job_times_are_read(tmp_path):
    root = engine_state(tmp_path, merged_recently=False)
    started = (NOW - timedelta(minutes=7)).timestamp()
    write(root / cc.LANE / "cpp-jobs" / "ew_x.json", {"id": "ew_x", "repository": "jweter/Project-Everward", "state": "RUNNING",
                                                      "pid": 7, "started_at": started})
    work = snap(root)["work"]
    assert work["busy"] and work["kind"] == "native build" and round(work["since_age_s"]) == 420


def test_attempts_in_window_come_from_timestamped_packet_events_not_lifetime_counters(tmp_path):
    root = engine_state(tmp_path, merged_recently=False)
    write(root / cc.LANE / "backlog-state.json", {"items": {"T": {"attempts": 9, "outcome": "FAIL: x", "updated_at_utc": RECENT}}})
    write(root / cc.LANE / "ledger.json", {"packets": {}, "events": [
        {"state_after": "BLOCKED", "observed_at": OLD}, {"state_after": "BLOCKED", "observed_at": OLD},
        {"state_after": "BLOCKED", "observed_at": RECENT}, {"state_after": "IMPLEMENTING", "observed_at": RECENT}]})
    assert snap(root)["progress"]["build_attempts_in_window"] == 1                      # not 9, not the old ones


def test_missing_evidence_is_unknown_never_nothing_delivered(tmp_path):
    root = engine_state(tmp_path, merged_recently=False)
    (root / cc.LANE / "publications.json").unlink()
    s = snap(root)
    assert s["progress"]["unavailable"] and s["progress"]["verified_in_window"] is None
    assert s["verdict"]["state"] == "UNKNOWN" and "unavailable" in s["verdict"]["headline"]
    assert all(p["verified_merged_prs"] is None for p in s["projects"])


def test_a_publication_without_a_pr_number_is_not_an_open_pr_and_project_totals_use_every_merge(tmp_path):
    root = engine_state(tmp_path, merged_recently=False)
    pubs = json.loads((root / cc.LANE / "publications.json").read_text(encoding="utf-8"))
    pubs["items"]["pending"] = {"state": "PUBLISH_PENDING", "repository": "jweter/knowledge-engine-ai", "title": "no pr yet"}
    for n in range(15):
        pubs["items"][f"m{n}"] = {"state": "MERGED", "repository": "jweter/knowledge-engine-core", "pr": {"number": n},
                                  "merged_at_utc": OLD}
    write(root / cc.LANE / "publications.json", pubs)
    s = snap(root)
    assert [o["pr"] for o in s["progress"]["open_pull_requests"]] == [200]
    assert s["progress"]["merged_by_repo"]["knowledge-engine-core"] == 16 and len(s["progress"]["verified_deliveries"]) == 12   # all merges, 12 shown


def test_an_unreadable_dev_mode_state_is_unknown_not_off(tmp_path):
    root = engine_state(tmp_path, merged_recently=False)
    write(root / "dev-mode-state.json", "{not json")
    assert snap(root)["resources"]["dev_mode"]["active"] is None
    (root / "dev-mode-state.json").unlink()
    assert snap(root)["resources"]["dev_mode"]["active"] is False


def test_the_page_renders_unknown_for_missing_sources_and_the_pwa_follows_the_route_change():
    js = (ROOT / "dashboard/iron-god.js").read_text(encoding="utf-8")
    assert "Dev Mode unknown" in js and "could not be read" in js and "In progress" in js
    assert json.loads((ROOT / "dashboard/manifest.webmanifest").read_text(encoding="utf-8"))["start_url"] == "/index.html"
    sw = (ROOT / "dashboard/sw.js").read_text(encoding="utf-8")
    assert 'caches.match("/index.html")' in sw and "shell-v9" in sw and sw.count('"/index.html"') >= 2


def test_rollback_removes_dashboard_files_that_the_promotion_newly_installed():
    script = (ROOT / "scripts/desktop-control/Promote-DisabledLocalRelease.ps1").read_text(encoding="utf-8")
    assert "$dashboardNew.Add($file)" in script and "foreach($file in $dashboardNew)" in script and "Remove-Item" in script


def test_the_live_server_follows_the_pin_to_another_release_and_health_works_first(tmp_path):
    import shutil
    import sys

    base = tmp_path / "ExponentialEngine"
    live = base / "Dashboard" / "live"
    (live / "scripts").mkdir(parents=True)
    shutil.copy2(ROOT / "scripts" / "foundry_dashboard_server.py", live / "scripts" / "foundry_dashboard_server.py")
    shutil.copytree(ROOT / "dashboard", live / "dashboard")
    for sha, marker in (("a" * 40, "RELEASE_A"), ("b" * 40, "RELEASE_B")):
        src = base / "releases" / sha / "src"
        shutil.copytree(ROOT / "src" / "project_orchestrator", src / "project_orchestrator", ignore=shutil.ignore_patterns("__pycache__"))
        with (src / "project_orchestrator" / "iron_god_command_center.py").open("a", encoding="utf-8") as f:
            f.write(f"\nRELEASE_MARKER = {marker!r}\n")
    state = engine_state(base, merged_recently=True)
    # simulate a bare process, then put sys.modules / sys.path back exactly: other tests hold the checkout's module objects
    ours = lambda n: n == "project_orchestrator" or n.startswith("project_orchestrator.")  # noqa: E731
    saved_modules = {n: m for n, m in sys.modules.items() if ours(n)}
    saved_path = list(sys.path)
    for name in saved_modules:
        del sys.modules[name]
    spec = importlib.util.spec_from_file_location("live_server_pin", live / "scripts" / "foundry_dashboard_server.py")
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    write(state / "supervisor-pinned-release.json", {"accepted_sha": "a" * 40})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.handler_for(state, live / "dashboard"))
    Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{httpd.server_address[1]}"
        assert json.load(urlopen(url + "/api/health")) is not None            # health first: no prior command-center call needed
        assert json.load(urlopen(url + "/api/command-center"))["read_only"]
        assert sys.modules["project_orchestrator.iron_god_command_center"].RELEASE_MARKER == "RELEASE_A"
        write(state / "supervisor-pinned-release.json", {"accepted_sha": "b" * 40})   # a promotion moves the pin
        assert json.load(urlopen(url + "/api/command-center"))["read_only"]
        assert sys.modules["project_orchestrator.iron_god_command_center"].RELEASE_MARKER == "RELEASE_B"
    finally:
        httpd.shutdown()
        for name in [n for n in sys.modules if ours(n)]:
            del sys.modules[name]
        sys.modules.update(saved_modules)
        sys.path[:] = saved_path
