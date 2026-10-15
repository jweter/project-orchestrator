from __future__ import annotations

import pytest

from project_orchestrator.iron_god_ambiguity_detector import AmbiguityCheck
from project_orchestrator.iron_god_slice_decomposition import SliceDecomposition
from project_orchestrator.iron_god_slicespec_generator import (
    SliceSpecGenerationError,
    SliceSpecInputs,
    generate_slice_spec,
)


def _decomposition(**overrides) -> SliceDecomposition:
    base = dict(
        capability_id="cap-a",
        status="DECOMPOSED",
        next_increment="build the thing",
        depends_on=("cap-dep",),
        completion_evidence=("pytest green",),
        reason="test fixture",
    )
    base.update(overrides)
    return SliceDecomposition(**base)


def _ambiguity(**overrides) -> AmbiguityCheck:
    base = dict(
        capability_id="cap-a",
        status="CLEAR",
        reason="test fixture",
        unresolved_dependencies=(),
    )
    base.update(overrides)
    return AmbiguityCheck(**base)


def _inputs(**overrides) -> SliceSpecInputs:
    base = dict(
        repository="repo-a",
        exact_baseline_sha="a" * 40,
        allowed_paths=("src/thing.py",),
        forbidden_paths=("src/other.py",),
        runner_requirements=("windows",),
        runtime_requirements=("python3.12",),
        resource_requirements=("cpu",),
        product_reality_requirements=("pr-check",),
        expected_artifacts=("src/thing.py",),
        risk_class="LOW",
        verification_policy="FULL_PREFLIGHT",
        estimated_conflict_scope=("src/thing.py",),
    )
    base.update(overrides)
    return SliceSpecInputs(**base)


def test_decomposed_and_clear_generates_full_spec():
    spec = generate_slice_spec("SLICE-1", _decomposition(), _ambiguity(), _inputs())

    assert spec.slice_id == "SLICE-1"
    assert spec.capability_id == "cap-a"
    assert spec.objective == "build the thing"
    assert spec.dependencies == ("cap-dep",)
    assert spec.acceptance_criteria == ("pytest green",)
    assert spec.repository == "repo-a"
    assert spec.exact_baseline_sha == "a" * 40
    assert spec.allowed_paths == ("src/thing.py",)
    assert spec.forbidden_paths == ("src/other.py",)
    assert spec.risk_class == "LOW"
    assert spec.verification_policy == "FULL_PREFLIGHT"


def test_non_decomposed_raises():
    with pytest.raises(SliceSpecGenerationError):
        generate_slice_spec(
            "SLICE-1",
            _decomposition(status="DIRECTION_REQUIRED", next_increment=None),
            _ambiguity(),
            _inputs(),
        )


def test_non_clear_ambiguity_raises_even_if_decomposed():
    with pytest.raises(SliceSpecGenerationError):
        generate_slice_spec(
            "SLICE-1",
            _decomposition(),
            _ambiguity(status="AMBIGUOUS", unresolved_dependencies=("cap-dep",)),
            _inputs(),
        )


def test_capability_id_mismatch_raises():
    with pytest.raises(SliceSpecGenerationError):
        generate_slice_spec(
            "SLICE-1",
            _decomposition(),
            _ambiguity(capability_id="cap-other"),
            _inputs(),
        )


@pytest.mark.parametrize("slice_id", ["", "   "])
def test_empty_or_whitespace_slice_id_raises(slice_id):
    with pytest.raises(SliceSpecGenerationError):
        generate_slice_spec(slice_id, _decomposition(), _ambiguity(), _inputs())
