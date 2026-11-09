"""Why an idle product lane is idle: every candidate carries the reason it was excluded, and the reasons are the dispatch rules."""

from __future__ import annotations

import json
from pathlib import Path

from project_orchestrator import iron_god_backlog as backlog
from project_orchestrator import iron_god_roadmap_refill as refill

REPO = "jweter/VoiceStudio"


def item(item_id: str, **over) -> dict:
    base = {"id": item_id, "repository": REPO, "title": "t", "roadmap_ref": "r", "addresses": "a", "why": "w",
            "consumer": "the export workflow step", "objective": "o", "verification": "v", "definition_of_done": "d",
            "complexity": "small", "interfaces": [], "depends_on": [], "acceptance_criteria": ["a"], "tests": ["t"],
            "status": "approved", "level": 1, "assignee": "iron-god",
            "build": {"module_path": "pkg/m.py", "function_name": "f", "signature": "def f() -> int",
                      "objective": "o", "examples": [{"call": "f()", "expected": 1}]}}
    return {**base, **over}


def reasons(items: list[dict], state: dict | None = None, max_level: int = 1) -> dict[str, str]:
    return {row["id"]: row["reason"] for row in backlog.explain_candidates(items, state or {"items": {}}, max_level=max_level)}


def test_an_approved_buildable_item_is_eligible_and_is_the_one_dispatched() -> None:
    items = [item("A")]
    assert reasons(items) == {"A": "ELIGIBLE"}
    assert backlog.next_item(items, {"items": {}}, REPO, max_level=1)["id"] == "A"


def test_every_exclusion_has_a_named_reason() -> None:
    direct = {"module_path": "pkg/m.py"}
    elsewhere_build = {**item("x")["build"], "module_path": "other/theirs.py"}  # their own file: no reservation clash
    items = [item("PROPOSED", status="proposed"), item("ELSEWHERE", assignee="another-agent", build=elsewhere_build),
             item("TOO_HIGH", level=2, direct=direct), item("DONE", status="done"),
             item("WAITS", depends_on=["PROPOSED"]), item("SPENT"), item("SAME"), item("FLYING"),
             item("TRIED", level=2, direct=direct)]
    state = {"items": {
        "SPENT": {"attempts": backlog.MAX_ITEM_ATTEMPTS, "outcome": "FAIL: SLICE_TESTS_FAILED"},
        "SAME": {"attempts": 2, "outcome": "FAIL: x", "failed_source_by_packet": {"p1": "f" * 8, "p2": "f" * 8}},
        "FLYING": {"attempts": 1, "outcome": "DISPATCHED"},
        "TRIED": {"attempts": 1, "outcome": "FAIL: SLICE_TESTS_FAILED"}}}
    assert reasons(items, state, max_level=2) == {
        "PROPOSED": "NOT_APPROVED", "ELSEWHERE": "ASSIGNED_ELSEWHERE", "TOO_HIGH": "ELIGIBLE", "DONE": "ALREADY_COMPLETE",
        "WAITS": "WAITING_ON_DEPENDENCY", "SPENT": "ATTEMPTS_EXHAUSTED", "SAME": "REPEATED_FAILED_SOURCE",
        "FLYING": "IN_FLIGHT", "TRIED": "DIRECTED_ATTEMPT_SPENT"}
    assert reasons([items[2]], max_level=1) == {"TOO_HIGH": "LEVEL_ABOVE_MEASURED_CAPABILITY"}


def test_an_item_whose_files_another_developer_has_reserved_says_which_one() -> None:
    items = [item("MINE"), item("THEIRS", assignee="another-agent")]  # both edit pkg/m.py
    rows = {row["id"]: row for row in backlog.explain_candidates(items, {"items": {}}, max_level=1)}
    assert rows["MINE"]["reason"] == "FILES_RESERVED_BY_OTHER_DEVELOPER" and rows["MINE"]["detail"] == "THEIRS"
    assert backlog.next_item(items, {"items": {}}, REPO, max_level=1) is None


def test_a_failed_slice_with_unchanged_source_is_not_retried_but_the_reason_says_so() -> None:
    items = [item("SAME")]
    state = {"items": {"SAME": {"attempts": 2, "outcome": "FAIL: canonical preflight failed",
                                "failed_source_by_packet": {"p1": "aaaa", "p2": "aaaa"}}}}
    assert backlog.next_item(items, state, REPO, max_level=1) is None
    (row,) = backlog.explain_candidates(items, state, max_level=1)
    assert row["reason"] == "REPEATED_FAILED_SOURCE" and "canonical preflight failed" in row["detail"] and row["attempts"] == 2


def test_the_explanation_and_the_dispatch_decision_never_disagree() -> None:
    items = [item("A", status="proposed"), item("B", depends_on=["A"]), item("C", level=2, direct={"module_path": "p"}),
             item("D"), item("E", assignee="other")]
    state = {"items": {"D": {"attempts": 1, "outcome": "DISPATCHED"}}}
    for max_level in (1, 2):
        eligible = [r["id"] for r in backlog.explain_candidates(items, state, max_level=max_level) if r["eligible"]]
        chosen = backlog.next_item(items, state, REPO, max_level=max_level)
        assert (chosen["id"] if chosen else None) == (eligible[0] if eligible else None)


def test_an_empty_backlog_recovers_when_valid_work_appears() -> None:
    state = {"items": {}}
    assert backlog.next_item([], state, REPO, max_level=1) is None
    assert backlog.next_item([item("NEW")], state, REPO, max_level=1)["id"] == "NEW"
    assert backlog.next_item([item("NEW", status="proposed")], state, REPO, max_level=1) is None  # not approved: still idle


def test_a_merged_item_unblocks_its_dependent() -> None:
    items = [item("A"), item("B", depends_on=["A"])]
    assert reasons(items)["B"] == "WAITING_ON_DEPENDENCY"
    assert reasons(items, {"items": {"A": {"attempts": 1, "outcome": "MERGED"}}}) == {"A": "ALREADY_COMPLETE", "B": "ELIGIBLE"}


def test_the_summary_counts_reasons_per_repository_and_omits_finished_work() -> None:
    items = [item("A", status="proposed"), item("B", status="proposed"), item("C", status="retired"), item("D", status="done"),
             item("E"), item("F", repository="jweter/other", assignee="x")]
    summary = backlog.summarize_exclusions(backlog.explain_candidates(items, {"items": {}}, max_level=1))
    assert summary[REPO] == {"eligible": ["E"], "excluded": {"NOT_APPROVED": 2}}
    assert summary["jweter/other"] == {"eligible": [], "excluded": {"ASSIGNED_ELSEWHERE": 1}}


# --- roadmap requests ---------------------------------------------------------------------------------------------

def write_request(lane: Path, ident: str, head: str, builder: str = "CAPABLE", reason: str = "LEVEL_BEYOND_PRODUCT_LANE") -> None:
    directory = lane / refill.REQUESTS
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{ident}.json").write_text(json.dumps({
        "id": ident, "repository": REPO, "exact_head": head, "route": {"builder": builder, "reason": reason}}), encoding="utf-8")


def test_request_status_names_what_each_open_request_is_waiting_for(tmp_path: Path) -> None:
    write_request(tmp_path, "RM-A", "a" * 40)
    write_request(tmp_path, "RM-B", "b" * 40, builder="LOCAL", reason="WITHIN_MEASURED_LOCAL_CAPABILITY")
    refill.log_event(tmp_path, "CAPABLE_NOT_DISPATCHED", REPO, "RM-A", {"dispatch": False, "reason": "DAILY_BUDGET_EXHAUSTED"})
    rows = {row["id"]: row for row in refill.request_status(tmp_path)}
    assert rows["RM-A"]["builder"] == "CAPABLE" and rows["RM-A"]["blocked_by"] == "DAILY_BUDGET_EXHAUSTED"
    assert rows["RM-A"]["route_reason"] == "LEVEL_BEYOND_PRODUCT_LANE"
    assert rows["RM-B"]["blocked_by"] is None and rows["RM-B"]["builder"] == "LOCAL"


def test_a_later_dispatch_clears_the_budget_blocker(tmp_path: Path) -> None:
    write_request(tmp_path, "RM-A", "a" * 40)
    refill.log_event(tmp_path, "CAPABLE_NOT_DISPATCHED", REPO, "RM-A", {"reason": "DAILY_BUDGET_EXHAUSTED"})
    refill.log_event(tmp_path, "CAPABLE_DISPATCHED", REPO, "RM-A", {"reason": "DISPATCHED"})
    (row,) = refill.request_status(tmp_path)
    assert row["last_event"] == "CAPABLE_DISPATCHED" and row["blocked_by"] is None


def test_request_status_with_no_requests_or_damaged_files_is_empty_not_an_error(tmp_path: Path) -> None:
    assert refill.request_status(tmp_path) == []
    (tmp_path / refill.REQUESTS).mkdir()
    (tmp_path / refill.REQUESTS / "bad.json").write_text("{not json", encoding="utf-8")
    assert refill.request_status(tmp_path) == []


def test_a_request_older_than_the_checked_head_is_flagged_stale(tmp_path: Path) -> None:
    """A stale-head request is shown as such; the refill re-derives it on the current head rather than building it silently."""
    write_request(tmp_path, "RM-OLD", "a" * 40)
    write_request(tmp_path, "RM-NEW", "b" * 40)
    refill._mutate(tmp_path, lambda store: store["checked"].update({REPO: {"at": "2026-11-04T00:00:00+00:00", "head": "b" * 40}}))
    rows = {row["id"]: row for row in refill.request_status(tmp_path)}
    assert rows["RM-OLD"]["head_is_current"] is False and rows["RM-NEW"]["head_is_current"] is True


def test_admission_drops_a_revision_that_does_not_prove_its_authorized_level(tmp_path: Path) -> None:
    """A re-authored slice (-r1) only becomes eligible through the refill's stamped authorization, never by assertion."""
    good = item("RM-X-r1", origin=refill.ORIGIN, authorization={"declared_capability_level": 1})
    forged = item("RM-X-r2", origin=refill.ORIGIN)  # no authorization
    understated = item("RM-X-r3", origin=refill.ORIGIN, authorization={"declared_capability_level": 2})
    hand_made = item("RM-X-r4", authorization={"declared_capability_level": 1})  # not roadmap-derived
    refill._mutate(tmp_path, lambda store: store["items"].extend([good, forged, understated, hand_made]))
    assert [i["id"] for i in refill.admitted_items(tmp_path)] == ["RM-X-r1"]


FAILED = "FAIL: SLICE_TESTS_FAILED"


def test_a_plan_step_that_keeps_failing_its_tests_is_capped_across_its_repairs() -> None:
    """Issue #673: t1, t1-r1, t1-r2, t1-r3 each had their own 2 attempts, so one step burned 8 packets on the 7B."""
    items = [item("RM-x-t1"), item("RM-x-t1-r1"), item("RM-x-t1-r2"), item("RM-x-t1-r3"), item("RM-x-t2")]
    state = {"items": {"RM-x-t1": {"attempts": 2, "outcome": FAILED}, "RM-x-t1-r1": {"attempts": 1, "outcome": FAILED}}}
    got = reasons(items, state)
    assert got["RM-x-t1-r1"] == "NEEDS_DIFFERENT_CAPABILITY"
    assert got["RM-x-t1-r2"] == got["RM-x-t1-r3"] == "NEEDS_DIFFERENT_CAPABILITY"  # new repairs never start
    assert got["RM-x-t2"] == "ELIGIBLE"  # a distinct, independent step is untouched
    (row,) = [r for r in backlog.explain_candidates(items, state, max_level=1) if r["id"] == "RM-x-t1-r2"]
    assert "3 slice-test failures across RM-x-t1" in row["detail"]


def test_the_lineage_cap_counts_only_slice_test_failures_and_never_touches_in_flight_work() -> None:
    items = [item("RM-y-t1"), item("RM-y-t1-r1")]
    env_failures = {"items": {"RM-y-t1": {"attempts": 2, "outcome": "ENVIRONMENT_FAILURE: x"},
                              "RM-y-t1-r1": {"attempts": 1, "outcome": "FAIL: memory"}}}
    assert reasons(items, env_failures)["RM-y-t1-r1"] != "NEEDS_DIFFERENT_CAPABILITY"
    two = {"items": {"RM-y-t1": {"attempts": 2, "outcome": FAILED}}}
    assert reasons(items, two)["RM-y-t1-r1"] == "ELIGIBLE"  # below the cap: the first repair still runs
    flying = {"items": {"RM-y-t1": {"attempts": 2, "outcome": FAILED}, "RM-y-t1-r1": {"attempts": 1, "outcome": "DISPATCHED"}}}
    assert reasons(items, flying)["RM-y-t1-r1"] == "IN_FLIGHT"


def test_the_refill_stops_re_authoring_a_capped_step_and_escalates_it_to_the_owner() -> None:
    from project_orchestrator import iron_god_roadmap_refill as refill

    items = [item("RM-z-t1"), item("RM-z-t1-r1")]
    state = {"items": {"RM-z-t1": {"attempts": 2, "outcome": FAILED}, "RM-z-t1-r1": {"attempts": 1, "outcome": FAILED}}}
    assert refill._slice_status("RM-z-t1", items, state) == ("EXHAUSTED", "RM-z-t1-r1")  # not FAILED -> no -r2 is authored
