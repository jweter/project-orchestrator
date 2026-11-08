from __future__ import annotations

import json
from pathlib import Path

from project_orchestrator import iron_god_cpp_lane as lane

CMAKE = "project(X)\nif(BUILD_TESTING)\n    add_executable(old tests/old.cpp)\nendif()\n"


def request(**over):
    base = {
        "id": "EW-CORE-1", "repository": "jweter/Project-Everward", "objective": "Add clamp helper.",
        "slice_acceptance": ["clamp(5,0,3) == 3"],
        "allowed_paths": ["src/simulation/include/everward/clamp.hpp", "src/simulation/tests/clamp_tests.cpp"],
        "forbidden_paths": list(lane.EVERWARD.protected),
        "cpp": {"header": "src/simulation/include/everward/clamp.hpp", "test": "src/simulation/tests/clamp_tests.cpp"},
    }
    base.update(over)
    return base


def workspace(tmp_path: Path) -> Path:
    cmake = tmp_path / "src/simulation/CMakeLists.txt"
    cmake.parent.mkdir(parents=True)
    cmake.write_text(CMAKE, encoding="utf-8")
    return tmp_path


def test_only_registered_repositories_with_a_cpp_section_are_native() -> None:
    assert lane.classify(request()) is lane.EVERWARD
    assert lane.classify(request(repository="other/Repo")) is None
    assert lane.classify({k: v for k, v in request().items() if k != "cpp"}) is None


def test_scope_accepts_a_well_formed_slice() -> None:
    assert lane.scope_problems(request(), lane.EVERWARD) == []


def test_scope_rejects_paths_outside_the_profile_and_protected_files() -> None:
    bad = request(cpp={"header": "src/game/x.hpp", "test": "src/simulation/tests/../CMakeLists.txt"},
                  allowed_paths=["src/game/x.hpp", "../etc/passwd"])
    problems = lane.scope_problems(bad, lane.EVERWARD)
    assert "OUT_OF_PROFILE_ROOTS:header" in problems
    assert "PATH_NOT_CANONICAL:test" in problems
    protected = lane.scope_problems(request(cpp={"header": "src/simulation/include/everward/a.hpp",
                                                  "test": "src/simulation/CMakeLists.txt"}), lane.EVERWARD)
    assert "OUT_OF_PROFILE_ROOTS:test" in protected
    assert any(p.startswith("ALLOWED_PATH_OUTSIDE_PROFILE") for p in problems)


def test_scope_requires_protected_paths_to_be_forbidden_and_acceptance_criteria() -> None:
    problems = lane.scope_problems(request(forbidden_paths=[], slice_acceptance=[]), lane.EVERWARD)
    assert "NO_ACCEPTANCE_CRITERIA" in problems
    assert any(p.startswith("PROTECTED_PATH_NOT_FORBIDDEN") for p in problems)


def test_non_canonical_paths_are_rejected() -> None:
    sneaky = request(cpp={"header": "src/simulation/include/./everward/x.hpp", "test": "src/simulation/tests/x_tests.cpp"})
    assert "PATH_NOT_CANONICAL:header" in lane.scope_problems(sneaky, lane.EVERWARD)


def test_unreal_is_refused_until_a_profile_exists() -> None:
    wants_engine = request(cpp={"header": "src/simulation/include/everward/a.hpp",
                                "test": "src/simulation/tests/a_tests.cpp", "unreal_required": True})
    assert "UNREAL_PROFILE_NOT_AVAILABLE" in lane.scope_problems(wants_engine, lane.EVERWARD)


def test_target_name_is_deterministic_and_safe() -> None:
    assert lane.target_name("EW-CORE-1") == "ew_ew_core_1_tests"
    assert lane.target_name("a/b c") == "ew_a_b_c_tests"


def test_register_cmake_adds_the_test_once(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    lane.register_cmake(root, lane.EVERWARD, "t1", "tests/t1.cpp")
    lane.register_cmake(root, lane.EVERWARD, "t1", "tests/t1.cpp")
    text = (root / lane.EVERWARD.cmake_file).read_text(encoding="utf-8")
    assert text.count("add_executable(t1 ") == 1 and text.rstrip().endswith("endif()")
    assert "target_link_libraries(t1 PRIVATE everward_simulation)" in text


def test_author_blocks_without_a_model_call_when_out_of_scope(tmp_path: Path) -> None:
    calls: list[str] = []
    outcome = lane.author(request(allowed_paths=["docs/x.md"]), workspace(tmp_path), tmp_path / "b",
                          generate=lambda *a, **k: calls.append("x") or {}, which=lambda t: "/bin/" + t)
    assert not outcome.ok and "ALLOWED_PATH_OUTSIDE_PROFILE" in outcome.blocked and calls == []


def test_author_blocks_when_the_toolchain_is_missing(tmp_path: Path) -> None:
    outcome = lane.author(request(), workspace(tmp_path), tmp_path / "b", generate=lambda *a, **k: {}, which=lambda t: None)
    assert not outcome.ok and "TOOL_MISSING:cmake" in outcome.blocked


def test_author_succeeds_through_the_guarded_door_and_records_evidence(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    seen: dict = {}

    def generate(prompt: str, **kw):
        seen.update(kw)
        reply = ("=== FILE: src/simulation/include/everward/clamp.hpp ===\n#pragma once\n"
                 "inline int clamp(int v,int lo,int hi){return v<lo?lo:(v>hi?hi:v);}\n"
                 "=== FILE: src/simulation/tests/clamp_tests.cpp ===\n#include \"everward/clamp.hpp\"\n"
                 "int main(){return clamp(5,0,3)==3?0:1;}\n")
        return {"response": reply, "model_used": "qwen2.5-coder:3b", "lowest_available_gib": 3.2, "eval_count": 10,
                "eval_duration": 1_000_000_000}

    def run(cmd, cwd, timeout):
        return (0, "ok") if cmd[0] != "git" else (0, "")

    outcome = lane.author(request(), root, tmp_path / "b", generate=generate, run=run, which=lambda t: "/bin/" + t)
    assert outcome.ok, outcome.evidence
    assert seen["requested"] is None  # the shared door chooses the primary builder; the lane names no model
    assert outcome.evidence["model"] == "qwen2.5-coder:3b" and outcome.evidence["lowest_available_gib"] == 3.2
    assert (root / "src/simulation/include/everward/clamp.hpp").is_file()
    assert "ew_ew_core_1_tests" in (root / lane.EVERWARD.cmake_file).read_text(encoding="utf-8")


def test_author_rolls_back_when_the_build_never_passes(tmp_path: Path) -> None:
    root = workspace(tmp_path)

    def generate(prompt: str, **kw):
        return {"response": "=== FILE: src/simulation/include/everward/clamp.hpp ===\nbroken\n"
                            "=== FILE: src/simulation/tests/clamp_tests.cpp ===\nbroken\n", "model_used": "qwen2.5-coder:3b"}

    def run(cmd, cwd, timeout):
        return (0, "") if cmd[0] == "git" or cmd[0] == "cmake" and "--build" not in cmd else (1, "clamp.hpp(1): error C2065: x")

    outcome = lane.author(request(), root, tmp_path / "b", generate=generate, run=run, which=lambda t: "/bin/" + t,
                          limits=lane.cb.Limits(max_iterations=2))
    assert not outcome.ok and outcome.evidence["rolled_back"]
    assert not (root / "src/simulation/include/everward/clamp.hpp").exists()


def test_advance_starts_one_job_and_waiting_spends_no_attempt(tmp_path: Path) -> None:
    import json

    started: list[Path] = []

    def spawn(req: Path, res: Path) -> int:
        started.append(res)
        return 4242

    other = request(id="EW-OTHER", repository="x/y")  # not native: never started
    out = lane.advance(tmp_path, [other, request()], may_start=True, spawn=spawn, alive=lambda pid: True)
    assert [o["status"] for o in out] == ["STARTED"] and lane.read_job(tmp_path, "EW-CORE-1")["attempts"] == 1
    again = lane.advance(tmp_path, [request()], may_start=True, spawn=spawn, alive=lambda pid: True)
    assert again == [] and len(started) == 1  # still running: nothing new
    started[0].write_text(json.dumps({"status": "WAITING_FOR_MEMORY", "reason": "MEMORY"}), encoding="utf-8")
    lane.advance(tmp_path, [request()], may_start=False, spawn=spawn, alive=lambda pid: True)
    waiting = lane.read_job(tmp_path, "EW-CORE-1")
    assert waiting["state"] == "WAITING" and waiting["attempts"] == 0  # the launch that only waited is refunded
    lane.advance(tmp_path, [request()], may_start=True, spawn=spawn, alive=lambda pid: True)
    assert lane.read_job(tmp_path, "EW-CORE-1")["attempts"] == 1  # the resumed launch is the real first attempt


def test_advance_gives_up_after_the_attempt_cap_and_survives_a_dead_author(tmp_path: Path) -> None:
    def spawn(req: Path, res: Path) -> int:
        return 7

    for _ in range(lane.MAX_ATTEMPTS):
        lane.advance(tmp_path, [request()], may_start=True, spawn=spawn, alive=lambda pid: False)
        lane.advance(tmp_path, [request()], may_start=False, spawn=spawn, alive=lambda pid: False)
    assert lane.read_job(tmp_path, "EW-CORE-1")["state"] == "FAILED"
    assert lane.advance(tmp_path, [request()], may_start=True, spawn=spawn, alive=lambda pid: False) == []


def test_advance_records_a_verified_build(tmp_path: Path) -> None:
    import json

    holder: list[Path] = []
    lane.advance(tmp_path, [request()], may_start=True, spawn=lambda q, r: holder.append(r) or 9, alive=lambda p: True)
    holder[0].write_text(json.dumps({"status": "BUILT_AND_VERIFIED", "branch": "ig/cpp-x", "head": "abc"}), encoding="utf-8")
    lane.advance(tmp_path, [request()], may_start=False, spawn=lambda q, r: 1, alive=lambda p: True)
    job = lane.read_job(tmp_path, "EW-CORE-1")
    assert job["state"] == "BUILT" and job["result"]["head"] == "abc"


def test_native_requests_never_reach_the_paid_builder_and_need_a_release(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    from project_orchestrator import iron_god_capable_builder as capable
    from project_orchestrator import iron_god_product_lane as product

    monkeypatch.setattr(capable, "dispatch_request", lambda *a, **k: (_ for _ in ()).throw(AssertionError("paid builder used")))
    fake = SimpleNamespace(root=tmp_path, state_root=tmp_path, reports=tmp_path, workspaces=tmp_path, active=lambda: None)
    report = {"capable_requests": [request()]}
    product.hand_to_capable_builder(fake, "everward", report, {"enabled": True, "daily_limit": 4})  # type: ignore[arg-type]
    product.build_native_requests(fake, "everward", report, tmp_path, "abc", None)  # type: ignore[arg-type]
    assert not (tmp_path / "cpp-jobs").exists()  # without a release identity nothing is started


def _built_job(tmp_path: Path, lane_root: Path, head: str = "h1") -> dict:
    ws = tmp_path / "ws"
    (ws / ".git").mkdir(parents=True, exist_ok=True)
    job = {"id": "EW-CORE-1", "attempts": 1, "state": "BUILT",
           "result": {"status": "BUILT_AND_VERIFIED", "branch": "ig/cpp-x", "head": head, "base": "b0", "workspace": str(ws),
                      "preflight": {"ok": True, "head_sha": head, "detail": "GREEN"}}}
    lane.write_job(lane_root, job)
    return job


def _fake_run(changed: str, head: str = "h1", fail: str | None = None, preflight: str = "GREEN", preflight_head: str | None = None):
    calls: list[list[str]] = []

    def run(cmd, cwd, timeout):
        calls.append(list(cmd))
        if "engineering/preflight.py" in cmd:
            if preflight != "NONE":
                (Path(cwd) / "preflight-evidence.json").write_text(json.dumps(
                    {"status": preflight, "mode": "FULL", "head_sha": preflight_head or head}), encoding="utf-8")
            return (0 if preflight == "GREEN" else 1), "preflight output"
        if cmd[:2] == ["git", "rev-parse"]:
            return 0, head + "\n"
        if cmd[:2] == ["git", "diff"]:
            return 0, changed
        if cmd[:2] == ["git", "status"]:
            return 0, ""
        if fail and fail in cmd:
            return 1, "boom"
        return 0, "ok"

    return run, calls


def test_change_problems_allows_only_declared_files_and_the_cmake_registration() -> None:
    req = request()
    ok = ["src/simulation/CMakeLists.txt", "src/simulation/include/everward/clamp.hpp", "src/simulation/tests/clamp_tests.cpp"]
    assert lane.change_problems(ok, req, lane.EVERWARD) == []
    assert lane.change_problems(ok + [".github/workflows/x.yml"], req, lane.EVERWARD) == ["OUT_OF_SCOPE_CHANGE:.github/workflows/x.yml"]
    assert lane.change_problems([], req, lane.EVERWARD) == ["NO_CHANGE_OR_DIFF_UNAVAILABLE"]


def test_finalize_registers_a_publication_only_after_independent_verification(tmp_path: Path) -> None:
    _built_job(tmp_path, tmp_path)
    run, calls = _fake_run("src/simulation/CMakeLists.txt\nsrc/simulation/include/everward/clamp.hpp\n"
                           "src/simulation/tests/clamp_tests.cpp\n")
    (tmp_path / "ws/src/simulation").mkdir(parents=True, exist_ok=True)
    (tmp_path / "ws/src/simulation/CMakeLists.txt").write_text(CMAKE, encoding="utf-8")
    reports: dict = {}
    pubs: dict = {}
    out = lane.finalize(tmp_path, [request()], save_report=reports.__setitem__, add_publication=pubs.__setitem__, run=run)
    assert [o["status"] for o in out] == ["PUBLICATION_REGISTERED"]
    verifier = next(iter(reports))
    assert reports[verifier]["passed"] is True and reports[verifier]["head_sha"] == "h1"
    item = pubs[verifier]
    assert item["state"] == "PUBLISH_PENDING" and item["verified_by"] == verifier and item["head_sha"] == "h1"
    assert any(c[0] == "ctest" for c in calls) and lane.read_job(tmp_path, "EW-CORE-1")["state"] == "PUBLISHED"
    assert lane.finalize(tmp_path, [request()], save_report=reports.__setitem__, add_publication=pubs.__setitem__, run=run) == []


def test_finalize_rejects_a_scope_escape_and_a_failing_fresh_test(tmp_path: Path) -> None:
    _built_job(tmp_path, tmp_path)
    run, _ = _fake_run("src/simulation/include/everward/clamp.hpp\nengineering/portfolio.json\n")
    reports: dict = {}
    pubs: dict = {}
    out = lane.finalize(tmp_path, [request()], save_report=reports.__setitem__, add_publication=pubs.__setitem__, run=run)
    assert out[0]["status"] == "VERIFICATION_FAILED" and "OUT_OF_SCOPE_CHANGE:engineering/portfolio.json" in out[0]["reason"]
    assert pubs == {} and lane.read_job(tmp_path, "EW-CORE-1")["state"] == "RETRY"

    _built_job(tmp_path, tmp_path)
    (tmp_path / "ws/src/simulation").mkdir(parents=True, exist_ok=True)
    (tmp_path / "ws/src/simulation/CMakeLists.txt").write_text(CMAKE, encoding="utf-8")
    run, _ = _fake_run("src/simulation/include/everward/clamp.hpp\nsrc/simulation/tests/clamp_tests.cpp\n", fail="ctest")
    out = lane.finalize(tmp_path, [request()], save_report=reports.__setitem__, add_publication=pubs.__setitem__, run=run)
    assert out[0]["reason"] == "TEST_FAILED" and pubs == {}


def test_finalize_rejects_a_head_that_moved_after_the_author_finished(tmp_path: Path) -> None:
    _built_job(tmp_path, tmp_path, head="h1")
    run, _ = _fake_run("src/simulation/include/everward/clamp.hpp\n", head="other")
    reports: dict = {}
    out = lane.finalize(tmp_path, [request()], save_report=reports.__setitem__, add_publication=lambda *_: None, run=run)
    assert out[0]["reason"] == "HEAD_MISMATCH"


def test_standing_rules_give_the_model_the_include_path_and_nan_fact() -> None:
    rules = lane.standing_rules({"header": "src/simulation/include/everward/simulation/clamp.hpp"}, lane.EVERWARD)
    assert any('#include "everward/simulation/clamp.hpp"' in r for r in rules)
    assert any("quiet_NaN" in r for r in rules)


def test_a_memory_wait_on_the_last_attempt_does_not_lock_the_job_out_forever(tmp_path: Path) -> None:
    """Review finding: two real failures, then a third launch that only waits for memory must stay startable."""
    started: list[Path] = []

    def spawn(req: Path, res: Path) -> int:
        started.append(res)
        return 99

    for _ in range(lane.MAX_ATTEMPTS - 1):
        lane.advance(tmp_path, [request()], may_start=True, spawn=spawn, alive=lambda pid: False)  # dies: a real failed attempt
        lane.advance(tmp_path, [request()], may_start=False, spawn=spawn, alive=lambda pid: False)
    lane.advance(tmp_path, [request()], may_start=True, spawn=spawn, alive=lambda pid: True)
    assert lane.read_job(tmp_path, "EW-CORE-1")["attempts"] == lane.MAX_ATTEMPTS
    started[-1].write_text(json.dumps({"status": "WAITING_FOR_MEMORY", "reason": "MEMORY"}), encoding="utf-8")
    lane.advance(tmp_path, [request()], may_start=False, spawn=spawn, alive=lambda pid: True)
    job = lane.read_job(tmp_path, "EW-CORE-1")
    assert job["state"] == "WAITING" and job["attempts"] == lane.MAX_ATTEMPTS - 1
    out = lane.advance(tmp_path, [request()], may_start=True, spawn=spawn, alive=lambda pid: True)
    assert [o["status"] for o in out] == ["STARTED"]  # memory came back: it runs again instead of being excluded


def test_a_memory_cancellation_survives_the_builder_boundary(tmp_path: Path) -> None:
    """Review finding: ModelCancelled is an OSError the builder reports as MODEL_UNAVAILABLE; the author must still see it."""
    import pytest

    from project_orchestrator import iron_god_local_model as lm

    def cancelled(prompt, **kw):
        raise lm.ModelCancelled("MEMORY_PRESSURE: available stayed under 0.6 GiB")

    ws = tmp_path / "ws"
    (ws / "src/simulation").mkdir(parents=True)
    (ws / "src/simulation/CMakeLists.txt").write_text(CMAKE, encoding="utf-8")
    with pytest.raises(lm.ModelCancelled):
        lane.author(request(), ws, tmp_path / "b", generate=cancelled, which=lambda tool: "x")


def test_canonical_preflight_accepts_only_green_full_evidence_for_the_exact_head(tmp_path: Path) -> None:
    """Review finding P1: the repository's own engineering/preflight.py gates every native publication."""
    for kwargs, expected in (({}, True), ({"preflight": "FAILED"}, False), ({"preflight": "NONE"}, False),
                             ({"preflight_head": "someone-elses-head"}, False)):
        run, calls = _fake_run("x", **kwargs)
        rec = lane.preflight_record(tmp_path, "h1", run)
        assert rec["ok"] is expected and rec["head_sha"] == "h1", kwargs
        assert any("engineering/preflight.py" in c for c in calls)
        assert not (tmp_path / "preflight-evidence.json").exists()  # no evidence file is left in the committed tree


def test_finalize_refuses_a_publication_without_a_green_preflight_record_for_this_head(tmp_path: Path) -> None:
    changed = ("src/simulation/CMakeLists.txt\nsrc/simulation/include/everward/clamp.hpp\n"
               "src/simulation/tests/clamp_tests.cpp\n")
    for record in (None, {"ok": False, "head_sha": "h1", "detail": "FAILED"}, {"ok": True, "head_sha": "other", "detail": "x"}):
        job = _built_job(tmp_path, tmp_path)
        if record is None:
            job["result"].pop("preflight", None)
        else:
            job["result"]["preflight"] = record
        lane.write_job(tmp_path, job)
        (tmp_path / "ws/src/simulation").mkdir(parents=True, exist_ok=True)
        (tmp_path / "ws/src/simulation/CMakeLists.txt").write_text(CMAKE, encoding="utf-8")
        run, _ = _fake_run(changed)
        pubs: dict = {}
        out = lane.finalize(tmp_path, [request()], save_report=lambda *_: None, add_publication=pubs.__setitem__, run=run)
        assert out[0]["status"] == "VERIFICATION_FAILED" and out[0]["reason"] == "CANONICAL_PREFLIGHT_FAILED" and pubs == {}, record


def test_the_lane_uses_the_shared_primary_builder_unless_a_slice_names_a_model(tmp_path: Path) -> None:
    """Review of the 7B decision: no hard-coded 3B. The lane asks the guarded door, which owns the primary-builder choice."""
    seen: list[str | None] = []

    def door(prompt, requested=None, **kw):
        seen.append(requested)
        return {"response": "", "model_used": requested or "whatever-the-door-chose"}

    ws = tmp_path / "ws"
    (ws / "src/simulation").mkdir(parents=True)
    (ws / "src/simulation/CMakeLists.txt").write_text(CMAKE, encoding="utf-8")
    lane.author(request(), ws, tmp_path / "b", generate=door, which=lambda tool: "x", limits=lane.cb.Limits(max_iterations=1))
    assert seen and set(seen) == {None}                      # no model named: the door picks the primary builder

    seen.clear()
    pinned = request()
    pinned["cpp"] = {**pinned["cpp"], "model": "qwen2.5-coder:3b"}
    lane.author(pinned, ws, tmp_path / "b2", generate=door, which=lambda tool: "x", limits=lane.cb.Limits(max_iterations=1))
    assert set(seen) == {"qwen2.5-coder:3b"}                 # an explicit, evidence-backed per-slice choice is honoured
    assert not hasattr(lane, "CPP_BUILDER_MODEL")
