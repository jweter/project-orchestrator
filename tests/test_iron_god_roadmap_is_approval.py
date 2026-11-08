from __future__ import annotations

import random
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from project_orchestrator import iron_god_backlog as backlog
from project_orchestrator import iron_god_product_lane as product_lane

ROADMAP = "docs/OWNER_APPROVED_ROADMAP_2026-10-25.md"
APPROVED_TEXT = ("# Roadmap\n\nStatus: OWNER-APPROVED\n\n## Standing development authorization\n\nThis roadmap authorizes development.\n\n"
                 "## Next slices\n\n1. Inventory the mechanics.\n")


def item(item_id, repo="jweter/Project-Everward", *, status="proposed", ref=f"{ROADMAP} next slice 1", level=3, **over):
    base = {"id": item_id, "repository": repo, "title": f"{item_id} title", "roadmap_ref": ref, "addresses": "a gap",
            "why": "needed", "consumer": "the loop verification step", "objective": "do it", "interfaces": [], "depends_on": [],
            "acceptance_criteria": ["works"], "tests": [], "level": level, "complexity": "low", "verification": "preflight",
            "definition_of_done": "merged", "status": status}
    base.update(over)
    return base


VERIFIED = {"jweter/Project-Everward": {ROADMAP}}


def test_a_proposed_item_that_cites_the_owners_verified_roadmap_is_approved_by_it_without_asking_again() -> None:
    out = backlog.promote_by_roadmap([item("EW-1")], VERIFIED)[0]
    assert out["status"] == "approved" and out["assignee"] == backlog.AGENT_ID and out["approved_by"] == f"OWNER_ROADMAP:{ROADMAP}"
    assert backlog.item_problems(out) == []


def test_the_roadmap_reference_must_be_a_real_verified_owner_roadmap_of_that_repository() -> None:
    unchanged = [
        item("EW-A", ref="Iron God readiness check 2026-10-27 (engine-originated audit finding)"),       # not the owner's roadmap
        item("EW-B", ref="docs/ROADMAP.md next slice 1"),                                                 # an ordinary doc, not owner-approved
        item("EW-C", ref="docs/OWNER_APPROVED_ROADMAP_2099-01-01.md next slice 1"),                       # a path nobody verified
        item("EW-D", repo="jweter/VoiceStudio"),                                                          # verified for another repository
        item("EW-E", ref=f"{ROADMAP}.evil next slice 1"),                                                 # a look-alike path
    ]
    for original, result in zip(unchanged, backlog.promote_by_roadmap(unchanged, VERIFIED)):
        assert result == original and result["status"] == "proposed", original["id"]
    assert backlog.promote_by_roadmap([item("EW-1")], {}) [0]["status"] == "proposed"                     # nothing verified: nothing approved


def test_retired_done_and_malformed_items_are_never_promoted() -> None:
    items = [item("R", status="retired"), item("D", status="done"), item("A", status="approved"),
             item("L1", level=1)]                                                                         # approved level 1 needs a build spec
    out = {i["id"]: i for i in backlog.promote_by_roadmap(items, VERIFIED)}
    assert out["R"]["status"] == "retired" and out["D"]["status"] == "done" and out["A"].get("approved_by") is None
    assert out["L1"]["status"] == "proposed"                                                               # would be malformed once approved


def test_a_promoted_item_stops_being_excluded_as_not_approved_and_is_judged_on_its_real_constraints() -> None:
    state: dict = {}
    raw = [item("EW-1", level=3)]
    assert backlog.exclusion(raw[0], state, items=raw, done=set(), max_level=2)[0] == "NOT_APPROVED"      # the old, wrong reason
    promoted = backlog.promote_by_roadmap(raw, VERIFIED)
    reason = backlog.exclusion(promoted[0], state, items=promoted, done=set(), max_level=2)
    assert reason is not None and reason[0] == "LEVEL_ABOVE_MEASURED_CAPABILITY"                           # the true reason is now shown


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                        "PATH": __import__("os").environ["PATH"], "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")})


def clone_with(tmp_path: Path, name: str, files: dict[str, str]) -> Path:
    repo = tmp_path / name
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    return repo


def test_the_lane_verifies_the_roadmap_from_the_document_itself(tmp_path: Path) -> None:
    clones = {
        "jweter/Project-Everward": clone_with(tmp_path, "ew", {ROADMAP: APPROVED_TEXT}),
        "jweter/NoStatus": clone_with(tmp_path, "ns", {ROADMAP: "# Roadmap\n\nStatus: DRAFT\n\nstanding development authorization\n"}),
        "jweter/NoGrant": clone_with(tmp_path, "ng", {ROADMAP: "# Roadmap\n\nStatus: OWNER-APPROVED\n\nnothing else\n"}),
        "jweter/NoRoadmap": clone_with(tmp_path, "nr", {"docs/README.md": "x"}),
    }
    lane = SimpleNamespace(clone=lambda repo: clones[repo] if repo in clones else tmp_path / "missing")
    found = product_lane.verified_owner_roadmaps(lane, [*clones, "jweter/NotCloned"])
    assert found == {"jweter/Project-Everward": {ROADMAP}}                                                 # only the genuine, granted one


def test_the_whole_chain_makes_the_owners_roadmap_items_eligible_without_any_owner_step(tmp_path: Path) -> None:
    clones = {"jweter/Project-Everward": clone_with(tmp_path, "ew", {ROADMAP: APPROVED_TEXT})}
    lane = SimpleNamespace(clone=lambda repo: clones[repo])
    raw = [item("EW-L2", level=2, direct={"module_path": "pkg/core.py", "targets": ["one"], "add": [], "direction": "make one() return 2",
                                        "test_path": "tests/test_one.py", "test_source": "from pkg.core import one\n\ndef test_one():\n    assert one() == 2\n"})]
    verified = product_lane.verified_owner_roadmaps(lane, ["jweter/Project-Everward"])
    approved = backlog.promote_by_roadmap(raw, verified)
    assert backlog.next_item(raw, {}, "jweter/Project-Everward", max_level=2) is None                      # before: stuck on approval
    chosen = backlog.next_item(approved, {}, "jweter/Project-Everward", max_level=2)
    assert chosen is not None and chosen["id"] == "EW-L2" and chosen["approved_by"].startswith("OWNER_ROADMAP:")


def test_a_memory_stall_in_local_decomposition_or_drafting_feeds_the_standing_dev_mode_rule(tmp_path, monkeypatch) -> None:
    """The roadmap reaches the local builder through decomposition and drafting; a RAM stall there is a development stall."""
    lane = product_lane.ProductLane(tmp_path)
    seen = []
    monkeypatch.setattr(product_lane, "_dev_mode_tick", lambda lane_, payload: seen.append(payload))
    monkeypatch.setattr(product_lane, "available_gib", lambda: 5.6)
    product_lane.log_draft_outcomes(lane, "jweter/knowledge-engine-core",
                                    [{"id": "RM-x", "status": "WAITING_FOR_MEMORY", "reason": "ModelNotAdmitted: MEMORY"}],
                                    prefix="LOCAL_DECOMPOSE_")
    assert len(seen) == 1 and seen[0]["status"] == "WAITING_FOR_MEMORY" and seen[0]["work_waiting"] is True
    assert seen[0]["route"]["required_gib"] == pytest.approx(5.3) and seen[0]["route"]["available_gib"] == 5.6   # the primary builder's need
    seen.clear()
    product_lane.log_draft_outcomes(lane, "jweter/knowledge-engine-core", [{"id": "RM-y", "status": "FAILED", "reason": "PLAN_REJECTED"}],
                                    prefix="LOCAL_DECOMPOSE_")
    assert seen == []                                                                                            # only a memory wait counts


def test_memory_wait_search_ignores_repositories_that_cannot_start_work_anyway(tmp_path, monkeypatch) -> None:
    """Review finding: freeing memory cannot help a repository blocked by an open publication or a failing preflight."""
    asked = []
    monkeypatch.setattr(product_lane.backlog, "load_backlog", lambda root: ([{"id": "x"}], {}))
    monkeypatch.setattr(product_lane.backlog, "load_state", lambda root: {})
    monkeypatch.setattr(product_lane, "product_repositories", lambda root, cfg: ["jweter/a", "jweter/b"])
    monkeypatch.setattr(product_lane, "verified_owner_roadmaps", lambda lane, names: {})
    monkeypatch.setattr(product_lane.backlog, "next_item",
                        lambda approved, state, repo, max_level: asked.append(repo) or {"id": repo})
    lane = SimpleNamespace(root=tmp_path)
    assert product_lane._approved_work_waiting(lane, tmp_path, {"development_level": 2}, {"jweter/a"}) is True
    assert asked == ["jweter/b"]                                                          # a is blocked: never considered
    asked.clear()
    assert product_lane._approved_work_waiting(lane, tmp_path, {"development_level": 2}, {"jweter/a", "jweter/b"}) is False
    assert asked == []


def test_repository_blockers_are_open_publications_and_failed_preflights(tmp_path, monkeypatch) -> None:
    pubs = {"p1": {"repository": "jweter/a", "state": "AWAITING_CI"}, "p2": {"repository": "jweter/b", "state": "MERGED"}}
    lane = SimpleNamespace(root=tmp_path, publications=lambda: {"items": pubs}, _json=lambda path, default: {"repositories": {}})
    monkeypatch.setattr(product_lane, "unready_repositories", lambda rows, head_of: {"jweter/c"})
    assert product_lane._repository_blockers(lane, None) == {"jweter/a", "jweter/c"}


def test_a_planning_memory_stall_survives_to_the_passs_final_report(tmp_path, monkeypatch) -> None:
    """Review finding: with a stall time above zero, the final IDLE report used to reset the clock the planning stall started."""
    seen = []
    monkeypatch.setattr(product_lane.dev_mode, "engine_step", lambda root, cfg, **kw: seen.append(kw) or None)
    lane = SimpleNamespace(state_root=tmp_path / "flow", root=tmp_path / "flow" / "iron-god-products", active=lambda: None,
                           dev_mode_config={"auto": True, "stall_minutes": 5})
    lane.planning_memory_wait = {"required_gib": 7.3, "available_gib": 5.6}
    product_lane._report(lane, {"status": "IDLE_NO_APPROVED_BACKLOG_ITEM"})                       # the pass's final report
    assert seen[-1]["waiting_for_memory"] is True and seen[-1]["idle"] is False                    # the stall continues, not reset
    assert seen[-1]["required_gib"] == 7.3 and seen[-1]["free_gib"] == 5.6
    assert lane.planning_memory_wait is None                                                       # consumed once
    product_lane._report(lane, {"status": "IDLE_NO_APPROVED_BACKLOG_ITEM"})                       # the next pass is genuinely idle
    assert seen[-1]["waiting_for_memory"] is False and seen[-1]["idle"] is True


def test_the_planning_hook_remembers_the_stall_for_the_final_report(tmp_path, monkeypatch) -> None:
    lane = product_lane.ProductLane(tmp_path)
    monkeypatch.setattr(product_lane, "_dev_mode_tick", lambda lane_, payload, final=False: None)
    monkeypatch.setattr(product_lane, "available_gib", lambda: 5.6)
    product_lane.log_draft_outcomes(lane, "jweter/knowledge-engine-core",
                                    [{"id": "RM-x", "status": "WAITING_FOR_MEMORY", "reason": "MEMORY"}], prefix="LOCAL_DECOMPOSE_")
    assert lane.planning_memory_wait["required_gib"] == pytest.approx(5.3)


# --- the lane-level proof for review finding 1 (the helper alone was not enough) ----------------------------------------------
from tests.test_iron_god_product_lane import (  # noqa: E402,F401  (env is a pytest fixture)
    FULL, REPOS, FakeGitHub, RoutedRunner, direct_item, directed_env, env, measured, routed_step)


def test_the_lane_does_not_call_work_a_memory_stall_when_its_repository_cannot_start_it_anyway(env, monkeypatch) -> None:
    """Review finding 1, at the place it was found: an approved level-2 item in a repository that is blocked (open publication or
    failing base preflight) must not look like 'waiting for memory', or the standing rule closes programs for nothing."""
    directed_env(env, [direct_item("L2-1", REPOS[0])])
    runner = RoutedRunner()
    waiting = routed_step(env, runner, 0.5, measured(**FULL))
    assert waiting["status"] == "WAITING_FOR_MEMORY" and waiting["work_waiting"] is True       # control: the unblocked case still waits

    monkeypatch.setattr(product_lane, "unready_repositories", lambda rows, head_of: {REPOS[0]})   # the repository is blocked
    blocked = routed_step(env, RoutedRunner(), 0.5, measured(**FULL))
    assert blocked.get("work_waiting") is not True          # the rule is not told about a stall closing programs cannot fix


# --- owner decision 2026-11-08: no padded 7B threshold, and a stall the trace sees must reach the standing rule ---------------
def test_the_7b_is_started_whenever_it_can_load_and_only_refused_when_it_cannot() -> None:
    from project_orchestrator import iron_god_local_model as lm

    for free in (5.9, 3.0, 1.0):                                    # the laptop's real free RAM and well below it
        assert lm.gate_for("qwen2.5-coder:7b", available=lambda free=free: free).admit() == (True, "ADMITTED")
    refused, why = lm.gate_for("qwen2.5-coder:7b", available=lambda: 0.5).admit()
    assert refused is False and why.startswith("MEMORY: 0.5 GiB available < 1.0 GiB required")
    assert lm.gate_for("qwen2.5-coder:7b", available=lambda: 0.0).required_gib == pytest.approx(5.3)   # the comfortable level stays
    assert lm.gate_for("some-unknown-model", available=lambda: 3.0).admit()[0] is False                # unparsed names stay strict


def test_tight_memory_runs_the_7b_and_tells_the_standing_rule(env, monkeypatch) -> None:
    """Below the comfortable level the model still runs; the rule is told so Dev Mode can free memory meanwhile."""
    carried = []
    monkeypatch.setattr(product_lane, "_dev_mode_tick",
                        lambda lane, payload, final=False: carried.append(getattr(lane, "planning_memory_wait", None)) if final else None)
    directed_env(env, [direct_item("L2-1", REPOS[0])])
    runner = RoutedRunner()
    tight = routed_step(env, runner, 3.0, measured(**FULL))
    assert tight["status"] in {"DISPATCHED", "ACCEPTED_LOCAL"} and runner.models == ["qwen2.5-coder:7b"]
    assert carried[-1] == {"required_gib": pytest.approx(5.3), "available_gib": 3.0}
    directed_env(env, [direct_item("L2-2", REPOS[1])])
    comfortable = routed_step(env, RoutedRunner(), 8.0, measured(**FULL))
    assert comfortable["status"] in {"DISPATCHED", "ACCEPTED_LOCAL"} and carried[-1] is None


def test_a_memory_stall_only_the_trace_can_see_still_reaches_the_dev_mode_rule(tmp_path, monkeypatch) -> None:
    """Found live: 10 slices waited for memory but the lane said IDLE and the rule answered 'nothing to do', because the stall
    is reported once (when the waiting author is harvested) and never again. The trace sees it every pass."""
    seen = []
    monkeypatch.setattr(product_lane.dev_mode, "engine_step", lambda root, cfg, **kw: seen.append(kw) or None)
    monkeypatch.setattr(product_lane.pipeline_trace, "refresh",
                        lambda root, **kw: {"by_blocker": {"DECOMPOSITION_WAITING_FOR_MEMORY": 10}})
    monkeypatch.setattr(product_lane, "available_gib", lambda: 4.0)
    lane = SimpleNamespace(state_root=tmp_path / "flow", root=tmp_path / "flow" / "iron-god-products", active=lambda: None,
                           dev_mode_config={"auto": True, "stall_minutes": 0})
    product_lane._report(lane, {"status": "IDLE_NO_APPROVED_BACKLOG_ITEM"})
    assert seen[-1]["waiting_for_memory"] is True and seen[-1]["free_gib"] == 4.0
    assert seen[-1]["required_gib"] == pytest.approx(5.3)

    monkeypatch.setattr(product_lane, "available_gib", lambda: 9.0)                            # memory is fine: nothing is stalled
    product_lane._report(lane, {"status": "IDLE_NO_APPROVED_BACKLOG_ITEM"})
    assert seen[-1]["waiting_for_memory"] is False


def test_dev_mode_never_hears_of_a_stall_while_the_owner_holds_control(env, monkeypatch) -> None:
    """Review P1: a trace-derived memory stall must not reach the rule when execution is not authorised (Give Me Control), or
    Dev Mode could close the owner's programs while they are using the laptop."""
    from types import SimpleNamespace as NS
    seen = []
    monkeypatch.setattr(product_lane.dev_mode, "engine_step", lambda root, cfg, **kw: seen.append(kw) or None)
    monkeypatch.setattr(product_lane.pipeline_trace, "refresh",
                        lambda root, **kw: {"by_blocker": {"DECOMPOSITION_WAITING_FOR_MEMORY": 10}})
    monkeypatch.setattr(product_lane, "available_gib", lambda: 2.0)
    policy = {**env.policy, "portfolio_rotation": {**env.policy["portfolio_rotation"],
                                                   "dev_mode": {"auto": True, "stall_minutes": 0}}}

    def advance(permitted):
        return product_lane.advance_product_lane(
            env.state, env.release, supervisor={}, release_sha="a" * 40, allow_dispatch=True,
            decide=lambda: NS(permitted=permitted, reasons=["OWNER_HOLDS_CONTROL"]), spawn=RoutedRunner().spawn,
            alive=lambda _w: False, stop=lambda _w: None, resources=lambda: {"admitted": True}, lease_pool=env.pool,
            github=FakeGitHub(), sync=env.sync, owned_paths=lambda r: [], gates=lambda *_a: None, memory_root=env.memory,
            rng=random.Random(7), policy=policy, profiles=lambda: measured(**FULL), free_gib=lambda: 2.0)

    denied = advance(False)
    assert denied["status"] == "WAITING_FOR_OWNER_AUTHORITY"
    assert seen == []                                            # the rule was never consulted: nothing can be closed

    advance(True)                                                # control returns: the same pass may consult the rule again
    assert seen and seen[-1]["waiting_for_memory"] is True
