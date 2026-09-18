from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .product_reality_dispatch import dispatch_plan


def queue_product_reality_job(
    entry: dict[str, Any],
    *,
    branch: str,
    base_sha: str,
    queue_dir: str | Path,
    verification: list[str] | None = None,
) -> dict[str, Any]:
    plan = dispatch_plan(entry, branch=branch, base_sha=base_sha)
    if plan["status"] != "DISPATCHABLE":
        return plan
    job = dict(plan["job"])
    if not verification or not all(isinstance(x, str) and x.strip() for x in verification):
        raise ValueError("automated Product Reality dispatch requires a bounded verification plan")
    job["verification"] = list(verification)
    job["task"] = f"Collect bounded Product Reality evidence for {plan['debt_id']}"
    job["project"] = entry["project"]
    job["paths"] = []
    queue = Path(queue_dir)
    queue.mkdir(parents=True, exist_ok=True)
    path = queue / f"{job['job_id']}.json"
    payload = json.dumps(job, indent=2, sort_keys=True) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != payload:
            raise ValueError("existing Product Reality queue job identity collision")
        return {**plan, "status": "ALREADY_QUEUED", "queue_path": str(path)}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)
    return {**plan, "status": "QUEUED", "queue_path": str(path)}
