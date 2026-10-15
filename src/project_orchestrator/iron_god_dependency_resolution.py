"""Dependency resolution (roadmap section 3.1 / Phase 6: Capability
Compiler).

The existing single-graph dependency/cycle check lives in
scripts/project_capability_graph.py (used by IG-060) and is not
duplicated here. This module instead resolves a capability's declared
dependencies against actual Git ground truth
(iron_god_state_reconstruction.py, IG-056) rather than trusting the
capability graph's self-reported completion state -- mirroring
iron_god_protected_work_audit.py's (IG-058) role as a second,
independent re-verification layer. A dependency is RESOLVED only when
Git ground truth reconstructs it as ACCEPTED; anything else
(NO_CANDIDATE, CANDIDATE_UNACCEPTED, AMBIGUOUS) is UNRESOLVED,
regardless of what the capability graph itself claims. This module
performs no ref or Git mutation -- it only composes IG-056's read-only
reconstruction.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .iron_god_state_reconstruction import ACCEPTED, reconstruct_slice_state

RESOLVED = "RESOLVED"
UNRESOLVED = "UNRESOLVED"


class DependencyResolutionError(RuntimeError):
    """Raised when a dependency has no known milestone mapping."""


@dataclass(frozen=True)
class DependencyResolution:
    capability_id: str
    dependency_id: str
    status: str
    reason: str


def resolve_dependencies(
    repo_root: Path,
    capability_id: str,
    depends_on: tuple[str, ...],
    *,
    milestone_id_by_capability: dict[str, str],
) -> tuple[DependencyResolution, ...]:
    """Resolve each of `depends_on` against Git ground truth.

    Raises DependencyResolutionError if a dependency id has no entry
    in `milestone_id_by_capability` -- an unmapped dependency is never
    silently treated as resolved or unresolved.
    """
    results: list[DependencyResolution] = []
    for dependency_id in depends_on:
        if dependency_id not in milestone_id_by_capability:
            raise DependencyResolutionError(
                f"no milestone mapping known for dependency {dependency_id!r} "
                f"of capability {capability_id!r}"
            )
        milestone_id = milestone_id_by_capability[dependency_id]
        state = reconstruct_slice_state(repo_root, dependency_id, milestone_id)

        if state.status == ACCEPTED:
            results.append(
                DependencyResolution(
                    capability_id=capability_id,
                    dependency_id=dependency_id,
                    status=RESOLVED,
                    reason=f"Git ground truth reconstructs {dependency_id!r} as ACCEPTED",
                )
            )
        else:
            results.append(
                DependencyResolution(
                    capability_id=capability_id,
                    dependency_id=dependency_id,
                    status=UNRESOLVED,
                    reason=(
                        f"Git ground truth reconstructs {dependency_id!r} as "
                        f"{state.status}, not ACCEPTED"
                    ),
                )
            )
    return tuple(results)
