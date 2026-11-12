from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from project_orchestrator import iron_god_pipeline_trace as pt
from project_orchestrator import iron_god_product_lane as product_lane
from project_orchestrator import iron_god_task_decomposer as decomposer

ROOT = Path(__file__).resolve().parents[1]
NOW = 2_000_000.0
OLD = NOW - 3 * 3600


def request(rid="RM-a", *, level=3, builder="CAPABLE", reason="LEVEL_BEYOND_PRODUCT_LANE", slice_="big-slice", **over):
    # A Python repository: these tests are about decomposition states. Everward's src/** is native C++ and is routed to
    # the C++ lane instead (test_a_native_slice_without_a_cpp_spec_names_the_gap_instead_of_looking_stuck).
    base = {"id": rid, "repository": "jweter/knowledge-engine-core", "slice": slice_, "capability_level": level,
            "route": {"builder": builder, "level": level, "reason": reason}, "allowed_paths": ["src/**"],
            "slice_acceptance": ["it works"], "_file_mtime": NOW - 60}
    base.update(over)
    return base


def ctx(**over):
    base = {"requests": [], "jobs": {}, "plans": set(), "events": {}, "dispatched": {}, "backlog": {}}
    base.update(over)
    return base


def run(req, **kw):
    context = kw.pop("context", ctx())
    context["requests"] = context.get("requests") or [req]
    return pt.classify(req, context, now=NOW, **kw)


def test_a_slice_without_a_declared_level_is_named_not_silently_dropped() -> None:
    row = run(request(level=None, reason="SLICE_CAPABILITY_LEVEL_UNDECLARED", slice_="phase2-low-spec-play-mode-next"))
    assert row["blocker"]["code"] == "SLICE_CAPABILITY_LEVEL_UNDECLARED" and row["status"] == "BLOCKED" and row["stage"] == "ROUTING"


def test_planning_tasks_have_no_level_by_design_and_are_not_flagged_as_missing_one() -> None:
    plan = request("RM-plan", level=None, slice_="extend-capability-graph", reason="ROADMAP_PLANNING_TASK")
    assert run(plan)["blocker"] is None and run(plan)["stage"] == "PLANNING_TASK"
    dispatched = run(plan, context=ctx(dispatched={"RM-plan": "2026-11-08T03:37:05Z"}))
    assert dispatched["status"] == "PROGRESSING" and dispatched["blocker"]["code"] == "CAPABLE_BUILDER_DISPATCHED"
    budget = {"RM-plan": [{"kind": "CAPABLE_NOT_DISPATCHED", "at": "2026-11-08T03:37:17Z", "detail": {"reason": "DAILY_BUDGET_EXHAUSTED"}}]}
    assert run(plan, context=ctx(events=budget))["blocker"]["code"] == "WAITING_FOR_CAPABLE_BUILDER_BUDGET"


def test_a_big_slice_waiting_for_the_local_model_says_it_is_the_memory_and_gives_the_numbers() -> None:
    row = run(request(), free_gib=5.6, need_gib=7.3)
    assert row["blocker"]["code"] == "DECOMPOSITION_WAITING_FOR_MEMORY" and row["status"] == "WAITING"
    assert row["evidence"] == {"free_gib": 5.6, "needs_gib": 7.3} and row["stage"] == "DECOMPOSITION"


def test_a_decomposition_that_never_starts_with_memory_available_becomes_stuck_and_is_named() -> None:
    fresh = run(request(), free_gib=9.0, need_gib=7.3)
    assert fresh["blocker"]["code"] == "DECOMPOSITION_NOT_STARTED" and fresh["status"] == "WAITING"
    stale = run(request(_file_mtime=OLD), free_gib=9.0, need_gib=7.3)
    assert stale["blocker"]["code"] == "DECOMPOSITION_NOT_STARTED" and stale["status"] == "STUCK" and stale["evidence"]["idle_minutes"] > 100


def job(stage="FAILED", reason="", attempt=0):
    return {"id": "RM-a", "stage": stage, "reason": reason, "attempt": attempt}


def test_each_way_a_decomposition_can_fail_has_its_own_named_blocker_and_keeps_the_reason() -> None:
    rejected = run(request(), context=ctx(jobs={"RM-a": [job(reason="PLAN_REJECTED: acceptance criterion 4 is not covered")]}), free_gib=9, need_gib=7.3)
    assert rejected["blocker"]["code"] == "DECOMPOSITION_PLAN_REJECTED" and "criterion 4" in rejected["evidence"]["last_reason"]
    memory = run(request(), context=ctx(jobs={"RM-a": [job(reason="ModelNotAdmitted: MEMORY: 5.0 GiB available")]}), free_gib=5, need_gib=7.3)
    assert memory["blocker"]["code"] == "DECOMPOSITION_WAITING_FOR_MEMORY"
    spent = [job(reason="PLAN_REJECTED: x", attempt=i) for i in range(decomposer.MAX_PLAN_ATTEMPTS)]
    assert run(request(), context=ctx(jobs={"RM-a": spent}))["blocker"]["code"] == "DECOMPOSITION_ATTEMPTS_EXHAUSTED"
    marker = sorted(decomposer.TERMINAL_MARKERS)[0]
    assert run(request(), context=ctx(jobs={"RM-a": [job(reason=f"{marker} cannot split")]}))["blocker"]["code"] == "DECOMPOSITION_NOT_POSSIBLE"
    running = run(request(), context=ctx(jobs={"RM-a": [job(stage="PLANNING")]}))
    assert running["status"] == "PROGRESSING" and running["blocker"] is None


def test_a_decomposed_slice_is_progressing_and_counts_its_children() -> None:
    kids = [{"id": f"RM-a-c{i}", "parent": "RM-a"} for i in range(3)]
    row = run(request(), context=ctx(requests=[request(), *kids], plans={"RM-a"}))
    assert row["stage"] == "DECOMPOSED" and row["status"] == "PROGRESSING" and row["evidence"]["children"] == 3


def test_a_local_slice_that_spent_its_attempts_is_named() -> None:
    local = request("RM-l", level=1, builder="LOCAL", reason="WITHIN_MEASURED_LOCAL_CAPABILITY")
    assert run(local)["stage"] == "LOCAL_BUILD" and run(local)["blocker"] is None
    spent = run(local, context=ctx(backlog={"RM-l": {"attempts": 3, "outcome": "FAIL: preflight"}}))
    assert spent["blocker"]["code"] == "LOCAL_BUILD_EXHAUSTED"


def test_an_unknown_gap_still_produces_evidence_instead_of_silence() -> None:
    """Not decomposable (no acceptance), not local, not dispatched, nothing moving for hours: no named rule explains it."""
    odd = request("RM-odd", slice_="mystery", slice_acceptance=[], _file_mtime=OLD)
    from datetime import UTC, datetime

    long_ago = datetime.fromtimestamp(OLD, UTC).isoformat()
    row = run(odd, context=ctx(events={"RM-odd": [{"kind": "GENERATED", "at": long_ago}]}))
    assert row["blocker"]["code"] == "UNEXPLAINED_NO_PROGRESS" and row["status"] == "STUCK"
    for key in ("idle_minutes", "builder", "route", "last_events", "has_plan", "jobs", "allowed_paths", "has_acceptance"):
        assert key in row["evidence"], key


def make_report(rows):
    return {"slices": rows, "requests": len(rows), "by_blocker": {}, "observed_at_utc": "x"}


def blocked_row(code="DECOMPOSITION_WAITING_FOR_MEMORY", rid="RM-a", **evidence):
    return {"id": rid, "repository": "jweter/Project-Everward", "slice": "s", "level": 3, "stage": "DECOMPOSITION", "status": "WAITING",
            "blocker": {"code": code, "text": pt.BLOCKERS[code]}, "evidence": evidence}


def test_a_trigger_is_reported_once_and_again_only_when_its_reason_changes(tmp_path: Path) -> None:
    first = pt.emit_new_triggers(tmp_path, make_report([blocked_row(free_gib=5.6)]))
    assert [e["code"] for e in first] == ["DECOMPOSITION_WAITING_FOR_MEMORY"]
    assert pt.emit_new_triggers(tmp_path, make_report([blocked_row(free_gib=5.6)])) == []             # same thing: silence
    changed = pt.emit_new_triggers(tmp_path, make_report([blocked_row("DECOMPOSITION_PLAN_REJECTED", last_reason="x")]))
    assert [e["code"] for e in changed] == ["DECOMPOSITION_PLAN_REJECTED"]
    lines = (tmp_path / pt.EVENTS).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and json.loads(lines[0])["evidence"] == {"free_gib": 5.6} and json.loads(lines[0])["text"]


def test_normal_progress_is_never_reported_as_a_trigger(tmp_path: Path) -> None:
    ok = {**blocked_row(), "blocker": {"code": "CAPABLE_BUILDER_DISPATCHED", "text": "x"}}
    assert pt.emit_new_triggers(tmp_path, make_report([ok, {**blocked_row(), "blocker": None}])) == []
    assert not (tmp_path / pt.EVENTS).exists()


def test_the_event_log_stays_bounded(tmp_path: Path) -> None:
    for i in range(pt.EVENT_LOG_LIMIT + 50):
        pt.emit_new_triggers(tmp_path, make_report([blocked_row(rid=f"RM-{i}", n=i)]))
    assert len((tmp_path / pt.EVENTS).read_text(encoding="utf-8").splitlines()) == pt.EVENT_LOG_LIMIT


def test_refresh_reads_the_engines_own_files_and_writes_the_report_for_the_dashboard(tmp_path: Path) -> None:
    requests_dir = tmp_path / "roadmap-requests"
    requests_dir.mkdir()
    (requests_dir / "RM-a.json").write_text(json.dumps({k: v for k, v in request().items() if k != "_file_mtime"}), encoding="utf-8")
    report = pt.refresh(tmp_path, free_gib=5.6, need_gib=7.3)
    assert report["requests"] == 1 and report["by_blocker"] == {"DECOMPOSITION_WAITING_FOR_MEMORY": 1}
    assert report["new_triggers"] == [{"id": "RM-a", "code": "DECOMPOSITION_WAITING_FOR_MEMORY"}]
    on_disk = json.loads((tmp_path / pt.REPORT).read_text(encoding="utf-8"))
    assert on_disk["slices"][0]["blocker"]["code"] == "DECOMPOSITION_WAITING_FOR_MEMORY"
    assert pt.refresh(tmp_path, free_gib=5.6, need_gib=7.3)["new_triggers"] == []                  # already reported


def test_the_lane_refreshes_the_trace_on_every_report_and_a_failure_never_breaks_it(tmp_path, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(product_lane.pipeline_trace, "refresh", lambda root, **kw: calls.append((root, kw)))
    monkeypatch.setattr(product_lane, "available_gib", lambda: 6.0)
    lane = SimpleNamespace(state_root=tmp_path / "flow", root=tmp_path / "flow" / "iron-god-products", active=lambda: None)
    product_lane._report(lane, {"status": "IDLE_NO_APPROVED_BACKLOG_ITEM"})
    assert len(calls) == 1 and calls[0][1]["free_gib"] == 6.0 and calls[0][1]["need_gib"] == pytest.approx(5.3)

    def boom(*a, **k):
        raise RuntimeError("unreadable state")

    monkeypatch.setattr(product_lane.pipeline_trace, "refresh", boom)
    assert product_lane._report(lane, {"status": "IDLE_NO_APPROVED_BACKLOG_ITEM"})["status"] == "IDLE_NO_APPROVED_BACKLOG_ITEM"


def test_the_dashboard_carries_the_pipeline_and_the_page_renders_it(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("foundry_dashboard_server", ROOT / "scripts" / "foundry_dashboard_server.py")
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    lane_dir = tmp_path / "iron-god-products"
    lane_dir.mkdir()
    report = make_report([blocked_row(free_gib=5.6)])
    report["by_blocker"] = {"DECOMPOSITION_WAITING_FOR_MEMORY": 1}
    (lane_dir / pt.REPORT).write_text(json.dumps(report), encoding="utf-8")
    pt.emit_new_triggers(lane_dir, report)
    view = server._execution_view(tmp_path)
    pipeline = view["pipeline"]
    assert pipeline["by_blocker"] == {"DECOMPOSITION_WAITING_FOR_MEMORY": 1} and pipeline["attention"][0]["code"] == "DECOMPOSITION_WAITING_FOR_MEMORY"
    assert pipeline["recent_triggers"][0]["code"] == "DECOMPOSITION_WAITING_FOR_MEMORY"
    assert server._execution_view(tmp_path / "empty").get("pipeline") in (None, {})
    script = (ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")
    assert "Roadmap pipeline" in script and "Recent triggers" in script


def test_a_stopped_or_partial_plan_is_reported_as_such_never_as_progress() -> None:
    """A plan file used to mean "decomposed, progressing" whatever its status, so a BLOCKED plan looked like progress."""
    blocked = run(request(), context=ctx(plans={"RM-a": "BLOCKED"}))
    assert blocked["blocker"]["code"] == "DECOMPOSITION_PLAN_BLOCKED" and blocked["status"] == "WAITING"
    partial = run(request(), context=ctx(plans={"RM-a": "PARTIAL"}))
    assert partial["blocker"]["code"] == "DECOMPOSITION_PARTIAL" and partial["stage"] == "CAPABLE_BUILDER"
    taken = run(request(), context=ctx(plans={"RM-a": "PARTIAL"}, dispatched={"RM-a": "2026-11-08T14:00:00Z"}))
    assert taken["blocker"]["code"] == "CAPABLE_BUILDER_DISPATCHED"                 # the stronger builder has the residual
    active = run(request(), context=ctx(plans={"RM-a": "ACTIVE"}))
    assert active["stage"] == "DECOMPOSED" and active["status"] == "PROGRESSING"



def test_a_native_slice_without_a_cpp_spec_names_the_gap_instead_of_looking_stuck() -> None:
    native = request("RM-ew", repository="jweter/Project-Everward", allowed_paths=["src/simulation/**", "tests/**"])
    row = run(native)
    assert row["blocker"]["code"] == "NATIVE_SLICE_NEEDS_CPP_SPEC" and row["status"] == "WAITING"
    with_spec = request("RM-ew", repository="jweter/Project-Everward", allowed_paths=["src/simulation/**"], cpp={"test": "x"})
    assert (run(with_spec)["blocker"] or {}).get("code") != "NATIVE_SLICE_NEEDS_CPP_SPEC"   # a declared cpp spec routes on


def _write_request(directory: Path, rid: str, head: str | None, repository: str = "jweter/knowledge-engine-core") -> None:
    body = {k: v for k, v in request(rid, repository=repository).items() if k != "_file_mtime"}
    if head is not None:
        body["exact_head"] = head
    (directory / f"{rid}.json").write_text(json.dumps(body), encoding="utf-8")


def _record_checked_head(lane_root: Path, repository: str, head: str) -> None:
    from project_orchestrator import iron_god_roadmap_refill as refill

    store = refill.load_store(lane_root)
    store["checked"][repository] = {"at": "2026-11-11T21:00:00+00:00", "head": head}
    refill.store_path(lane_root).parent.mkdir(parents=True, exist_ok=True)
    refill.store_path(lane_root).write_text(json.dumps(store), encoding="utf-8")


def test_requests_from_an_older_repository_head_are_history_not_live_work(tmp_path: Path) -> None:
    """A stale request file once made the dashboard say 'decomposition not started for 7 days' about work that was done."""
    requests_dir = tmp_path / "roadmap-requests"
    requests_dir.mkdir()
    _write_request(requests_dir, "RM-live", "head-new")
    _write_request(requests_dir, "RM-old", "head-old")
    _record_checked_head(tmp_path, "jweter/knowledge-engine-core", "head-new")

    report = pt.scan(tmp_path, now=NOW)

    assert [row["id"] for row in report["slices"]] == ["RM-live"] and report["requests"] == 1
    assert report["superseded_requests"] == 1 and report["superseded"][0]["id"] == "RM-old"
    assert report["superseded"][0]["exact_head"] == "head-old"


def test_a_live_request_is_never_hidden_because_its_file_is_old(tmp_path: Path) -> None:
    """File age is not currency: an unchanged live request can be many hours old. Only the head decides."""
    import os

    requests_dir = tmp_path / "roadmap-requests"
    requests_dir.mkdir()
    _write_request(requests_dir, "RM-live-but-old-file", "head-new")
    os.utime(requests_dir / "RM-live-but-old-file.json", (NOW - 30 * 3600, NOW - 30 * 3600))
    _record_checked_head(tmp_path, "jweter/knowledge-engine-core", "head-new")

    report = pt.scan(tmp_path, now=NOW)

    assert [row["id"] for row in report["slices"]] == ["RM-live-but-old-file"] and report["superseded_requests"] == 0


def test_without_a_recorded_head_or_an_exact_head_nothing_is_hidden(tmp_path: Path) -> None:
    requests_dir = tmp_path / "roadmap-requests"
    requests_dir.mkdir()
    _write_request(requests_dir, "RM-no-head", None)
    _write_request(requests_dir, "RM-unchecked-repo", "head-x", repository="jweter/never-refilled")
    _record_checked_head(tmp_path, "jweter/knowledge-engine-core", "head-new")

    report = pt.scan(tmp_path, now=NOW)

    assert sorted(row["id"] for row in report["slices"]) == ["RM-no-head", "RM-unchecked-repo"]
    assert report["superseded_requests"] == 0


def test_is_superseded_compares_only_the_head_of_the_requests_own_repository() -> None:
    checked = {"jweter/a": {"head": "h1"}, "jweter/b": {"head": "h2"}}
    assert pt.is_superseded({"repository": "jweter/a", "exact_head": "h0"}, checked) is True
    assert pt.is_superseded({"repository": "jweter/a", "exact_head": "h1"}, checked) is False
    assert pt.is_superseded({"repository": "jweter/b", "exact_head": "h1"}, checked) is True
    assert pt.is_superseded({"repository": "jweter/a"}, checked) is False
    assert pt.is_superseded({"repository": "jweter/a", "exact_head": "h0"}, "not a mapping") is False
