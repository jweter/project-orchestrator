"""Repository fairness policy (roadmap section 21: Capacity-Aware
Worker Allocation; Phase 7: Event-Driven Scheduler).

Allocates available development capacity
(iron_god_capacity_model.py, IG-070) fairly across repositories with
eligible work via deterministic round-robin (max-min) allocation, so
one repository with many eligible slices cannot starve another that
has fewer but still has at least one -- directly answering the
roadmap's own scenario: "one repo blocked while three others remain
actionable" must not mean the three actionable repos are denied
capacity. An optional per-repository max-concurrency cap is honored
and never exceeded. This module performs no scheduling or dispatch
itself -- it only decides how many slots each repository may claim
this round.
"""

from __future__ import annotations

from dataclasses import dataclass


class FairnessPolicyError(RuntimeError):
    """Raised on invalid fairness-allocation inputs."""


@dataclass(frozen=True)
class RepositoryDemand:
    repository: str
    eligible_slice_count: int
    max_concurrency: int | None = None


@dataclass(frozen=True)
class RepositoryAllocation:
    repository: str
    allocated_slots: int


def allocate_capacity_fairly(
    available_slots: int,
    demands: tuple[RepositoryDemand, ...],
) -> tuple[RepositoryAllocation, ...]:
    """Allocate `available_slots` across `demands` via round-robin.

    Each repository with remaining eligible demand (and headroom
    under its own max_concurrency, if any) receives one slot per
    round until `available_slots` is exhausted or no repository has
    further eligible demand. Raises FairnessPolicyError on a negative
    slot count, a negative eligible_slice_count/max_concurrency, or a
    duplicate repository name.
    """
    if available_slots < 0:
        raise FairnessPolicyError("available_slots must be >= 0")

    seen_repositories: set[str] = set()
    for demand in demands:
        if demand.repository in seen_repositories:
            raise FairnessPolicyError(
                f"duplicate repository {demand.repository!r} in demands"
            )
        seen_repositories.add(demand.repository)
        if demand.eligible_slice_count < 0:
            raise FairnessPolicyError(
                f"eligible_slice_count must be >= 0 for {demand.repository!r}"
            )
        if demand.max_concurrency is not None and demand.max_concurrency < 0:
            raise FairnessPolicyError(
                f"max_concurrency must be >= 0 for {demand.repository!r}"
            )

    order = [demand.repository for demand in demands]
    allocated = {repository: 0 for repository in order}
    remaining_demand = {
        demand.repository: demand.eligible_slice_count for demand in demands
    }
    caps = {demand.repository: demand.max_concurrency for demand in demands}
    remaining_slots = available_slots

    progressed = True
    while remaining_slots > 0 and progressed:
        progressed = False
        for repository in order:
            if remaining_slots <= 0:
                break
            if remaining_demand[repository] <= 0:
                continue
            cap = caps[repository]
            if cap is not None and allocated[repository] >= cap:
                continue
            allocated[repository] += 1
            remaining_demand[repository] -= 1
            remaining_slots -= 1
            progressed = True

    return tuple(
        RepositoryAllocation(
            repository=demand.repository,
            allocated_slots=allocated[demand.repository],
        )
        for demand in demands
    )
