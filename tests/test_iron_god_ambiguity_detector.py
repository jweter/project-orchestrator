from __future__ import annotations

import pytest

from project_orchestrator.iron_god_ambiguity_detector import (
    AMBIGUOUS,
    CLEAR,
    NOT_APPLICABLE,
    AmbiguityDetectorError,
    detect_ambiguity,
)
from project_orchestrator.iron_god_dependency_resolution import DependencyResolution
from project_orchestrator.iron_god_slice_decomposition import SliceDecomposition


def _decomposition(**overrides) -> SliceDecomposition:
    base = dict(
        capability_id="cap-a",
        status="DECOMPOSED",
        next_increment="build the thing",
        depends_on=("cap-dep",),
        completion_evidence=(),
        reason="test fixture",
    )
    base.update(overrides)
    return SliceDecomposition(**base)


def _resolution(**overrides) -> DependencyResolution:
    base = dict(
        capability_id="cap-a",
        dependency_id="cap-dep",
        status="RESOLVED",
        reason="test fixture",
    )
    base.update(overrides)
    return DependencyResolution(**base)


def test_decomposed_with_all_resolved_is_clear():
    result = detect_ambiguity(_decomposition(), (_resolution(status="RESOLVED"),))
    assert result.status == CLEAR
    assert result.unresolved_dependencies == ()


def test_decomposed_with_unresolved_dependency_is_ambiguous():
    result = detect_ambiguity(_decomposition(), (_resolution(status="UNRESOLVED"),))
    assert result.status == AMBIGUOUS
    assert result.unresolved_dependencies == ("cap-dep",)


def test_decomposed_with_no_dependencies_is_clear():
    result = detect_ambiguity(_decomposition(depends_on=()), ())
    assert result.status == CLEAR


@pytest.mark.parametrize("status", ["DIRECTION_REQUIRED", "AWAITING_VERIFICATION"])
def test_non_decomposed_is_not_applicable_regardless_of_resolutions(status):
    result = detect_ambiguity(
        _decomposition(status=status, next_increment=None),
        (_resolution(status="UNRESOLVED"),),
    )
    assert result.status == NOT_APPLICABLE


def test_mismatched_capability_id_raises():
    with pytest.raises(AmbiguityDetectorError):
        detect_ambiguity(_decomposition(), (_resolution(capability_id="cap-other"),))


def test_multiple_dependencies_any_unresolved_makes_it_ambiguous():
    resolutions = (
        _resolution(dependency_id="cap-dep-1", status="RESOLVED"),
        _resolution(dependency_id="cap-dep-2", status="UNRESOLVED"),
    )
    result = detect_ambiguity(_decomposition(), resolutions)
    assert result.status == AMBIGUOUS
    assert result.unresolved_dependencies == ("cap-dep-2",)
