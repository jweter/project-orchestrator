from __future__ import annotations

from project_orchestrator.iron_god_runner_manifest_enforcement import (
    CONFORMS,
    VIOLATION,
    enforce_runner_manifest,
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
        runner_requirements=("claude",),
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


def test_exact_match_conforms():
    result = enforce_runner_manifest(_spec(), ("claude",))
    assert result.status == CONFORMS
    assert result.missing_tags == ()
    assert result.unexpected_tags == ()


def test_missing_tag_is_violation():
    result = enforce_runner_manifest(_spec(runner_requirements=("claude",)), ())
    assert result.status == VIOLATION
    assert result.missing_tags == ("claude",)
    assert result.unexpected_tags == ()


def test_unexpected_tag_is_violation():
    result = enforce_runner_manifest(_spec(runner_requirements=()), ("other-runner",))
    assert result.status == VIOLATION
    assert result.missing_tags == ()
    assert result.unexpected_tags == ("other-runner",)


def test_both_missing_and_unexpected_reported_simultaneously():
    result = enforce_runner_manifest(
        _spec(runner_requirements=("claude",)), ("other-runner",)
    )
    assert result.status == VIOLATION
    assert result.missing_tags == ("claude",)
    assert result.unexpected_tags == ("other-runner",)


def test_empty_declared_and_empty_executed_conforms():
    result = enforce_runner_manifest(_spec(runner_requirements=()), ())
    assert result.status == CONFORMS


def test_superset_executed_is_violation_not_conforms():
    result = enforce_runner_manifest(
        _spec(runner_requirements=("claude",)), ("claude", "ollama")
    )
    assert result.status == VIOLATION
    assert result.unexpected_tags == ("ollama",)


def test_subset_executed_is_violation_not_conforms():
    result = enforce_runner_manifest(
        _spec(runner_requirements=("claude", "gpu")), ("claude",)
    )
    assert result.status == VIOLATION
    assert result.missing_tags == ("gpu",)


def test_duplicate_reported_runner_tags_are_violation():
    result = enforce_runner_manifest(_spec(), ("claude", "claude"))
    assert result.status == VIOLATION


def test_duplicate_declared_runner_tags_are_violation():
    result = enforce_runner_manifest(
        _spec(runner_requirements=("claude", "claude")), ("claude",)
    )
    assert result.status == VIOLATION
