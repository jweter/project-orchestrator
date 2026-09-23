from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .foreman import build_foreman_plan
from .jev_runtime import runtime_cycle


def run_unattended_cycle(
    inventory: dict[str, Any],
    live_snapshot: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    run_id: str,
    ledger_path: Path,
    execution_records: list[dict[str, Any]] | None = None,
    events: list[dict[str, Any]] | None = None,
    machine_health: dict[str, Any] | None = None,
    steward_state: dict[str, Any] | None = None,
    dispatch: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Run one Foreman cycle with automatic Jev evidence capture/reconciliation."""
    plan = build_foreman_plan(
        inventory,
        live_snapshot,
        candidates,
        execution_records=execution_records,
        events=events,
        machine_health=machine_health,
        steward_state=steward_state,
    )

    jev = runtime_cycle(
        ledger_path,
        run_id=run_id,
        plan=plan,
        results=execution_records or [],
    )

    dispatched = []
    if dispatch is not None:
        for lane in plan["dispatch_queue"]:
            dispatch(lane)
            dispatched.append(lane["lane_id"])

    return {
        "schema_version": 1,
        "run_id": run_id,
        "foreman_plan": plan,
        "jev_runtime": jev,
        "dispatched_lane_ids": dispatched,
        "immediate_continuation": plan["immediate_continuation"],
    }
