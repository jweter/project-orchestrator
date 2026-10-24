"""Capacity model (roadmap section 21: Capacity-Aware Worker
Allocation; Phase 7: Event-Driven Scheduler, first deliverable).

Computes available development worker capacity from total worker
slots and current worker occupancy. Per the roadmap: "An occupied
GitHub publication lane must not consume a development slot" -- a
worker occupying the publication lane is counted and reported
separately, never subtracted from development capacity. This module
performs no scheduling or dispatch itself; it only answers how much
development capacity is free right now. This module performs no file
or state mutation.
"""

from __future__ import annotations

from dataclasses import dataclass

DEVELOPMENT = "development"
PUBLICATION = "publication"
KNOWN_LANES = frozenset({DEVELOPMENT, PUBLICATION})


class CapacityModelError(RuntimeError):
    """Raised on invalid capacity inputs."""


@dataclass(frozen=True)
class WorkerOccupancy:
    worker_id: str
    lane: str


@dataclass(frozen=True)
class CapacityDecision:
    total_worker_slots: int
    occupied_development_slots: int
    available_development_slots: int
    excluded_publication_lane_workers: tuple[str, ...]


def compute_available_capacity(
    total_worker_slots: int,
    occupancy: tuple[WorkerOccupancy, ...],
) -> CapacityDecision:
    """Decide how much development capacity is free right now.

    Raises CapacityModelError if `total_worker_slots` is negative, if
    any occupancy entry names an unrecognized lane, if two entries
    share a worker_id, or if the occupied development slot count
    exceeds `total_worker_slots`.
    """
    if total_worker_slots < 0:
        raise CapacityModelError("total_worker_slots must be >= 0")

    seen_worker_ids: set[str] = set()
    for worker in occupancy:
        if worker.lane not in KNOWN_LANES:
            raise CapacityModelError(
                f"unknown lane {worker.lane!r} for worker {worker.worker_id!r}"
            )
        if worker.worker_id in seen_worker_ids:
            raise CapacityModelError(
                f"duplicate worker_id {worker.worker_id!r} in occupancy"
            )
        seen_worker_ids.add(worker.worker_id)

    development_workers = tuple(
        worker.worker_id for worker in occupancy if worker.lane == DEVELOPMENT
    )
    publication_workers = tuple(
        worker.worker_id for worker in occupancy if worker.lane == PUBLICATION
    )

    occupied_development_slots = len(development_workers)
    if occupied_development_slots > total_worker_slots:
        raise CapacityModelError(
            f"occupied development slots ({occupied_development_slots}) exceed "
            f"total_worker_slots ({total_worker_slots})"
        )

    return CapacityDecision(
        total_worker_slots=total_worker_slots,
        occupied_development_slots=occupied_development_slots,
        available_development_slots=total_worker_slots - occupied_development_slots,
        excluded_publication_lane_workers=publication_workers,
    )
