"""Extracted scheduling and delivery diagnostics for the resident supervisor.

This module may only reduce or observe capability. It never grants execution,
claims/revokes a worker lease, or changes owner control.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from .delivery_throughput import assess_delivery_throughput
from .quarantine_triage import triage_quarantines
from .state_io import atomic_json
from .worker_memory_admission import memory_admission


def publish_worker_memory_admission(state_root: Path, occupied_workers: int) -> int:
    """Persist a fresh host sample and return capacity for NEW workers only."""
    assessment = memory_admission(occupied_workers)
    atomic_json(
        state_root / "worker-memory-admission.json",
        {
            "schema_version": 1,
            **assessment,
            "observed_at_utc": datetime.now(UTC).isoformat(),
        },
    )
    return int(assessment["new_worker_budget"])


def next_worker_budget(occupied_workers: int) -> int:
    """Fresh memory gate for same-pass worker refills after a failed harvest."""
    return int(memory_admission(occupied_workers)["new_worker_budget"])


def publish_delivery_diagnostics(
    state_root: Path,
    products: list[str],
    pool_snapshot: dict,
    quarantines: dict[str, dict],
) -> tuple[dict, dict]:
    """Separate code delivery evidence from historical quarantine backlog."""
    delivery = assess_delivery_throughput(state_root, products, pool_snapshot)
    quarantine = triage_quarantines(quarantines)
    atomic_json(state_root / "verified-delivery-throughput.json", delivery)
    atomic_json(state_root / "quarantine-triage.json", quarantine)
    return delivery, quarantine


def degraded_reasons(
    *,
    lifecycle_recovery: dict,
    liveness_published: bool,
    workspace_provisioning: dict,
    convergence: dict,
    idle_streaks: dict,
    liveness: dict,
    delivery: dict,
    builder_ok: bool,
    builder_status: str,
    rc: int,
    linear_rc: int | None,
    quarantines: dict,
) -> list[dict]:
    """Do not conflate process liveness with proven software delivery."""
    result: list[dict] = []
    if lifecycle_recovery.get("publication_blocked"):
        result.append({"reason": "LIFECYCLE_STATE_PUBLICATION_BLOCKED"})
    if not liveness_published:
        result.append({"reason": "PRODUCTIVE_LIVENESS_PUBLICATION_BLOCKED"})
    if workspace_provisioning.get("status") != "OK":
        result.append(
            {
                "reason": "WORKSPACE_PROVISIONING_DEGRADED",
                "details": workspace_provisioning,
            }
        )
    if convergence.get("status") != "CONVERGED":
        result.append(
            {
                "reason": convergence.get("status"),
                "products": convergence.get("unexplained_eligible_products", []),
            }
        )
    if idle_streaks.get("status") == "P1_LIVENESS_DEFECT":
        result.append({"reason": "P1_LIVENESS_DEFECT"})
    if liveness.get("status") in {
        "STALE_ACTIVE_WORK",
        "ELIGIBLE_IDLE",
        "AUTHORIZED_NO_SLICE",
    }:
        result.append({"reason": liveness["status"]})
    if delivery.get("status") == "VERIFIED_DELIVERY_STALLED":
        result.append(
            {
                "reason": "VERIFIED_DELIVERY_STALLED",
                "seconds_since_start": delivery.get("seconds_since_start"),
                "verified_deliveries": 0,
            }
        )
    if not builder_ok and builder_status != "NO_READY_EXECUTION_SLICE":
        result.append(
            {"reason": "BUILDER_FAILED", "status": builder_status, "returncode": rc}
        )
    if linear_rc not in (None, 0):
        result.append(
            {"reason": "LINEAR_RECONCILIATION_FAILED", "returncode": linear_rc}
        )
    if quarantines:
        result.append({"reason": "QUARANTINED_GRANTS", "count": len(quarantines)})
    return result


def publish_worker_pool_snapshot(
    state_root: Path,
    pool_snapshot: dict,
    runtime_leases_snapshot: dict,
    reconciliation: list,
    dispatched_workers: list[str],
    dispatch_invocation_id: str | None,
    runtime_lease_recovery: dict | None,
) -> None:
    """Publish post-harvest worker accounting, never phantom active execution."""
    atomic_json(
        state_root / "parallel-worker-pool.json",
        {
            "schema_version": 2,
            "pool": pool_snapshot,
            "runtime_leases": runtime_leases_snapshot,
            "runtime_lease_reconciliation": reconciliation,
            "dispatched": dispatched_workers,
            "dispatch_invocation_id": dispatch_invocation_id,
            "runtime_lease_recovery": runtime_lease_recovery,
            "recorded_at_utc": datetime.now(UTC).isoformat(),
        },
    )
