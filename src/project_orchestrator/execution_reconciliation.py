from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .claude_dispatch import ingest_session_result, specialist_scorecard


TERMINAL_WORKER = {"PASS", "FAIL", "NO_OP", "BLOCKED"}


def reconcile_laptop_queue(queue_dir: Path, results_dir: Path) -> list[dict[str, Any]]:
    reconciled = []
    if not queue_dir.exists():
        return reconciled
    for task_path in sorted(queue_dir.glob("*.json")):
        task = json.loads(task_path.read_text(encoding="utf-8"))
        result_path = results_dir / task_path.name
        if not result_path.exists():
            reconciled.append({"task_key": task["task_key"], "actor": "LAPTOP_ACTOR", "status": "PENDING"})
            continue
        result = json.loads(result_path.read_text(encoding="utf-8"))
        status = str(result.get("status", "UNKNOWN")).upper()
        if status not in TERMINAL_WORKER:
            status = "UNKNOWN"
        reconciled.append({"task_key": task["task_key"], "actor": "LAPTOP_ACTOR", "status": status,
                           "repository": task["repository"], "evidence": result})
    return reconciled


def reconcile_claude_result(state_dir: Path, dispatch_id: str, evidence: dict[str, Any]) -> dict[str, Any]:
    required = {"outcome_kind"}
    missing = required - evidence.keys()
    if missing:
        raise ValueError(f"Claude reconciliation missing: {sorted(missing)}")
    return ingest_session_result(
        state_dir, dispatch_id, outcome_kind=evidence["outcome_kind"],
        repository=evidence.get("repository"), pr_url=evidence.get("pr_url"),
        verification=evidence.get("verification"),
        product_advancement=bool(evidence.get("product_advancement", False)),
        human_intervention=bool(evidence.get("human_intervention", False)),
        notes=str(evidence.get("notes", "")),
    )


def reconciliation_snapshot(claude_state_dir: Path, laptop_queue_dir: Path,
                            laptop_results_dir: Path) -> dict[str, Any]:
    laptop = reconcile_laptop_queue(laptop_queue_dir, laptop_results_dir)
    claude_rows = [json.loads(p.read_text(encoding="utf-8")) for p in claude_state_dir.glob("*.json")] if claude_state_dir.exists() else []
    pending_claude = [r for r in claude_rows if r.get("status") == "DISPATCHED"]
    completed_claude = [r for r in claude_rows if r.get("status") in {"PASS","FAIL","NO_OP"}]
    pending_laptop = [r for r in laptop if r["status"] == "PENDING"]
    return {
        "claude": {"pending": len(pending_claude), "completed": len(completed_claude),
                   "scorecard": specialist_scorecard(claude_state_dir)},
        "laptop": {"pending": len(pending_laptop), "terminal": len(laptop)-len(pending_laptop), "results": laptop},
        "ready_for_next_work": not pending_claude and not pending_laptop,
    }
