"""Automatic slice decomposition (roadmap section 3.1 / section 23:
"Knowledge Engine Capability Compiler").

Given one incomplete CapabilityNode (iron_god_capability_graph_parser.py,
IG-060), decides whether its next increment can be proposed
automatically (DECOMPOSED), is already implemented and only needs
verification (AWAITING_VERIFICATION -- no new increment to propose),
or requires a human product choice (DIRECTION_REQUIRED). Per the
roadmap: "Only the last case should become DIRECTION_REQUIRED." This
module maps state=BLOCKED to DIRECTION_REQUIRED using the existing
STATES vocabulary rather than inventing a new field.

"What code area should change" is deliberately left to IG-066
(SliceSpec generator) -- the capability graph schema carries no path
information, and this module never invents one. This module performs
no file or state mutation.
"""

from __future__ import annotations

from dataclasses import dataclass

from .iron_god_capability_graph_parser import VERIFIED_COMPLETE, CapabilityNode

BLOCKED = "BLOCKED"
IMPLEMENTED_UNVERIFIED = "IMPLEMENTED_UNVERIFIED"

DECOMPOSED = "DECOMPOSED"
DIRECTION_REQUIRED = "DIRECTION_REQUIRED"
AWAITING_VERIFICATION = "AWAITING_VERIFICATION"


class SliceDecompositionError(RuntimeError):
    """Raised when asked to decompose an already-complete capability."""


@dataclass(frozen=True)
class SliceDecomposition:
    capability_id: str
    status: str
    next_increment: str | None
    depends_on: tuple[str, ...]
    completion_evidence: tuple[str, ...]
    reason: str


def decompose_capability(node: CapabilityNode) -> SliceDecomposition:
    """Decide the next increment for `node`, an incomplete capability.

    Raises SliceDecompositionError if `node` is already
    VERIFIED_COMPLETE -- decomposing a complete capability is a
    caller error, not a legitimate decomposition outcome.
    """
    if node.state == VERIFIED_COMPLETE:
        raise SliceDecompositionError(
            f"capability {node.capability_id!r} is already VERIFIED_COMPLETE; "
            "nothing to decompose"
        )

    if node.state == BLOCKED:
        return SliceDecomposition(
            capability_id=node.capability_id,
            status=DIRECTION_REQUIRED,
            next_increment=None,
            depends_on=node.depends_on,
            completion_evidence=node.completion_evidence,
            reason="capability state is BLOCKED; requires a human product choice",
        )

    if node.state == IMPLEMENTED_UNVERIFIED:
        return SliceDecomposition(
            capability_id=node.capability_id,
            status=AWAITING_VERIFICATION,
            next_increment=None,
            depends_on=node.depends_on,
            completion_evidence=node.completion_evidence,
            reason=(
                "capability is already implemented and only awaits "
                "verification; no new increment to propose"
            ),
        )

    return SliceDecomposition(
        capability_id=node.capability_id,
        status=DECOMPOSED,
        next_increment=node.description,
        depends_on=node.depends_on,
        completion_evidence=node.completion_evidence,
        reason=(
            "smallest meaningful next increment is the capability's own "
            "declared description"
        ),
    )
