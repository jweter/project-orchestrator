from __future__ import annotations

import pytest

from project_orchestrator.iron_god_checkpoint_controller import (
    UnknownCheckpointEventError,
    classify_checkpoint_event,
)


@pytest.mark.parametrize(
    "event_kind,expected_class",
    [
        ("worker_commit", 1),
        ("candidate_commit", 2),
        ("accepted_milestone", 3),
        ("recovery_checkpoint", 4),
        ("release", 5),
    ],
)
def test_classifies_each_known_event_kind(event_kind, expected_class):
    result = classify_checkpoint_event(event_kind)
    assert result.checkpoint_class == expected_class


def test_unknown_event_kind_raises():
    with pytest.raises(UnknownCheckpointEventError):
        classify_checkpoint_event("mystery_event")


def test_worker_commit_is_reclaimable_and_not_protected():
    result = classify_checkpoint_event("worker_commit")
    assert result.eventually_reclaimable is True
    assert result.must_survive_worker_cleanup is False
    assert result.protected_indefinitely is False


def test_candidate_commit_must_survive_but_is_not_indefinite():
    result = classify_checkpoint_event("candidate_commit")
    assert result.must_survive_worker_cleanup is True
    assert result.protected_indefinitely is False
    assert result.eventually_reclaimable is False


@pytest.mark.parametrize(
    "event_kind", ["accepted_milestone", "recovery_checkpoint", "release"]
)
def test_classes_three_and_above_are_protected_indefinitely(event_kind):
    result = classify_checkpoint_event(event_kind)
    assert result.must_survive_worker_cleanup is True
    assert result.protected_indefinitely is True
    assert result.eventually_reclaimable is False
