"""A LOCAL slice the local lane cannot verify goes to the capable builder instead of waiting forever."""

from __future__ import annotations

from project_orchestrator import iron_god_capable_builder as capable
from project_orchestrator import iron_god_local_drafter as drafter
from project_orchestrator import iron_god_product_lane as lane
from project_orchestrator import iron_god_roadmap_refill as refill

EXAMPLES = [{"call": "{fn}(1)", "expected": 2, "why": "adds one"}, {"call": "{fn}(2)", "expected": 3, "why": "adds one"}]


def request(ident: str, *, builder: str = refill.LOCAL, level: int = 1, examples=None) -> dict:
    return {"id": ident, "repository": "jweter/knowledge-engine-core", "slice": ident, "capability_level": level,
            "acceptance_examples": examples if examples is not None else [],
            "route": {"builder": builder, "level": level, "reason": "WITHIN_LOCAL_CAPABILITY"}}


def test_a_local_slice_without_examples_is_rerouted_and_one_with_examples_stays_local() -> None:
    """KE-core core-online-evidence-failure-classification and VoiceStudio s5-full-render-codec-args, 2026-11-15."""
    report = {"local_requests": [request("RM-bare"), request("RM-ready", examples=EXAMPLES),
                                  request("RM-directed", level=2)],
              "capable_requests": [request("RM-big", builder=refill.CAPABLE, level=3)]}

    moved = lane.reroute_slices_the_local_lane_cannot_verify(report)

    assert [r["id"] for r in moved] == ["RM-bare"]
    assert [r["id"] for r in report["local_requests"]] == ["RM-ready", "RM-directed"]   # only the unverifiable one moves
    assert [r["id"] for r in report["capable_requests"]] == ["RM-big", "RM-bare"]
    route = moved[0]["route"]
    assert route["builder"] == refill.CAPABLE and route["was"] == "LOCAL"
    assert route["reason"] == "LOCAL_SLICE_WITHOUT_ACCEPTANCE_EXAMPLES"
    assert not drafter.missing_examples(report["local_requests"][0])                    # nothing left to be refused


def test_the_capable_builder_accepts_the_rerouted_request_and_nothing_changes_when_nothing_needs_moving(tmp_path) -> None:
    report = {"local_requests": [request("RM-bare")], "capable_requests": []}
    (moved,) = lane.reroute_slices_the_local_lane_cannot_verify(report)
    moved = {**moved, "exact_head": "a" * 40, "allowed_paths": ["knowledge_engine/**"], "forbidden_paths": [],
             "roadmap_ref": "g#x", "objective": "Do it.", "slice_acceptance": ["it works"]}
    decision = capable.decide(moved, lane_root=tmp_path, enabled=True, transport=capable.ENGINE_TRANSPORT,
                              which=lambda _name: "claude.exe", environ={})
    assert decision == {"dispatch": True, "reason": "WITHIN_BUDGET"}

    untouched = {"local_requests": [request("RM-ready", examples=EXAMPLES)], "capable_requests": []}
    assert lane.reroute_slices_the_local_lane_cannot_verify(untouched) == []
    assert [r["id"] for r in untouched["local_requests"]] == ["RM-ready"] and untouched["capable_requests"] == []
    assert lane.reroute_slices_the_local_lane_cannot_verify({}) == []



def test_previously_admitted_local_draft_must_never_be_rerouted() -> None:
    """The actual full-lane regression: process_drafts accepted the owner draft."""
    report = {
        "local_requests": [
            request("RM-approved-local"),
            request("RM-truly-unverifiable"),
        ],
        "admitted": ["RM-approved-local"],
        "capable_requests": [],
    }

    moved = lane.reroute_slices_the_local_lane_cannot_verify(report)

    assert [row["id"] for row in moved] == ["RM-truly-unverifiable"]
    assert [row["id"] for row in report["local_requests"]] == ["RM-approved-local"]
    assert [row["id"] for row in report["capable_requests"]] == [
        "RM-truly-unverifiable"
    ]
    assert not any(row["id"] == "RM-approved-local" for row in moved)


def test_only_admitted_local_request_keeps_existing_ledger_sequence() -> None:
    report = {
        "local_requests": [request("RM-approved-local")],
        "admitted": ["RM-approved-local"],
        "capable_requests": [],
    }
    assert lane.reroute_slices_the_local_lane_cannot_verify(report) == []
    assert [row["id"] for row in report["local_requests"]] == ["RM-approved-local"]
    assert report["capable_requests"] == []



def test_accepted_local_work_remains_local_across_subsequent_refill_passes() -> None:
    """A previously accepted draft is not admitted again, but stays protected."""
    previous_accepted = frozenset({"RM-approved-from-previous-pass"})
    report = {
        "local_requests": [
            request("RM-approved-from-previous-pass"),
            request("RM-unverifiable"),
        ],
        "admitted": [],
        "capable_requests": [],
    }
    moved = lane.reroute_slices_the_local_lane_cannot_verify(
        report, already_local=previous_accepted
    )
    assert [row["id"] for row in moved] == ["RM-unverifiable"]
    assert [row["id"] for row in report["local_requests"]] == [
        "RM-approved-from-previous-pass"
    ]
    assert [row["id"] for row in report["capable_requests"]] == [
        "RM-unverifiable"
    ]
