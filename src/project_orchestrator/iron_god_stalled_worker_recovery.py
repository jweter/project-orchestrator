"""Stalled-worker recovery (roadmap section 4.1: Worker Isolation /
section 21: "Worker dies: -> recovery controller evaluates
immediately"; Phase 7: Event-Driven Scheduler).

Composes the existing worker liveness probe
(recovery_manager.worker_liveness, injectable via its own
LivenessProbe type) with a recovery decision policy: only a
confirmed-DEAD worker triggers RECLAIM_AND_REQUEUE on its held
slice. An UNKNOWN liveness result (cross-host, unreadable, or any
other inconclusive case) never triggers a reclaim -- the slice is
held, reusing this roadmap's own "never silently resolve ambiguity"
principle for worker liveness. A confirmed-ALIVE worker is also
NO_ACTION. This module performs no lease, ref, or state mutation
itself -- it only decides what should happen next, given an
already-observed liveness probe result.
"""

from __future__ import annotations

from dataclasses import dataclass

from .recovery_manager import LivenessProbe, worker_liveness

DEAD = "DEAD"
ALIVE = "ALIVE"
UNKNOWN = "UNKNOWN"

RECLAIM_AND_REQUEUE = "RECLAIM_AND_REQUEUE"
NO_ACTION = "NO_ACTION"


@dataclass(frozen=True)
class StalledWorkerDecision:
    worker_id: str
    liveness: str
    status: str
    reason: str


def decide_stalled_worker_recovery(
    worker_id: str,
    worker: dict | None,
    *,
    liveness_probe: LivenessProbe = worker_liveness,
) -> StalledWorkerDecision:
    """Decide whether `worker_id`'s held slice should be reclaimed.

    RECLAIM_AND_REQUEUE only on a confirmed DEAD liveness result.
    ALIVE and UNKNOWN both decide NO_ACTION -- UNKNOWN is never
    treated as DEAD, since reclaiming a slice from a worker that
    might still be alive would be a destructive, premature action.
    """
    liveness = liveness_probe(worker)

    if liveness == DEAD:
        return StalledWorkerDecision(
            worker_id=worker_id,
            liveness=liveness,
            status=RECLAIM_AND_REQUEUE,
            reason="worker liveness probe confirms DEAD; slice lease is reclaimed and requeued",
        )

    if liveness == ALIVE:
        return StalledWorkerDecision(
            worker_id=worker_id,
            liveness=liveness,
            status=NO_ACTION,
            reason="worker liveness probe confirms ALIVE; no reclaim",
        )

    return StalledWorkerDecision(
        worker_id=worker_id,
        liveness=liveness,
        status=NO_ACTION,
        reason=(
            "worker liveness is UNKNOWN; never reclaim on an inconclusive "
            "result, per fail-closed ambiguity policy"
        ),
    )
