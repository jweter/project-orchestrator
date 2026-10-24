from __future__ import annotations

from project_orchestrator.iron_god_ambiguity_detector import AmbiguityCheck
from project_orchestrator.iron_god_conflict_aware_scheduling import SchedulingDecision
from project_orchestrator.iron_god_rocksmith_explanation_trace import (
    RECOMMENDED,
    REJECTED_DEPENDENCY_BLOCKED,
    REJECTED_OWNED,
    REJECTED_RESOURCE_INCOMPATIBLE,
    CandidateInput,
    explain_candidate,
    explain_candidates,
)


def _ambiguity(**overrides) -> AmbiguityCheck:
    base = dict(
        capability_id="cap-a",
        status="CLEAR",
        reason="test fixture",
        unresolved_dependencies=(),
    )
    base.update(overrides)
    return AmbiguityCheck(**base)


def _scheduling(**overrides) -> SchedulingDecision:
    base = dict(slice_id="SLICE-1", status="SELECTED", reason="test fixture")
    base.update(overrides)
    return SchedulingDecision(**base)


def test_owned_candidate_rejected_regardless_of_other_fields():
    result = explain_candidate(
        "SLICE-1",
        is_owned=True,
        ambiguity_check=_ambiguity(status="AMBIGUOUS"),
        resource_compatible=False,
        scheduling_decision=_scheduling(status="REJECTED_CONFLICT"),
    )
    assert result.status == REJECTED_OWNED


def test_ambiguous_dependency_rejected_before_resource_and_conflict_checks():
    result = explain_candidate(
        "SLICE-1",
        is_owned=False,
        ambiguity_check=_ambiguity(status="AMBIGUOUS", reason="dep unresolved"),
        resource_compatible=False,
        scheduling_decision=_scheduling(status="REJECTED_CONFLICT"),
    )
    assert result.status == REJECTED_DEPENDENCY_BLOCKED
    assert "dep unresolved" in result.reason


def test_resource_incompatible_rejected_before_conflict_check():
    result = explain_candidate(
        "SLICE-1",
        is_owned=False,
        ambiguity_check=_ambiguity(status="CLEAR"),
        resource_compatible=False,
        scheduling_decision=_scheduling(status="REJECTED_CONFLICT"),
    )
    assert result.status == REJECTED_RESOURCE_INCOMPATIBLE


def test_non_selected_scheduling_decision_passed_through_unchanged():
    result = explain_candidate(
        "SLICE-1",
        is_owned=False,
        ambiguity_check=_ambiguity(status="CLEAR"),
        resource_compatible=True,
        scheduling_decision=_scheduling(
            status="REJECTED_CAPACITY", reason="no capacity remained"
        ),
    )
    assert result.status == "REJECTED_CAPACITY"
    assert result.reason == "no capacity remained"


def test_all_checks_pass_with_selected_scheduling_is_recommended():
    result = explain_candidate(
        "SLICE-1",
        is_owned=False,
        ambiguity_check=_ambiguity(status="CLEAR"),
        resource_compatible=True,
        scheduling_decision=_scheduling(status="SELECTED"),
    )
    assert result.status == RECOMMENDED


def test_none_ambiguity_check_and_none_scheduling_decision_skip_those_checks():
    result = explain_candidate(
        "SLICE-1",
        is_owned=False,
        ambiguity_check=None,
        resource_compatible=True,
        scheduling_decision=None,
    )
    assert result.status == RECOMMENDED


def test_batch_explains_every_candidate_in_order():
    candidates = (
        CandidateInput(
            slice_id="s1",
            is_owned=True,
            ambiguity_check=None,
            resource_compatible=True,
            scheduling_decision=None,
        ),
        CandidateInput(
            slice_id="s2",
            is_owned=False,
            ambiguity_check=_ambiguity(status="CLEAR"),
            resource_compatible=True,
            scheduling_decision=_scheduling(slice_id="s2", status="SELECTED"),
        ),
    )
    results = explain_candidates(candidates)
    assert [r.slice_id for r in results] == ["s1", "s2"]
    assert results[0].status == REJECTED_OWNED
    assert results[1].status == RECOMMENDED


def test_empty_batch_yields_empty_result():
    assert explain_candidates(()) == ()
