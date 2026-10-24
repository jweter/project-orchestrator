from __future__ import annotations

import pytest

from project_orchestrator.iron_god_artifact_retention import (
    ArtifactEntry,
    ArtifactRetentionDecision,
)
from project_orchestrator.iron_god_protected_work_audit import (
    CONFLICT,
    SAFE,
    ArtifactIdMismatchError,
    audit_protected_work,
)


def _pair(
    artifact_id: str,
    checkpoint_class: int,
    eligible_for_reclamation: bool,
    *,
    decision_artifact_id: str | None = None,
):
    entry = ArtifactEntry(
        artifact_id=artifact_id,
        checkpoint_class=checkpoint_class,
        in_active_use=False,
    )
    decision = ArtifactRetentionDecision(
        artifact_id=decision_artifact_id or artifact_id,
        eligible_for_reclamation=eligible_for_reclamation,
        reason="test fixture",
    )
    return entry, decision


def test_not_eligible_is_always_safe_regardless_of_class():
    for checkpoint_class in (1, 2, 3, 4, 5):
        pair = _pair("art-1", checkpoint_class, eligible_for_reclamation=False)
        (result,) = audit_protected_work((pair,))
        assert result.status == SAFE
        assert result.safe_to_proceed is True


def test_eligible_class_1_is_confirmed_safe():
    pair = _pair("art-1", 1, eligible_for_reclamation=True)
    (result,) = audit_protected_work((pair,))
    assert result.status == SAFE
    assert result.safe_to_proceed is True


@pytest.mark.parametrize("checkpoint_class", [2, 3, 4, 5])
def test_eligible_protected_class_is_conflict(checkpoint_class):
    pair = _pair("art-1", checkpoint_class, eligible_for_reclamation=True)
    (result,) = audit_protected_work((pair,))
    assert result.status == CONFLICT
    assert result.safe_to_proceed is False


def test_artifact_id_mismatch_raises_rather_than_silently_auditing():
    pair = _pair(
        "art-1", 1, eligible_for_reclamation=True, decision_artifact_id="art-2"
    )
    with pytest.raises(ArtifactIdMismatchError):
        audit_protected_work((pair,))


def test_multiple_pairs_audited_independently():
    safe_pair = _pair("art-safe", 1, eligible_for_reclamation=True)
    conflict_pair = _pair("art-conflict", 3, eligible_for_reclamation=True)
    untouched_pair = _pair("art-untouched", 5, eligible_for_reclamation=False)

    results = audit_protected_work((safe_pair, conflict_pair, untouched_pair))
    by_id = {result.artifact_id: result for result in results}

    assert by_id["art-safe"].status == SAFE
    assert by_id["art-conflict"].status == CONFLICT
    assert by_id["art-untouched"].status == SAFE


def test_empty_input_yields_empty_result():
    assert audit_protected_work(()) == ()
