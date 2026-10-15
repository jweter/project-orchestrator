"""Capability graph parser (roadmap section 3.1 / Phase 6: Capability
Compiler, first deliverable).

scripts/project_capability_graph.py already contains the whole-product
capability-graph validator (`validate()`), already wired into
capability_authority.py. This module does not duplicate that
validation logic -- it wraps `validate()` and projects its validated
payload into a typed, read-only graph (`CapabilityNode` /
`ParsedCapabilityGraph`) that later Capability Compiler stages (IG-061
roadmap parser onward) can consume directly instead of re-parsing raw
JSON. It refuses (raises) rather than guessing when `validate()`
reports DIRECTION_REQUIRED -- an invalid graph is never partially
parsed. This module performs no file or state mutation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from scripts.project_capability_graph import validate

DIRECTION_REQUIRED = "DIRECTION_REQUIRED"
VERIFIED_COMPLETE = "VERIFIED_COMPLETE"


class CapabilityGraphInvalidError(RuntimeError):
    """Raised when validate() reports DIRECTION_REQUIRED for the payload."""


@dataclass(frozen=True)
class CapabilityNode:
    capability_id: str
    description: str
    state: str
    depends_on: tuple[str, ...]
    completion_evidence: tuple[str, ...]
    verified: bool


@dataclass(frozen=True)
class ParsedCapabilityGraph:
    status: str
    nodes: tuple[CapabilityNode, ...]
    eligible: tuple[str, ...]
    remaining: tuple[str, ...]
    selected: str | None


def parse_capability_graph(payload: dict[str, Any]) -> ParsedCapabilityGraph:
    """Validate `payload` via the existing project capability-graph
    validator and project it into a typed, read-only graph.

    Raises CapabilityGraphInvalidError if validation reports
    DIRECTION_REQUIRED. Relies on validate() as the single source of
    truth for id/dependency/cycle/evidence well-formedness; this
    function trusts that guarantee rather than re-checking it.
    """
    report = validate(payload)
    if report["status"] == DIRECTION_REQUIRED:
        raise CapabilityGraphInvalidError(
            f"capability graph failed validation: {report['issues']!r}"
        )

    raw_capabilities = payload.get("capabilities") or []
    nodes = tuple(
        CapabilityNode(
            capability_id=cap.get("id"),
            description=cap.get("capability", ""),
            state=cap.get("state", ""),
            depends_on=tuple(cap.get("depends_on", [])),
            completion_evidence=tuple(cap.get("completion_evidence", [])),
            verified=cap.get("state") == VERIFIED_COMPLETE
            and bool(cap.get("verification")),
        )
        for cap in raw_capabilities
        if isinstance(cap, dict)
    )

    return ParsedCapabilityGraph(
        status=report["status"],
        nodes=nodes,
        eligible=tuple(report["eligible"]),
        remaining=tuple(report["remaining"]),
        selected=report.get("selected"),
    )
