from __future__ import annotations

import pytest

from project_orchestrator.iron_god_capability_graph_parser import (
    CapabilityGraphInvalidError,
    parse_capability_graph,
)


def test_single_verified_capability_parses_to_one_node():
    payload = {
        "capabilities": [
            {
                "id": "cap-a",
                "capability": "does a thing",
                "state": "VERIFIED_COMPLETE",
                "depends_on": [],
                "completion_evidence": ["pytest green"],
                "verification": "receipt-123",
            }
        ]
    }

    result = parse_capability_graph(payload)

    assert result.status == "COMPLETE"
    assert len(result.nodes) == 1
    node = result.nodes[0]
    assert node.capability_id == "cap-a"
    assert node.description == "does a thing"
    assert node.state == "VERIFIED_COMPLETE"
    assert node.depends_on == ()
    assert node.completion_evidence == ("pytest green",)
    assert node.verified is True


def test_unverified_capability_is_not_marked_verified():
    payload = {
        "capabilities": [
            {
                "id": "cap-a",
                "capability": "does a thing",
                "state": "IN_PROGRESS",
                "depends_on": [],
                "completion_evidence": ["wip"],
            }
        ],
        "roadmap": ["cap-a"],
    }

    result = parse_capability_graph(payload)

    assert result.nodes[0].verified is False


def test_verified_complete_without_verification_field_is_not_verified():
    payload = {
        "capabilities": [
            {
                "id": "cap-a",
                "capability": "does a thing",
                "state": "VERIFIED_COMPLETE",
                "depends_on": [],
                "completion_evidence": ["wip"],
            }
        ]
    }

    with pytest.raises(CapabilityGraphInvalidError):
        parse_capability_graph(payload)


def test_missing_capabilities_raises():
    with pytest.raises(CapabilityGraphInvalidError):
        parse_capability_graph({})


def test_unknown_dependency_raises():
    payload = {
        "capabilities": [
            {
                "id": "cap-a",
                "capability": "does a thing",
                "state": "PLANNED",
                "depends_on": ["cap-missing"],
                "completion_evidence": ["none yet"],
            }
        ]
    }

    with pytest.raises(CapabilityGraphInvalidError):
        parse_capability_graph(payload)


def test_dependency_cycle_raises():
    payload = {
        "capabilities": [
            {
                "id": "cap-a",
                "capability": "a",
                "state": "PLANNED",
                "depends_on": ["cap-b"],
                "completion_evidence": ["none"],
            },
            {
                "id": "cap-b",
                "capability": "b",
                "state": "PLANNED",
                "depends_on": ["cap-a"],
                "completion_evidence": ["none"],
            },
        ]
    }

    with pytest.raises(CapabilityGraphInvalidError):
        parse_capability_graph(payload)


def test_eligible_remaining_selected_carried_through_with_roadmap():
    payload = {
        "capabilities": [
            {
                "id": "cap-a",
                "capability": "a",
                "state": "PLANNED",
                "depends_on": [],
                "completion_evidence": ["none"],
            },
            {
                "id": "cap-b",
                "capability": "b",
                "state": "PLANNED",
                "depends_on": ["cap-a"],
                "completion_evidence": ["none"],
            },
        ],
        "roadmap": ["cap-a", "cap-b"],
    }

    result = parse_capability_graph(payload)

    assert result.status == "READY"
    assert result.eligible == ("cap-a",)
    assert result.remaining == ("cap-a", "cap-b")
    assert result.selected == "cap-a"
    assert len(result.nodes) == 2
