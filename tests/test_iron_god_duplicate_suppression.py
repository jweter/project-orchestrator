from __future__ import annotations

import pytest

from project_orchestrator.iron_god_duplicate_suppression import (
    KEPT,
    SUPPRESSED,
    DuplicateSuppressionError,
    suppress_duplicate_work,
)
from project_orchestrator.iron_god_slicespec_generator import SliceSpec


def _spec(**overrides) -> SliceSpec:
    base = dict(
        slice_id="SLICE-1",
        repository="repo-a",
        objective="build the thing",
        capability_id="cap-a",
        exact_baseline_sha="a" * 40,
        dependencies=(),
        acceptance_criteria=(),
        allowed_paths=(),
        forbidden_paths=(),
        runner_requirements=(),
        runtime_requirements=(),
        resource_requirements=(),
        product_reality_requirements=(),
        expected_artifacts=(),
        risk_class="LOW",
        verification_policy="FULL_PREFLIGHT",
        estimated_conflict_scope=(),
    )
    base.update(overrides)
    return SliceSpec(**base)


def test_single_spec_is_kept():
    (result,) = suppress_duplicate_work((_spec(),))
    assert result.status == KEPT
    assert result.duplicate_of_slice_id is None


def test_second_spec_same_capability_and_repo_is_suppressed():
    first = _spec(slice_id="SLICE-1")
    second = _spec(slice_id="SLICE-2")

    results = suppress_duplicate_work((first, second))
    by_id = {r.slice_id: r for r in results}

    assert by_id["SLICE-1"].status == KEPT
    assert by_id["SLICE-2"].status == SUPPRESSED
    assert by_id["SLICE-2"].duplicate_of_slice_id == "SLICE-1"


def test_same_capability_in_different_repositories_both_kept():
    first = _spec(slice_id="SLICE-1", repository="repo-a")
    second = _spec(slice_id="SLICE-2", repository="repo-b")

    results = suppress_duplicate_work((first, second))
    assert all(r.status == KEPT for r in results)


def test_different_capability_same_repository_both_kept():
    first = _spec(slice_id="SLICE-1", capability_id="cap-a")
    second = _spec(slice_id="SLICE-2", capability_id="cap-b")

    results = suppress_duplicate_work((first, second))
    assert all(r.status == KEPT for r in results)


def test_duplicate_slice_id_raises():
    first = _spec(slice_id="SLICE-1", capability_id="cap-a")
    second = _spec(slice_id="SLICE-1", capability_id="cap-b")

    with pytest.raises(DuplicateSuppressionError):
        suppress_duplicate_work((first, second))


def test_empty_input_yields_empty_result():
    assert suppress_duplicate_work(()) == ()


def test_three_specs_same_pair_only_first_kept():
    specs = (
        _spec(slice_id="SLICE-1"),
        _spec(slice_id="SLICE-2"),
        _spec(slice_id="SLICE-3"),
    )
    results = suppress_duplicate_work(specs)
    statuses = [r.status for r in results]
    assert statuses == [KEPT, SUPPRESSED, SUPPRESSED]
    assert results[1].duplicate_of_slice_id == "SLICE-1"
    assert results[2].duplicate_of_slice_id == "SLICE-1"
