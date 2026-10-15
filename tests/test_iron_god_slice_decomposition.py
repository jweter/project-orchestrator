from __future__ import annotations

import pytest

from project_orchestrator.iron_god_capability_graph_parser import CapabilityNode
from project_orchestrator.iron_god_slice_decomposition import (
    AWAITING_VERIFICATION,
    DECOMPOSED,
    DIRECTION_REQUIRED,
    SliceDecompositionError,
    decompose_capability,
)


def _node(**overrides) -> CapabilityNode:
    base = dict(
        capability_id="cap-a",
        description="build the thing",
        state="PLANNED",
        depends_on=("cap-base",),
        completion_evidence=("pytest green",),
        verified=False,
    )
    base.update(overrides)
    return CapabilityNode(**base)


@pytest.mark.parametrize("state", ["PLANNED", "IN_PROGRESS"])
def test_planned_or_in_progress_decomposes_with_description_as_increment(state):
    node = _node(state=state)
    result = decompose_capability(node)

    assert result.status == DECOMPOSED
    assert result.next_increment == "build the thing"
    assert result.depends_on == ("cap-base",)
    assert result.completion_evidence == ("pytest green",)


def test_blocked_is_direction_required_with_no_increment():
    node = _node(state="BLOCKED")
    result = decompose_capability(node)

    assert result.status == DIRECTION_REQUIRED
    assert result.next_increment is None
    assert result.depends_on == ("cap-base",)


def test_implemented_unverified_awaits_verification_with_no_increment():
    node = _node(state="IMPLEMENTED_UNVERIFIED")
    result = decompose_capability(node)

    assert result.status == AWAITING_VERIFICATION
    assert result.next_increment is None


def test_verified_complete_raises():
    node = _node(state="VERIFIED_COMPLETE", verified=True)
    with pytest.raises(SliceDecompositionError):
        decompose_capability(node)


def test_capability_id_carried_through():
    node = _node(capability_id="cap-xyz", state="PLANNED")
    result = decompose_capability(node)
    assert result.capability_id == "cap-xyz"
