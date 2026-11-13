"""The local-slice validator applies the engine's own rules before a slice is committed."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from project_orchestrator import iron_god_roadmap_refill as refill

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("validate_local_slice", ROOT / "scripts" / "iron_god_validate_local_slice.py")
assert _spec and _spec.loader
validator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(validator)

EXAMPLES = [
    {"call": "{fn}(1.5)", "expected": 1500, "why": "1.5 seconds is 1500 milliseconds"},
    {"call": "{fn}(0.0005)", "expected": 1, "why": "half a millisecond rounds half up to 1"},
    {"call": "{fn}(0.0)", "expected": 0, "why": "zero stays zero"},
]
FORBIDDEN = [".github/**", "private/**", ".env"]


def graph_with(slice_overrides: dict[str, Any] | None = None, *, extra_slices: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    local = {
        "id": "local-seconds-to-ms", "state": "PLANNED", "capability_level": 1, "depends_on": [],
        "objective": "Add one pure helper that converts a duration in seconds to whole milliseconds, rounding half up.",
        "acceptance": ["the helper returns 1500 for 1.5 seconds", "it rounds half up"],
        "acceptance_examples": EXAMPLES, "allowed_paths": ["pkg/**", "tests/**"], "forbidden_paths": FORBIDDEN,
        "runner_requirements": ["code", "local", "offline"], "runtime_requirements": [],
    }
    local.update(slice_overrides or {})
    return {"schema_version": 1, "repository": "jweter/example", "roadmap": ["cap"],
            "capabilities": [{"id": "cap", "state": "PLANNED", "capability": "an example capability", "completion_evidence": ["x"],
                              "depends_on": [], "slices": [local, *(extra_slices or [])]}]}


@pytest.fixture(autouse=True)
def measured_local_capability(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand in for the measured profile: the model is calibrated through Level 2 on this hardware."""
    monkeypatch.setattr(refill, "capability_gate", lambda profile, request, model=None: None)


def check(graph: dict[str, Any], slice_id: str = "local-seconds-to-ms") -> dict[str, Any]:
    return validator.check_slice(graph, slice_id, "jweter/example", profile={}, max_level=2)


def test_a_well_formed_level_1_slice_passes_and_routes_local() -> None:
    result = check(graph_with())
    assert result["problems"] == [] and result["executable_now"] is True
    assert any("routes LOCAL" in note for note in result["notes"])


def test_an_unknown_slice_is_reported() -> None:
    assert check(graph_with(), "nope")["problems"] == ["slice 'nope' is not in the graph"]


@pytest.mark.parametrize("level", [3, 4, None])
def test_only_levels_1_and_2_are_local_work(level: int | None) -> None:
    problems = check(graph_with({"capability_level": level}))["problems"]
    assert any("capability_level must be 1 or 2" in p for p in problems)


def test_examples_must_be_authored_not_left_for_the_model() -> None:
    assert any("needs acceptance_examples authored by a stronger agent" in p for p in check(graph_with({"acceptance_examples": []}))["problems"])
    assert any("at least 2 usable" in p for p in check(graph_with({"acceptance_examples": EXAMPLES[:1]}))["problems"])


def test_a_malformed_or_unexplained_example_is_named() -> None:
    bad = [*EXAMPLES[:2], {"call": "seconds_to_ms(2)", "expected": 2000, "why": "missing the {fn} placeholder"},
           {"call": "{fn}(3)", "expected": 3000, "why": ""}]
    assert any("every acceptance example needs" in p for p in check(graph_with({"acceptance_examples": bad}))["problems"])


def test_contradictory_examples_are_caught_before_the_model_sees_them() -> None:
    contradictory = [*EXAMPLES, {"call": "{fn}(1.5)", "expected": 1501, "why": "a second, different answer"}]
    assert any("contradictory expected values for {fn}(1.5)" in p for p in check(graph_with({"acceptance_examples": contradictory}))["problems"])


def test_a_path_placeholder_example_is_rejected_like_the_engine_does() -> None:
    placeholder = [{"call": "{fn}('path/to/take.wav')", "expected": 1, "why": "x"}, {"call": "{fn}('path/to/b.wav')", "expected": 2, "why": "y"}]
    assert any("path/to/" in p for p in check(graph_with({"acceptance_examples": placeholder}))["problems"])


def test_text_longer_than_the_worker_bound_is_rejected() -> None:
    problems = check(graph_with({"objective": "x" * 1201, "acceptance": ["ok", "y" * 1300]}))["problems"]
    assert any(p.startswith("objective is 1201") for p in problems) and any(p.startswith("acceptance[1] is 1300") for p in problems)


def test_a_slice_with_no_place_for_a_new_module_is_rejected() -> None:
    for paths in ([], ["tests/**", "docs/**"], ["engineering/capability-graph.json"]):
        problems = check(graph_with({"allowed_paths": paths}))["problems"]
        assert problems, paths


def test_a_level_2_slice_needs_a_complete_directed_edit() -> None:
    problems = check(graph_with({"capability_level": 2, "acceptance_examples": [], "directed_edit": None}))["problems"]
    assert any("complete directed_edit" in p for p in problems)


def test_a_slice_blocked_behind_an_earlier_one_is_a_note_not_a_failure() -> None:
    first = {"id": "first", "state": "PLANNED", "capability_level": 3, "depends_on": [], "objective": "an earlier big slice",
             "acceptance": ["done"], "allowed_paths": ["pkg/**"], "forbidden_paths": FORBIDDEN,
             "runner_requirements": ["code", "local", "offline"], "runtime_requirements": []}
    graph = graph_with({"depends_on": ["first"]}, extra_slices=[first])
    result = check(graph)
    assert result["problems"] == [] and result["executable_now"] is False
    assert any("not next in its capability yet" in note for note in result["notes"])


def test_a_level_the_measured_profile_does_not_cover_is_routed_to_the_capable_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(refill, "capability_gate", lambda profile, request, model=None: "LEVEL_NOT_CALIBRATED")
    problems = check(graph_with())["problems"]
    assert any("would route this slice to CAPABLE" in p and "LEVEL_NOT_CALIBRATED" in p for p in problems)


def test_the_command_line_prints_pass_or_fail_and_sets_the_exit_status(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    good, bad = tmp_path / "good.json", tmp_path / "bad.json"
    good.write_text(json.dumps(graph_with()), encoding="utf-8")
    bad.write_text(json.dumps(graph_with({"acceptance_examples": []})), encoding="utf-8")
    assert validator.main(["--graph", str(good), "--repository", "jweter/example", "--slice", "local-seconds-to-ms"]) == 0
    assert "PASS local-seconds-to-ms" in capsys.readouterr().out
    assert validator.main(["--graph", str(bad), "--repository", "jweter/example", "--slice", "local-seconds-to-ms"]) == 1
    out = capsys.readouterr().out
    assert "PROBLEM:" in out and "FAIL local-seconds-to-ms" in out


def test_a_slice_the_engine_would_reject_for_incomplete_authority_does_not_pass() -> None:
    """Review (Codex P2): an empty acceptance list made derive() report SLICE_AUTHORITY_INCOMPLETE but the check printed PASS."""
    result = check(graph_with({"acceptance": []}))
    assert any("SLICE_AUTHORITY_INCOMPLETE" in p for p in result["problems"])


def test_the_authority_check_examines_the_slice_even_when_other_capabilities_sit_ahead_of_it() -> None:
    ahead = {"id": "cap-ahead", "state": "PLANNED", "capability": "an earlier capability", "completion_evidence": ["x"], "depends_on": [],
             "slices": [{"id": "ahead-1", "state": "PLANNED", "capability_level": 3, "depends_on": [], "objective": "big",
                         "acceptance": ["done"], "allowed_paths": ["pkg/**"], "forbidden_paths": FORBIDDEN,
                         "runner_requirements": ["code"], "runtime_requirements": []}]}
    graph = graph_with({"acceptance": []})
    graph["capabilities"].insert(0, ahead)
    graph["roadmap"] = ["cap-ahead", "cap"]
    assert any("SLICE_AUTHORITY_INCOMPLETE" in p for p in check(graph)["problems"])


def test_an_invalid_graph_is_a_problem_not_a_note() -> None:
    graph = graph_with()
    graph["roadmap"] = []
    assert any("CAPABILITY_GRAPH_INVALID" in p for p in check(graph)["problems"])


def test_a_level_1_objective_longer_than_the_builder_is_shown_is_rejected() -> None:
    """2026-11-13: a rule the local builder never sees is guessed (the arrangement-presence failure)."""
    from project_orchestrator import iron_god_local_drafter as drafter

    fits = "Return x. " + "b" * (drafter.ROADMAP_OBJECTIVE_CHARS - 10)
    cut = fits + " and the rule that matters comes last."

    assert not any("shown only the first" in p for p in check(
        graph_with({"objective": fits}))["problems"])
    assert any("shown only the first" in p and "state the rule first" in p for p in check(
        graph_with({"objective": cut}))["problems"])
