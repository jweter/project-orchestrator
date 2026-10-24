"""Duplicate-work suppression (roadmap section 3.1 / Phase 6:
Capability Compiler, final deliverable).

Given a batch of SliceSpecs (iron_god_slicespec_generator.py, IG-066)
that may have been independently generated from different compiler
inputs for the same underlying capability, suppresses the duplicates:
two SliceSpecs are the same work if they target the same
(repository, capability_id) pair, since a capability is scoped per
repository (capability_authority.py already stores one compiled graph
per repository). The first spec seen per pair is KEPT; every later
spec for the same pair is SUPPRESSED and records which slice_id it
duplicates -- never silently dropped without a trace.

A distinct slice_id collision (two different specs claiming the same
slice_id) is a more severe ambiguity than an ordinary duplicate and
raises rather than being processed. This module performs no file or
state mutation.
"""

from __future__ import annotations

from dataclasses import dataclass

from .iron_god_slicespec_generator import SliceSpec

KEPT = "KEPT"
SUPPRESSED = "SUPPRESSED"


class DuplicateSuppressionError(RuntimeError):
    """Raised when two distinct SliceSpecs share the same slice_id."""


@dataclass(frozen=True)
class DuplicateSuppressionResult:
    slice_id: str
    status: str
    duplicate_of_slice_id: str | None
    reason: str


def suppress_duplicate_work(
    specs: tuple[SliceSpec, ...],
) -> tuple[DuplicateSuppressionResult, ...]:
    """Decide KEPT vs SUPPRESSED for each spec in `specs`, in order.

    The first spec seen for a given (repository, capability_id) pair
    is KEPT; every subsequent spec for the same pair is SUPPRESSED and
    records which slice_id it duplicates. Raises
    DuplicateSuppressionError if two specs share the same slice_id.
    """
    first_seen: dict[tuple[str, str], str] = {}
    seen_slice_ids: set[str] = set()
    results: list[DuplicateSuppressionResult] = []

    for spec in specs:
        if spec.slice_id in seen_slice_ids:
            raise DuplicateSuppressionError(
                f"slice_id {spec.slice_id!r} appears on more than one SliceSpec"
            )
        seen_slice_ids.add(spec.slice_id)

        key = (spec.repository, spec.capability_id)
        if key not in first_seen:
            first_seen[key] = spec.slice_id
            results.append(
                DuplicateSuppressionResult(
                    slice_id=spec.slice_id,
                    status=KEPT,
                    duplicate_of_slice_id=None,
                    reason=(
                        f"first SliceSpec seen for capability "
                        f"{spec.capability_id!r} in repository "
                        f"{spec.repository!r}"
                    ),
                )
            )
        else:
            results.append(
                DuplicateSuppressionResult(
                    slice_id=spec.slice_id,
                    status=SUPPRESSED,
                    duplicate_of_slice_id=first_seen[key],
                    reason=(
                        f"duplicates {first_seen[key]!r}: same capability "
                        f"{spec.capability_id!r} in repository "
                        f"{spec.repository!r}"
                    ),
                )
            )
    return tuple(results)
