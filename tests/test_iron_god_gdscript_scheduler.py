"""Godot scheduler integration: owner gates, one lease, crash recovery and publication."""
from __future__ import annotations

import json

from pathlib import Path
from types import SimpleNamespace

import pytest

from project_orchestrator import iron_god_gdscript_lane as gd
from project_orchestrator import iron_god_product_lane as product
from project_orchestrator import iron_god_roadmap_refill as refill
from project_orchestrator.state_io import atomic_json

BASE = "a" * 40
HEAD = "b" * 40
REPO = "jweter/Project-Everward"


def request() -> dict:
    return {
        "schema_version": 1, "id": "everward-godot-local-001",
        "repository": REPO, "exact_head": BASE, "owner_approved": True,
        "capability_level": 1, "route": {"builder": "LOCAL"},
        "objective": "Implement approved GDScript behavior",
        "gdscript": {"project_root": "game", "module": "game/src/move.gd",
                     "test_script": "game/tests/test_move.gd"},
        "allowed_paths": ["game/src/move.gd"],
        "slice_acceptance": ["Existing headless test must pass"],
        "reserved_conflicts": [],
    }


def test_request_must_be_exact_head_and_explicitly_owner_authorized():
    req = request()
    assert gd.eligible(req, repository=REPO, base=BASE)
    for update in (
        {"owner_approved": False}, {"exact_head": HEAD},
        {"repository": "jweter/another"}, {"capability_level": 2},
        {"route": {"builder": "CAPABLE"}}, {"gdscript": None},
        {"reserved_conflicts": ["game/src/move.gd"]},
    ):
        changed = {**req, **update}
        assert not gd.eligible(changed, repository=REPO, base=BASE)
    assert not gd.eligible(req, repository=REPO, base="not-a-sha")


def test_existing_request_or_result_json_is_not_a_worker(tmp_path):
    req = request()
    path = gd.job_path(tmp_path, req)
    path.parent.mkdir(parents=True)
    atomic_json(path.with_suffix(".request.json"), req)
    atomic_json(path.with_suffix(".result.json"),
                {"request": req["id"], "status": "FAILED"})
    assert not gd.in_progress(tmp_path)
    assert gd.pending_jobs(tmp_path) == []
    atomic_json(path, {"id": req["id"], "repository": REPO, "state": "STARTING"})
    assert gd.in_progress(tmp_path)


def test_dispatch_requires_one_exclusive_lease_and_binds_exact_request(tmp_path, monkeypatch):
    req = request()
    monkeypatch.setattr(gd.builder, "validate", lambda *_: [])
    monkeypatch.setattr(gd.builder, "toolchain_problems", lambda: [])
    launched = []
    leased = []
    released = []
    def reserve():
        leased.append("lease-1")
        return "lease-1"
    def spawn(source, result, clone, base):
        launched.append((str(source), str(result), clone, base))
        return {"pid": 456, "host": "host", "process_start_fingerprint": "f123"}
    out = gd.advance(
        tmp_path, req, base=BASE, clone=tmp_path, may_start=False, reserve=reserve,
        release=released.append, spawn=spawn, alive=lambda _: False,
    )
    assert out["status"] == "WAITING_FOR_OTHER_LOCAL_WORK"
    assert leased == []
    out = gd.advance(
        tmp_path, req, base=BASE, clone=tmp_path, may_start=True, reserve=reserve,
        release=released.append, spawn=spawn, alive=lambda _: True,
    )
    assert out["status"] == "STARTED"
    assert len(launched) == 1 and leased == ["lease-1"]
    assert launched[0][-1] == BASE
    assert gd.in_progress(tmp_path)
    assert gd.pending_jobs(tmp_path)[0]["request"] == req
    # Restart may observe the same worker, but never launches another.
    out = gd.advance(
        tmp_path, req, base=BASE, clone=tmp_path, may_start=True, reserve=reserve,
        release=released.append, spawn=spawn, alive=lambda _: True,
    )
    assert out["status"] == "RUNNING" and len(launched) == 1
    result_file = Path(gd.read_job(tmp_path, req)["result_path"])
    atomic_json(result_file, {
        "request": req["id"], "base": BASE, "head": HEAD,
        "status": "BUILT_AND_VERIFIED", "workspace": str(tmp_path),
    })
    out = gd.advance(
        tmp_path, req, base=BASE, clone=tmp_path, may_start=False, reserve=reserve,
        release=released.append, spawn=spawn, alive=lambda _: False,
    )
    assert out["status"] == "BUILT" and released == ["lease-1"]
    assert gd.read_job(tmp_path, req)["attempts"] == 1
    assert gd.advance(
        tmp_path, req, base=BASE, clone=tmp_path, may_start=True, reserve=reserve,
        release=released.append, spawn=spawn, alive=lambda _: False,
    )["status"] == "BUILT"
    assert len(launched) == 1


def test_unconfirmed_launch_preserves_lease_for_reconciliation(tmp_path, monkeypatch):
    req = request()
    monkeypatch.setattr(gd.builder, "validate", lambda *_: [])
    monkeypatch.setattr(gd.builder, "toolchain_problems", lambda: [])
    released = []
    out = gd.advance(
        tmp_path, req, base=BASE, clone=tmp_path, may_start=True,
        reserve=lambda: "lease-1", release=released.append,
        spawn=lambda *_: {"pid": 455}, alive=lambda _: False,
    )
    assert out["status"] == "NEEDS_RECONCILIATION"
    assert released == [] and gd.in_progress(tmp_path)
    assert gd.advance(
        tmp_path, req, base=BASE, clone=tmp_path, may_start=True,
        reserve=lambda: "lease-2", release=released.append,
        spawn=lambda *_: pytest.fail("never launch while unresolved"),
        alive=lambda _: False,
    )["status"] == "NEEDS_RECONCILIATION"


def test_failed_worker_result_does_not_publish(tmp_path, monkeypatch):
    req = request()
    path = gd.job_path(tmp_path, req)
    path.parent.mkdir(parents=True)
    atomic_json(path, {"id": req["id"], "repository": REPO, "state": "BUILT",
                       "result": {"status": "BUILT_AND_VERIFIED", "head": HEAD,
                                  "base": BASE, "workspace": str(tmp_path)}})
    monkeypatch.setattr(gd, "independent_verification",
                        lambda *args, **kw: {"passed": False, "head_sha": HEAD,
                                            "reason": "INDEPENDENT_TEST_FAILED"})
    publications = []
    reports = []
    result = gd.finalize(tmp_path, req, save_report=lambda *x: reports.append(x),
                         add_publication=lambda *x: publications.append(x))
    assert result["status"] == "VERIFICATION_FAILED"
    assert reports and not publications
    assert gd.read_job(tmp_path, req)["state"] == "FAILED"


def test_only_supervisor_green_exact_head_can_register_publication(tmp_path, monkeypatch):
    req = request()
    path = gd.job_path(tmp_path, req)
    path.parent.mkdir(parents=True)
    atomic_json(path, {"id": req["id"], "repository": REPO, "state": "BUILT",
                       "result": {"status": "BUILT_AND_VERIFIED", "head": HEAD,
                                  "base": BASE, "branch": "ig/godot-1",
                                  "workspace": str(tmp_path)}})
    monkeypatch.setattr(gd, "independent_verification",
                        lambda *args, **kw: {"passed": True, "head_sha": HEAD})
    publications = []
    reports = []
    result = gd.finalize(tmp_path, req, save_report=lambda *x: reports.append(x),
                         add_publication=lambda *x: publications.append(x))
    assert result["status"] == "PUBLICATION_REGISTERED"
    assert len(publications) == 1
    verifier, publication = publications[0]
    assert publication["verified_by"] == verifier
    assert publication["state"] == "PUBLISH_PENDING"
    assert publication["head_sha"] == HEAD
    assert gd.read_job(tmp_path, req)["state"] == "PUBLISHED"


def test_local_lookahead_knows_owner_declared_godot_without_python_drafter():
    req = request()
    assert refill.can_start_locally(req)
    assert not refill.can_start_locally({**req, "owner_approved": False})
    assert not refill.can_start_locally({**req, "route": {"builder": "CAPABLE"}})


def test_product_rotation_calls_godot_scheduler_and_harvest(monkeypatch):
    assert callable(product.build_godot_requests)
    assert callable(product.harvest_godot_jobs)
    assert callable(product.spawn_godot_author)
    # Same release-bound scheduler and canonical publication functions;
    # no independent PR creation or auto-merge path exists here.
    import inspect
    src = inspect.getsource(product.refill_from_roadmaps)
    assert "harvest_godot_jobs(lane, lease_pool)" in src
    assert "build_godot_requests(lane, name, report, clone, head, release, lease_pool)" in src
    assert "build_native_requests(lane, name, report, clone, head, release)" in src


def test_owner_authorization_is_checked_in_product_lane(monkeypatch):
    req = request()
    class Lane:
        root = Path("/not-used")
        state_root = Path("/not-used")
    monkeypatch.setattr(product.owner_controller, "evaluate",
                        lambda *a, **kw: SimpleNamespace(permitted=False))
    monkeypatch.setattr(product.owner_controller, "control_root", lambda x: x)
    monkeypatch.setattr(product.gdscript_lane, "advance",
                        lambda *a, **kw: pytest.fail("owner veto must prevent dispatch"))
    product.build_godot_requests(
        Lane(), REPO, {"local_requests": [req]}, Path("/not-used"), BASE,
        (Path("/not-used"), BASE), lease_pool=object(),
    )


def test_model_not_admitted_becomes_refundable_wait_result(tmp_path, monkeypatch):
    """No result-file disappearance or leaked model lease on temporary shortage."""
    from scripts import iron_god_gdscript_author as author

    req = request()
    monkeypatch.setattr(author, "evaluate_execution_authority",
                        lambda **kw: SimpleNamespace(permitted=True))

    def fake_git(folder, *args):
        if args[:2] == ("rev-parse", "HEAD"):
            return SimpleNamespace(returncode=0, stdout=BASE, stderr="")
        if args[:2] == ("rev-parse", "origin/main"):
            return SimpleNamespace(returncode=0, stdout=BASE, stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(author, "git", fake_git)

    def fake_task(request, workspace):
        path = workspace / request["gdscript"]["test_script"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("extends SceneTree\\nfunc _initialize(): quit(1)\\n")
        return SimpleNamespace(test=["godot", "--headless", "--script", "tests/test_move.gd"])

    monkeypatch.setattr(author.gdscript, "make_task", fake_task)
    monkeypatch.setattr(author.bounded, "default_run", lambda *args: (1, "assert failed"))

    def unavailable(*args, **kwargs):
        raise author.local_model.ModelNotAdmitted("model slot busy")

    monkeypatch.setattr(author.bounded, "build_change", unavailable)
    result = author.run_once(req, tmp_path, BASE, tmp_path, BASE)
    assert result["status"] == "WAITING_FOR_MEMORY"
    assert "ModelNotAdmitted" in result["reason"]


def test_godot_publication_writes_packet_before_common_merger(tmp_path, monkeypatch):
    req = request()
    lane = product.ProductLane(tmp_path)
    lane.root.mkdir(parents=True)
    result = {"base": BASE, "head": HEAD, "branch": "ig/godot-test"}
    item = gd.publication_item(req, result, "godot-verified")
    product._add_godot_publication(lane, "godot-verified", item, req)
    packet_file = lane.packets / "godot-verified.json"
    assert packet_file.exists()
    packet = json.loads(packet_file.read_text(encoding="utf-8"))
    assert packet["packet_id"] == "godot-verified"
    assert packet["base_sha"] == BASE
    assert packet["slice"]["id"] == req["id"]
    assert lane.publications()["items"]["godot-verified"] == item
    # Replaying the registration is idempotent rather than overwriting evidence.
    product._add_godot_publication(lane, "godot-verified", item, req)
    with pytest.raises(RuntimeError, match="PUBLICATION_PACKET_CONFLICT"):
        modified = {**req, "objective": "different task"}
        product._add_godot_publication(lane, "godot-verified", item, modified)


def test_only_verified_merged_godot_publications_complete_graph(tmp_path):
    req = request()
    lane = product.ProductLane(tmp_path)
    lane.root.mkdir(parents=True)
    item = gd.publication_item(
        req, {"base": BASE, "head": HEAD, "branch": "ig/godot-test"},
        "godot-verified",
    )
    product._add_godot_publication(lane, "godot-verified", item, req)
    assert gd.merged_request_ids(lane.root) == frozenset()
    records = lane.publications()["items"]
    records["godot-verified"].update(state="MERGED", merged_sha="f" * 40)
    product._save_publications(lane, records)
    assert gd.merged_request_ids(lane.root) == frozenset({req["id"]})


def test_godot_review_findings_block_wrong_python_repair_path(tmp_path, monkeypatch):
    req = request()
    lane = product.ProductLane(tmp_path)
    lane.root.mkdir(parents=True)
    item = gd.publication_item(
        req, {"base": BASE, "head": HEAD, "branch": "ig/godot-test"},
        "godot-verified",
    )
    product._add_godot_publication(lane, "godot-verified", item, req)
    monkeypatch.setattr(product.working_memory, "record", lambda *a, **kw: None)
    product._request_review_repair(
        lane, "godot-verified", item,
        SimpleNamespace(findings=[{"path": req["gdscript"]["module"]}]),
    )
    assert item["state"] == "BLOCKED"
    assert item["reason"] == "GODOT_REVIEW_REPAIR_REQUIRES_APPROVED_GDSCRIPT_PATH"


def test_executable_godot_stays_local_without_python_examples():
    req = request()
    assert "acceptance_examples" not in req
    report = {"local_requests": [req], "capable_requests": []}
    moved = product.reroute_slices_the_local_lane_cannot_verify(report)
    assert moved == []
    assert report["local_requests"] == [req]
    assert report["capable_requests"] == []


def test_failed_godot_job_has_one_capped_retry_and_keeps_attempt_evidence(
    tmp_path, monkeypatch,
):
    req = request()
    monkeypatch.setattr(gd.builder, "validate", lambda *_: [])
    monkeypatch.setattr(gd.builder, "toolchain_problems", lambda: [])
    history = []
    releases = []
    def spawn(_source, _result, _clone, _base):
        history.append(len(history) + 1)
        return {"pid": len(history), "host": "local", "process_start_fingerprint": str(len(history))}

    for index in (1, 2):
        first = gd.advance(tmp_path, req, base=BASE, clone=tmp_path,
                           may_start=True, reserve=lambda: "lease-"+str(index),
                           release=releases.append, spawn=spawn, alive=lambda _: False)
        assert first["status"] == "STARTED"
        job = gd.read_job(tmp_path, req)
        assert job["attempts"] == index
        atomic_json(Path(job["result_path"]), {
            "request": req["id"], "base": BASE, "status": "FAILED",
            "reason": "TEST_FAILED_"+str(index),
        })
        failed = gd.advance(tmp_path, req, base=BASE, clone=tmp_path,
                            may_start=False, reserve=lambda: None,
                            release=releases.append, spawn=spawn, alive=lambda _: False)
        assert failed["status"] == "FAILED"
    job = gd.read_job(tmp_path, req)
    assert job["attempts"] == gd.MAX_ATTEMPTS
    assert len(job["prior_results"]) == 1
    assert job["prior_results"][0]["worker_result"]["reason"] == "TEST_FAILED_1"
    assert gd.advance(tmp_path, req, base=BASE, clone=tmp_path,
                      may_start=True, reserve=lambda: "should-not-spend",
                      release=releases.append, spawn=lambda *_: pytest.fail("third attempt forbidden"),
                      alive=lambda _: False)["status"] == "ATTEMPTS_EXHAUSTED"
    assert len(history) == 2
    assert releases == ["lease-1", "lease-2"]


def test_failed_independent_verification_can_retry_within_existing_budget(
    tmp_path, monkeypatch,
):
    req = request()
    path = gd.job_path(tmp_path, req)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(path, {
        "id": req["id"], "repository": REPO, "state": "FAILED",
        "attempts": 1, "reason": "INDEPENDENT_TEST_FAILED",
        "result": {"status": "BUILT_AND_VERIFIED", "base": BASE},
        "base": BASE, "request": req,
    })
    monkeypatch.setattr(gd.builder, "validate", lambda *_: [])
    monkeypatch.setattr(gd.builder, "toolchain_problems", lambda: [])
    launched = []
    result = gd.advance(tmp_path, req, base=BASE, clone=tmp_path, may_start=True,
                        reserve=lambda: "lease-2", release=lambda _: None,
                        spawn=lambda *_: launched.append(True) or {
                            "pid": 22, "host": "local", "process_start_fingerprint": "f2",
                        }, alive=lambda _: False)
    assert result["status"] == "STARTED"
    assert launched == [True]
    job = gd.read_job(tmp_path, req)
    assert job["prior_results"][0]["independent_failure"] == "INDEPENDENT_TEST_FAILED"
    assert job["attempts"] == 2


def test_godot_generation_has_live_owner_abort_and_no_commit_after_takeover(
    tmp_path, monkeypatch,
):
    from scripts import iron_god_gdscript_author as author

    req = request()
    permission = {"allowed": True}
    monkeypatch.setattr(
        author, "evaluate_execution_authority",
        lambda **_kw: SimpleNamespace(permitted=permission["allowed"]),
    )
    def git_ok(_folder, *args):
        if args[:2] == ("rev-parse", "HEAD"):
            return SimpleNamespace(returncode=0, stdout=BASE, stderr="")
        if args[:2] == ("rev-parse", "origin/main"):
            return SimpleNamespace(returncode=0, stdout=BASE, stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(author, "git", git_ok)
    def task_from_test(request, workspace):
        test = workspace / request["gdscript"]["test_script"]
        test.parent.mkdir(parents=True, exist_ok=True)
        test.write_text("extends SceneTree\nfunc _initialize(): quit(1)\n", encoding="utf-8")
        return SimpleNamespace(test=["godot", "test"], build=["godot", "parse"])
    monkeypatch.setattr(author.gdscript, "make_task", task_from_test)
    monkeypatch.setattr(author.bounded, "default_run", lambda *_: (1, "baseline fails"))
    evidence = []
    def fake_build(task, ask, **kwargs):
        evidence.append(kwargs)
        assert kwargs["should_abort"]() is None
        permission["allowed"] = False
        assert kwargs["should_abort"]() == "OWNER_AUTHORIZATION_REVOKED"
        return {"status": "CANCELLED_BY_GUARD", "reason": "OWNER_AUTHORIZATION_REVOKED"}
    monkeypatch.setattr(author.bounded, "build_change", fake_build)
    result = author.run_once(req, tmp_path, BASE, tmp_path, BASE)
    assert evidence and result["status"] == "BLOCKED"
    assert result["reason"] == "OWNER_AUTHORIZATION_REVOKED_DURING_BUILD"
    assert result.get("head") is None
