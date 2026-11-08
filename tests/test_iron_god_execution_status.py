from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from project_orchestrator import iron_god_execution_status as es
from project_orchestrator import iron_god_local_model as lm

ROOT = Path(__file__).resolve().parents[1]
NOW = 1_000_000.0


def seed(lane_root: Path, **sources: dict) -> Path:
    directory = es.execution_dir(lane_root)
    for source, fields in sources.items():
        es.note(directory, source, **fields)
    return directory


def stamp(lane_root: Path, source: str, epoch: float) -> None:
    path = es.execution_dir(lane_root) / f"{source}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["updated_at_epoch"] = epoch
    path.write_text(json.dumps(data), encoding="utf-8")


ACTIVE = {"packet_id": "P1", "repository": "jweter/Project-Everward", "task_id": "EW-1", "title": "Add clamp_unit",
          "capability_level": 3, "model": "ollama/qwen2.5-coder:7b", "worker_pid": 4242}


def test_an_active_turn_shows_repository_task_model_level_phase_and_worker(tmp_path: Path) -> None:
    seed(tmp_path, lane={"status": "DISPATCHED", "active": ACTIVE},
         worker={"phase": "TESTING", "detail": "running the slice's own tests", "packet_id": "P1", "started_at_epoch": NOW - 120, "pid": 4242})
    stamp(tmp_path, "worker", NOW - 20)
    v = es.view(tmp_path, now=NOW, available_gib=8.1, loaded_models=["qwen2.5-coder:7b"],
                capability_profile={"model": "ollama/qwen2.5-coder:7b", "max_passing_level": 3})
    assert v["phase"] == "TESTING" and v["active"] is True and v["stuck"] is False
    assert v["repository"] == "jweter/Project-Everward" and v["task"] == {"id": "EW-1", "title": "Add clamp_unit", "capability_level": 3}
    assert v["model"]["name"] == "ollama/qwen2.5-coder:7b" and v["model"]["calibrated_max_level"] == 3 and v["model"]["level_ok"] is True
    assert v["worker"]["pid"] == 4242 and v["worker"]["elapsed_s"] == 120.0 and v["worker"]["last_progress_age_s"] == 20.0
    assert v["resources"]["loaded_models"] == ["qwen2.5-coder:7b"] and v["resources"]["available_gib"] == 8.1
    assert v["waiting"] is None and v["blocked"] is None


def test_a_task_above_the_calibrated_level_is_flagged_never_silently_accepted(tmp_path: Path) -> None:
    seed(tmp_path, lane={"status": "DISPATCHED", "active": ACTIVE})
    v = es.view(tmp_path, now=NOW, capability_profile={"model": "ollama/qwen2.5-coder:3b", "max_passing_level": 1})
    assert v["model"]["level_ok"] is False and v["model"]["calibrated_for"] == "ollama/qwen2.5-coder:3b"


def test_a_worker_that_stopped_reporting_is_flagged_stuck_not_healthy(tmp_path: Path) -> None:
    seed(tmp_path, lane={"status": "DISPATCHED", "active": ACTIVE},
         worker={"phase": "CODING", "packet_id": "P1", "started_at_epoch": NOW - 4000})
    stamp(tmp_path, "worker", NOW - (es.STALE_AFTER_S + 60))
    assert es.view(tmp_path, now=NOW)["stuck"] is True


def test_waiting_and_blocked_carry_a_specific_reason(tmp_path: Path) -> None:
    seed(tmp_path, lane={"status": "WAITING_FOR_CALIBRATION", "reason": "CALIBRATION_STALE_MODEL", "active": None})
    v = es.view(tmp_path, now=NOW)
    assert v["phase"] == "WAITING" and v["waiting"]["code"] == "CALIBRATION_STALE_MODEL"
    assert "different model" in v["waiting"]["text"]

    seed(tmp_path, lane={"status": "WORKTREE_PROVISION_FAILED", "detail": "fatal: path too long", "active": None})
    v = es.view(tmp_path, now=NOW)
    assert v["phase"] == "BLOCKED" and v["blocked"]["code"] == "WORKTREE_PROVISION_FAILED" and "path too long" in v["blocked"]["detail"]


def test_nothing_recorded_is_idle_and_reports_unknown_facts_as_unknown(tmp_path: Path) -> None:
    v = es.view(tmp_path, now=NOW)
    assert v["phase"] == "IDLE" and v["active"] is False and v["repository"] is None
    assert v["resources"]["available_gib"] is None and v["resources"]["loaded_models"] is None and v["owner_authorized"] is None


def test_a_refused_admission_is_surfaced_as_the_reason_for_waiting(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(es.ENV_DIR, str(es.execution_dir(tmp_path)))
    es.record_decision("MEMORY", "qwen2.5-coder:7b", False, "MEMORY: 5.0 GiB available < 7.3 GiB required",
                       available_gib=5.0, required_gib=7.3)
    v = es.view(tmp_path, now=NOW)
    assert v["phase"] == "WAITING" and v["waiting"]["code"] == "MEMORY" and v["resources"]["required_gib"] == 7.3
    assert v["admission"][-1]["available_gib"] == 5.0


def test_repeated_identical_waits_are_one_line_not_a_flood(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(es.ENV_DIR, str(es.execution_dir(tmp_path)))
    for _ in range(20):
        es.record_decision("MEMORY", "m", False, "MEMORY: low", available_gib=5.0, required_gib=7.3)
    es.record_decision("ADMITTED", "m", True, "ADMITTED", available_gib=8.0, required_gib=7.3)
    rows = es.view(tmp_path, now=NOW)["admission"]
    assert [r["kind"] for r in rows] == ["MEMORY", "ADMITTED"]


def test_trace_reports_each_phase_as_it_starts_and_is_a_noop_without_a_directory(tmp_path: Path) -> None:
    trace = es.Trace(es.execution_dir(tmp_path), packet_id="P1", repository="r", model="m")
    calls = []
    wrapped = trace.wrap("COMPILING", lambda x: calls.append(x) or x * 2)
    assert wrapped(21) == 42 and calls == [21]
    assert json.loads((es.execution_dir(tmp_path) / "worker.json").read_text(encoding="utf-8"))["phase"] == "COMPILING"
    assert trace.wrap("CODING", None) is None                       # optional hooks stay optional
    es.Trace(None).phase("CODING")                                  # no directory configured: nothing happens
    with pytest.raises(ValueError):
        trace.phase("DAYDREAMING")


def test_the_model_door_reports_admission_decisions_and_cancellation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(es.ENV_DIR, str(es.execution_dir(tmp_path)))

    class Door:
        def loaded(self):
            return []

        def unload(self, *_a, **_k):
            return True

        def generate(self, *_a, **_k):
            return {"response": "{}"}

    with pytest.raises(lm.ModelNotAdmitted):
        lm.guarded_generate("p", num_ctx=1, num_predict=1, wait_s=0.0, client=Door(), probes={"available": lambda: 5.0},
                            directory=tmp_path / "slot")
    lm.guarded_generate("p", num_ctx=1, num_predict=1, wait_s=5, client=Door(), probes={"available": lambda: 9.0},
                        directory=tmp_path / "slot")
    kinds = [d["kind"] for d in es.view(tmp_path, now=NOW)["admission"]]
    assert kinds == ["MEMORY", "ADMITTED"]
    with lm.model_slot(1, directory=tmp_path / "busy"):
        with pytest.raises(lm.ModelNotAdmitted, match="SLOT_BUSY"):
            lm.guarded_generate("p", num_ctx=1, num_predict=1, wait_s=0.0, client=Door(), probes={"available": lambda: 9.0},
                                directory=tmp_path / "busy", poll_s=0.01)
    assert es.view(tmp_path, now=NOW)["admission"][-1]["kind"] == "SLOT_BUSY"


def test_the_lane_mirrors_every_status_with_its_active_task(tmp_path: Path) -> None:
    from project_orchestrator import iron_god_product_lane as product_lane

    lane = SimpleNamespace(root=tmp_path, active=lambda: {**ACTIVE, "worker": {"pid": 4242}})
    product_lane._report(lane, {"status": "DISPATCHED", "repository": "jweter/Project-Everward"})
    v = es.view(tmp_path, now=es._read(es.execution_dir(tmp_path), "lane")["updated_at_epoch"])
    assert v["lane"]["status"] == "DISPATCHED" and v["task"]["id"] == "EW-1" and v["worker"]["pid"] == 4242


def test_the_dashboard_health_endpoint_carries_the_execution_view_and_the_page_renders_it(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("foundry_dashboard_server", ROOT / "scripts" / "foundry_dashboard_server.py")
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    lane_root = tmp_path / "iron-god-products"
    seed(lane_root, lane={"status": "WAITING_FOR_MEMORY", "reason": "MEMORY", "active": None})
    view = server._execution_view(tmp_path)
    assert view["phase"] == "WAITING" and view["waiting"]["code"] == "MEMORY" and "model" in view
    page = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")
    assert 'id="execution"' in page and 'id="exec-phase"' in page and "app.js?v=7" in page
    assert "renderExecution(h?.execution)" in script and "Memory admission decisions" in script
    assert "azathoth-shell-v7" in (ROOT / "dashboard" / "sw.js").read_text(encoding="utf-8")


def test_a_native_cpp_job_with_no_lane_record_is_shown_from_the_authors_own_report(tmp_path: Path) -> None:
    seed(tmp_path, worker={"phase": "COMPILING", "detail": "cmake configure/build with MSVC", "packet_id": "EW-1",
                           "repository": "jweter/Project-Everward", "task_id": "EW-1", "title": "Add clamp_unit",
                           "capability_level": 3, "model": "ollama/qwen2.5-coder:7b", "started_at_epoch": NOW - 90, "pid": 777})
    stamp(tmp_path, "worker", NOW - 5)
    v = es.view(tmp_path, now=NOW)
    assert v["phase"] == "COMPILING" and v["active"] is True and v["repository"] == "jweter/Project-Everward"
    assert v["task"]["id"] == "EW-1" and v["worker"]["pid"] == 777 and v["model"]["name"] == "ollama/qwen2.5-coder:7b"


def test_a_finished_turn_reports_idle_and_is_never_shown_as_still_running(tmp_path: Path) -> None:
    seed(tmp_path, worker={"phase": "IDLE", "detail": "PASS: accepted", "packet_id": "EW-1", "started_at_epoch": NOW - 600})
    stamp(tmp_path, "worker", NOW - 2)
    assert es.view(tmp_path, now=NOW)["active"] is False


def test_the_cpp_author_reports_phases_from_the_commands_it_really_runs() -> None:
    text = (ROOT / "scripts" / "iron_god_cpp_author.py").read_text(encoding="utf-8")
    for needle in ('trace.phase("CODING"', 'trace.phase("COMPILING"', 'trace.phase("TESTING"', 'trace.phase("VERIFYING"', "run=traced_run",
                   "lane.preflight_record(workspace, head, traced_run)", 'trace.phase("IDLE"'):
        assert needle in text, needle


def test_the_routing_decision_is_shown_with_the_model_it_chose(tmp_path: Path) -> None:
    route = {"model": "qwen3.5:4b", "reason": "CONSTRAINED_FALLBACK: qwen2.5-coder:7b is not available now", "level": 2}
    seed(tmp_path, lane={"status": "DISPATCHED", "active": {**ACTIVE, "model": "ollama/qwen3.5:4b", "route": route}})
    v = es.view(tmp_path, now=NOW)
    assert v["model"]["name"] == "ollama/qwen3.5:4b" and v["model"]["route"]["reason"].startswith("CONSTRAINED_FALLBACK")
    assert "Why this model" in (ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")


def _server():
    spec = importlib.util.spec_from_file_location("foundry_dashboard_server", ROOT / "scripts" / "foundry_dashboard_server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._TOP_CACHE.update(at=0.0, rows=[])
    return module


def test_the_dashboard_names_the_biggest_memory_users_so_a_memory_wait_has_a_visible_cause() -> None:
    server = _server()
    out = json.dumps([{"name": "chrome", "count": 20, "mib": 2650}, {"name": "ollama", "count": 2, "mib": 120}])
    rows = server._top_memory_users(runner=lambda: out, now=1000.0)
    assert rows[0] == {"name": "chrome", "count": 20, "mib": 2650}
    calls = []
    cached = server._top_memory_users(runner=lambda: calls.append(1) or "[]", now=1005.0)   # inside ten seconds: no new probe
    assert cached == rows and calls == []
    assert server._top_memory_users(runner=lambda: json.dumps({"name": "solo", "count": 1, "mib": 5}), now=1100.0)[0]["name"] == "solo"


def test_an_unreadable_process_list_is_reported_as_nothing_not_as_an_error() -> None:
    server = _server()

    def boom():
        raise OSError("powershell unavailable")

    assert server._top_memory_users(runner=boom, now=5000.0) == []
    assert server._top_memory_users(runner=lambda: "not json", now=6000.0) == []


def test_the_memory_card_lists_them_and_the_server_never_changes_a_process() -> None:
    script = (ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")
    assert "Biggest memory users" in script and "top_consumers" in script
    text = (ROOT / "scripts" / "foundry_dashboard_server.py").read_text(encoding="utf-8")
    start = text.index("def _top_memory_users")
    block = text[start:text.index("def _execution_view")]
    assert "Stop-Process" not in block and "taskkill" not in block and "Kill" not in block   # it only reports
