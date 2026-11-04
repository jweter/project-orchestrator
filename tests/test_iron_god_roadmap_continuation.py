"""Roadmap-to-execution continuity: an exhausted graph or an exhausted slice is answered from approved scope, not by asking the owner.

Evidence (2026-11-04): VoiceStudio and Project-Everward had every graph slice VERIFIED_COMPLETE while their owner-approved
roadmaps still listed unfinished outcomes, and the engine recorded "what bounded slice should advance the roadmap?" instead
of planning from the roadmap that already authorizes planning. Nothing here widens authority: the grant is read from the
roadmap document at the exact head, the work goes to the capable builder through its existing budget and gates, and every
condition that is not covered keeps asking the owner.
"""

from __future__ import annotations

import json
from pathlib import Path

from project_orchestrator import iron_god_capable_builder as capable
from project_orchestrator import iron_god_roadmap_refill as refill

from test_iron_god_roadmap_refill import PORTFOLIO, SOURCE, graph, product, request_for, run_git, run_refill, write_profile

ROADMAP = "docs/OWNER_APPROVED_ROADMAP_2026-10-25.md"
GRANT = """# Owner-approved roadmap direction

Status: OWNER-APPROVED - standing development direction and implementation authorization.

## Standing development authorization and autonomous continuation
- Next slices are starting points, not an exhaustive ceiling or an approval queue.

## Next slices
1. Implement bounded pitch analysis contracts with native tests.
2. Connect style selection, generation, preview and export.
"""


def complete_graph(repository: str, *, planned_capability: bool) -> dict:
    payload = graph(repository, state="PLANNED" if planned_capability else "VERIFIED_COMPLETE")
    payload["capabilities"][0]["slices"][0]["state"] = "VERIFIED_COMPLETE"
    if not planned_capability:
        payload["capabilities"][0]["verification"] = ["pytest"]
    return payload


def with_roadmap(tmp_path: Path, repository: str, payload: dict, text: str | None = GRANT) -> tuple[Path, str]:
    clone, _head = product(tmp_path, repository, payload)
    if text is not None:
        (clone / ROADMAP).parent.mkdir(exist_ok=True)
        (clone / ROADMAP).write_text(text, encoding="utf-8")
        run_git(clone, "add", ".")
        run_git(clone, "commit", "-q", "-m", "roadmap")
    return clone, run_git(clone, "rev-parse", "HEAD")


def lane_events(tmp_path: Path) -> list[str]:
    return [row["kind"] for row in refill.load_store(tmp_path / "lane")["ledger"]]


def test_a_completed_graph_under_an_owner_roadmap_is_planned_by_the_capable_builder_not_put_to_the_owner(tmp_path: Path) -> None:
    repository = PORTFOLIO[5]  # VoiceStudio: capability complete, nothing left to run
    clone, head = with_roadmap(tmp_path, repository, complete_graph(repository, planned_capability=False))

    report = run_refill(tmp_path, repository, clone, head)

    (ident,) = report["requests"]
    assert report["direction_required"] == [] and "DIRECTION_REQUIRED" not in lane_events(tmp_path)
    request = json.loads((tmp_path / "lane" / refill.REQUESTS / f"{ident}.json").read_text("utf-8"))
    assert request["route"]["builder"] == refill.CAPABLE and request["route"]["reason"] == "ROADMAP_PLANNING_TASK"
    assert request["slice"] == refill.PLANNING_SLICE and request["exact_head"] == head
    assert request["allowed_paths"] == [SOURCE]  # a plan may change the graph and nothing else
    assert "pkg/secret/**" in request["forbidden_paths"] and ".github/**" in request["forbidden_paths"]
    assert "pitch analysis contracts" in request["objective"] and ROADMAP in request["objective"]
    assert [r["id"] for r in report["capable_requests"]] == [ident] and report["local_requests"] == []


def test_a_graph_whose_only_slice_is_done_is_planned_the_same_way(tmp_path: Path) -> None:
    repository = PORTFOLIO[4]  # Project-Everward: capability still PLANNED, its only slice VERIFIED_COMPLETE
    clone, head = with_roadmap(tmp_path, repository, complete_graph(repository, planned_capability=True))

    report = run_refill(tmp_path, repository, clone, head)

    assert len(report["requests"]) == 1 and report["direction_required"] == []
    assert report["routed"][refill.CAPABLE] == report["requests"]


def test_the_plan_id_is_stable_until_the_graph_or_the_roadmap_changes(tmp_path: Path) -> None:
    repository = PORTFOLIO[5]
    payload = complete_graph(repository, planned_capability=False)
    clone, head = with_roadmap(tmp_path, repository, payload)
    first = run_refill(tmp_path, repository, clone, head)["requests"]
    again = run_refill(tmp_path, repository, clone, head)["requests"]
    # an unrelated commit moves the head but changes neither the graph nor the roadmap
    (clone / "pkg" / "other.py").write_text("x = 1\n", encoding="utf-8")
    run_git(clone, "add", ".")
    run_git(clone, "commit", "-q", "-m", "unrelated")
    moved = run_refill(tmp_path, repository, clone, run_git(clone, "rev-parse", "HEAD"))["requests"]
    assert first == again == moved

    (clone / ROADMAP).write_text(GRANT + "3. A newly added outcome.\n", encoding="utf-8")
    run_git(clone, "commit", "-qam", "roadmap revised")
    revised = run_refill(tmp_path, repository, clone, run_git(clone, "rev-parse", "HEAD"))["requests"]
    assert revised != first


def test_without_an_owner_roadmap_the_owner_is_still_asked(tmp_path: Path) -> None:
    repository = PORTFOLIO[5]
    clone, head = with_roadmap(tmp_path, repository, complete_graph(repository, planned_capability=False), text=None)

    report = run_refill(tmp_path, repository, clone, head)

    assert report["requests"] == [] and report["direction_required"][0]["reason"] == "PRODUCT_COMPLETION_EVIDENCE_REQUIRED"


def test_a_roadmap_that_does_not_carry_the_standing_grant_is_not_authority(tmp_path: Path) -> None:
    repository = PORTFOLIO[5]
    for text in ("# Roadmap\n\nStatus: DRAFT\n\n## Standing development authorization\n\n## Next slices\n1. x\n",
                 "# Roadmap\n\nStatus: OWNER-APPROVED\n\n## Next slices\n1. x\n"):
        sub = tmp_path / str(abs(hash(text)))
        clone, head = with_roadmap(sub, repository, complete_graph(repository, planned_capability=False), text=text)
        report = run_refill(sub, repository, clone, head)
        assert report["requests"] == [] and report["direction_required"], text


def test_a_plan_the_capable_builder_already_failed_twice_comes_back_to_the_owner(tmp_path: Path) -> None:
    repository = PORTFOLIO[5]
    clone, head = with_roadmap(tmp_path, repository, complete_graph(repository, planned_capability=False))
    (ident,) = run_refill(tmp_path, repository, clone, head)["requests"]
    state = capable.dispatch_state(tmp_path / "lane")
    state.mkdir(parents=True, exist_ok=True)
    for attempt in range(capable.MAX_ATTEMPTS):
        (state / f"d{attempt}.json").write_text(json.dumps({"task_key": f"{ident}#a{attempt}", "status": "FAILED",
                                                            "repository": repository, "started_at": 1.0}), encoding="utf-8")
    (tmp_path / "lane" / "roadmap-refill.json").unlink()  # force a fresh pass

    report = run_refill(tmp_path, repository, clone, head)

    assert report["requests"] == []
    (row,) = report["direction_required"]
    assert row["reason"] == "PRODUCT_COMPLETION_EVIDENCE_REQUIRED" and any("capable attempts spent" in e for e in row["evidence"])


def test_planning_requests_in_flight_or_delivered_are_not_requested_again() -> None:
    row = {"reason": "NO_EXECUTABLE_SLICE", "evidence": []}
    payload = complete_graph(PORTFOLIO[4], planned_capability=True)
    kwargs = {"repository": PORTFOLIO[4], "head": "a" * 40, "source": SOURCE, "payload": payload, "roadmap": (ROADMAP, GRANT)}
    (first,), _, _ = refill.continue_without_owner([row], pending=frozenset(), done=frozenset(), spent=frozenset(), **kwargs)
    for pending, done, note in ((frozenset({first["id"]}), frozenset(), "PENDING_CAPABLE_BUILDER"),
                                (frozenset(), frozenset({first["id"]}), "DONE")):
        requests, remaining, notes = refill.continue_without_owner([row], pending=pending, done=done, spent=frozenset(), **kwargs)
        assert requests == [] and remaining == [] and notes == [{"id": first["id"], "reason": note}]


# --- an exhausted slice does not end the product ----------------------------------------------------------------

def exhausted_row(repository: str) -> dict:
    return {"capability": "cap-cache", "slice": "s1", "reason": "REAUTHOR_LIMIT_REACHED",
            "id": refill.item_id(repository, "cap-cache", "s1") + "-r3",
            "evidence": ["RM-x: FAIL after 2 attempts", "RM-x-r1: REVIEW_REQUIRED after 3 attempts"]}


def test_a_slice_that_spent_every_local_revision_is_escalated_once_to_the_capable_builder() -> None:
    repository = PORTFOLIO[5]
    payload = graph(repository)
    kwargs = {"repository": repository, "head": "a" * 40, "source": SOURCE, "payload": payload, "roadmap": None}
    requests, remaining, _ = refill.continue_without_owner([exhausted_row(repository)], pending=frozenset(), done=frozenset(),
                                                           spent=frozenset(), **kwargs)
    (request,) = requests
    base = refill.item_id(repository, "cap-cache", "s1")
    assert remaining == [] and request["id"] == base + "-cap"
    assert [r["id"] for r in request["prior_failures"]] == ["RM-x", "RM-x-r1"]
    assert request["allowed_paths"] == ["pkg/**", "tests/**"] and request["forbidden_paths"] == ["pkg/secret/**"]
    routed = refill.route(request, profile=write_profile(Path(__import__("tempfile").mkdtemp())), max_level=2)
    assert routed["builder"] == refill.CAPABLE and routed["reason"] == "LOCAL_BUILDER_EXHAUSTED"  # even though level 1

    spent = refill.continue_without_owner([exhausted_row(repository)], pending=frozenset(), done=frozenset(),
                                          spent=frozenset({request["id"]}), **kwargs)
    assert spent[0] == [] and spent[1][0]["reason"] == "REAUTHOR_LIMIT_REACHED"  # both builders failed: the owner decides
    assert any("capable attempts spent" in e for e in spent[1][0]["evidence"])


def test_a_merged_escalation_completes_the_slice_even_though_its_local_chain_failed() -> None:
    repository = PORTFOLIO[0]
    first = request_for(repository)
    base = first["id"]
    item = refill.stamp({"id": base, "repository": repository, "title": "t", "roadmap_ref": first["roadmap_ref"]}, first, SOURCE)
    state = {"items": {base: {"attempts": 3, "outcome": "FAIL: SLICE_TESTS_FAILED"}}}
    assert refill._slice_status(base, [item], state)[0] == "FAILED"
    assert refill._slice_status(base, [item], state, frozenset({base + refill.ESCALATION_SUFFIX})) == ("DONE", base + "-cap")


def test_an_exhausted_slice_without_allowed_paths_is_not_escalated_on_guesswork() -> None:
    repository = PORTFOLIO[5]
    payload = graph(repository)
    del payload["capabilities"][0]["slices"][0]["allowed_paths"]
    requests, remaining, _ = refill.continue_without_owner(
        [exhausted_row(repository)], repository=repository, head="a" * 40, source=SOURCE, payload=payload, roadmap=None,
        pending=frozenset(), done=frozenset(), spent=frozenset())
    assert requests == [] and remaining[0]["reason"] == "REAUTHOR_LIMIT_REACHED"


def test_one_blocked_repository_does_not_stop_planning_for_another(tmp_path: Path) -> None:
    blocked, working = PORTFOLIO[3], PORTFOLIO[5]
    clone_a, head_a = with_roadmap(tmp_path / "a", blocked, complete_graph(blocked, planned_capability=False), text=None)
    clone_b, head_b = with_roadmap(tmp_path / "b", working, complete_graph(working, planned_capability=False))
    lane, state = tmp_path / "lane", tmp_path / "flow-state"
    lane.mkdir()
    state.mkdir()
    write_profile(state)
    one = refill.refill_repository(lane, state, blocked, clone_a, head_a, items=[], state={}, reserved=set(), max_level=2)
    two = refill.refill_repository(lane, state, working, clone_b, head_b, items=[], state={}, reserved=set(), max_level=2)
    assert one["requests"] == [] and one["direction_required"]
    assert len(two["requests"]) == 1 and two["direction_required"] == []


def test_undeclared_levels_go_to_the_capable_builder_and_declared_simple_levels_stay_local(tmp_path: Path) -> None:
    """Rocksmith's slice declares no level: that is routed to the strong builder, never guessed down to the local one."""
    profile = write_profile(tmp_path)
    undeclared = {"capability_level": None, "id": "x"}
    assert refill.route(undeclared, profile=profile, max_level=2)["builder"] == refill.CAPABLE
    assert refill.route({"capability_level": 3}, profile=profile, max_level=2)["builder"] == refill.CAPABLE
    assert refill.route({"capability_level": 1}, profile=profile, max_level=2)["builder"] == refill.LOCAL


# --- the instruction the planner is given must produce a graph the compiler can execute (Codex review of #634) --------

def _appended(payload: dict, *, into_open_capability: bool, replace_roadmap: bool = True) -> dict:
    import copy

    plan = copy.deepcopy(payload)
    new_slice = {"id": "next", "state": "PLANNED", "capability_level": 3, "objective": "Do the next bounded thing.",
                 "acceptance": ["it works"], "allowed_paths": ["pkg/**"], "forbidden_paths": [], "runner_requirements": ["code"]}
    if into_open_capability:
        plan["capabilities"][0]["slices"].append(new_slice)
    else:
        plan["capabilities"].append({"id": "cap-next", "capability": "next", "state": "PLANNED", "depends_on": [],
                                     "completion_evidence": ["docs/ROADMAP.md"], "slices": [new_slice]})
        plan["roadmap"] = ["cap-next"] if replace_roadmap else [*plan["roadmap"], "cap-next"]
    return plan


def test_both_graph_shapes_have_a_planned_edit_the_compiler_actually_executes() -> None:
    repository = PORTFOLIO[4]
    open_capability = complete_graph(repository, planned_capability=True)  # Everward: PLANNED capability, all slices done
    done = complete_graph(repository, planned_capability=False)  # VoiceStudio: every capability complete with evidence

    # Why the instruction is shape-dependent: an appended capability behind an unfinished one is never selected.
    ignored = refill.derive(repository, "a" * 40, _appended(open_capability, into_open_capability=False, replace_roadmap=False), SOURCE, items=[], state={})
    assert ignored["requests"] == [] and ignored["direction_required"][0]["reason"] == "NO_EXECUTABLE_SLICE"

    for payload, into_open in ((open_capability, True), (done, False)):
        derived = refill.derive(repository, "a" * 40, _appended(payload, into_open_capability=into_open), SOURCE, items=[], state={})
        assert [r["slice"] for r in derived["requests"]] == ["next"] and derived["direction_required"] == []


def test_the_planning_instruction_names_both_edits_and_forbids_false_completion() -> None:
    request = refill.planning_request(PORTFOLIO[4], "a" * 40, SOURCE, complete_graph(PORTFOLIO[4], planned_capability=True), ROADMAP, GRANT)
    text = request["objective"]
    assert "append the new slices to THAT capability" in text and "append a new capability" in text
    assert "without evidence" in text and "never edit or remove an existing slice" in text
    assert any("executable slice" in item for item in request["slice_acceptance"])


def test_an_open_pull_request_that_owns_the_graph_blocks_every_request_because_a_session_may_edit_it(tmp_path: Path) -> None:
    """A capable session may record an already-delivered slice in the graph, so the graph is part of the conflict scope."""
    repository = PORTFOLIO[4]
    clone, head = with_roadmap(tmp_path, repository, graph(repository))

    report = run_refill(tmp_path, repository, clone, head, reserved={SOURCE})

    (ident,) = report["requests"]
    request = json.loads((tmp_path / "lane" / refill.REQUESTS / f"{ident}.json").read_text("utf-8"))
    assert SOURCE not in request["allowed_paths"]  # the slice itself never allowed the graph...
    assert request["reserved_conflicts"] == [SOURCE]  # ...yet its ownership blocks dispatch
    assert request["graph_path"] == SOURCE
    unowned = run_refill(tmp_path, repository, clone, head, reserved=set())["requests"][0]
    assert json.loads((tmp_path / "lane" / refill.REQUESTS / f"{unowned}.json").read_text("utf-8"))["reserved_conflicts"] == []
