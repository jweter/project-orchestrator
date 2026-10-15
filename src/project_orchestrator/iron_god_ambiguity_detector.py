"""Ambiguity detector (roadmap section 3.1 / section 23: "Knowledge
Engine Capability Compiler").

Composes IG-063's decomposition decision
(iron_god_slice_decomposition.py) and IG-064's Git-ground-truth
dependency resolution (iron_god_dependency_resolution.py) for the
same capability to detect a contradiction neither module alone can
see: a decomposition says DECOMPOSED (ready to build, no product
direction needed) while Git ground truth says at least one of its
dependencies is still UNRESOLVED. That contradiction is reported
AMBIGUOUS, reusing the roadmap's own quarantine principle
("Ambiguous work is quarantined. It is never silently deleted") as
quarantine-not-dispatch: the capability is never silently treated as
ready. A non-DECOMPOSED decomposition (DIRECTION_REQUIRED or
AWAITING_VERIFICATION) passes through as NOT_APPLICABLE, since IG-063
already resolved its own status and this module never re-decides it.
This module performs no mutation -- it is a pure cross-check.
"""

from __future__ import annotations

from dataclasses import dataclass

from .iron_god_dependency_resolution import UNRESOLVED, DependencyResolution
from .iron_god_slice_decomposition import DECOMPOSED, SliceDecomposition

CLEAR = "CLEAR"
AMBIGUOUS = "AMBIGUOUS"
NOT_APPLICABLE = "NOT_APPLICABLE"


class AmbiguityDetectorError(RuntimeError):
    """Raised when a dependency resolution's capability_id does not
    match the decomposition it is being checked against."""


@dataclass(frozen=True)
class AmbiguityCheck:
    capability_id: str
    status: str
    reason: str
    unresolved_dependencies: tuple[str, ...]


def detect_ambiguity(
    decomposition: SliceDecomposition,
    dependency_resolutions: tuple[DependencyResolution, ...],
) -> AmbiguityCheck:
    """Cross-check `decomposition` against `dependency_resolutions`.

    Raises AmbiguityDetectorError if any resolution's capability_id
    does not match `decomposition.capability_id` -- cross-checking the
    wrong capability's dependencies is never silently tolerated.
    """
    for resolution in dependency_resolutions:
        if resolution.capability_id != decomposition.capability_id:
            raise AmbiguityDetectorError(
                f"dependency resolution for {resolution.capability_id!r} does "
                f"not match decomposition capability_id "
                f"{decomposition.capability_id!r}"
            )

    if decomposition.status != DECOMPOSED:
        return AmbiguityCheck(
            capability_id=decomposition.capability_id,
            status=NOT_APPLICABLE,
            reason=(
                f"decomposition status is {decomposition.status}, not "
                "DECOMPOSED; IG-063 already resolved its own status"
            ),
            unresolved_dependencies=(),
        )

    unresolved = tuple(
        resolution.dependency_id
        for resolution in dependency_resolutions
        if resolution.status == UNRESOLVED
    )
    if unresolved:
        return AmbiguityCheck(
            capability_id=decomposition.capability_id,
            status=AMBIGUOUS,
            reason=(
                "decomposition says DECOMPOSED but Git ground truth has "
                "unresolved dependencies; never silently dispatched"
            ),
            unresolved_dependencies=unresolved,
        )

    return AmbiguityCheck(
        capability_id=decomposition.capability_id,
        status=CLEAR,
        reason=(
            "decomposition is DECOMPOSED and Git ground truth confirms all "
            "dependencies resolved"
        ),
        unresolved_dependencies=(),
    )
