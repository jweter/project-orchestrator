from __future__ import annotations

import hashlib
import json
from typing import Any

from .worker_transport import validate_packet

ALLOWED_REPOSITORIES = {
    "jweter/knowledge-engine-core",
    "jweter/knowledge-engine-web",
    "jweter/knowledge-engine-ai",
    "jweter/rocksmith-cdlc-generator",
    "jweter/Project-Everward",
    "jweter/VoiceStudio",
}


def build_laptop_job(packet: dict[str, Any], *, workspace_root: str) -> dict[str, Any]:
    """Build a bounded, replay-safe job for the unattended Windows laptop actor."""
    work = validate_packet(packet)
    if work["repository"] not in ALLOWED_REPOSITORIES:
        raise ValueError("laptop actor repository is outside the product portfolio")
    if not workspace_root or not isinstance(workspace_root, str):
        raise ValueError("workspace_root must be a non-empty string")
    material = {
        "packet_id": work["packet_id"],
        "repository": work["repository"],
        "base_sha": work["base_sha"],
        "branch": work["branch"],
        "paths": work["paths"],
        "task": work["task"],
        "verification": work["verification"],
    }
    job_id = hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]
    return {
        "schema_version": 1,
        "job_id": job_id,
        **material,
        "workspace_root": workspace_root,
        "execution_mode": "UNATTENDED_IDLE",
        "checkout": {"require_clean_worktree": True, "require_exact_base_sha": True, "force_push": False},
        "permissions": {"edit_authorized_paths": True, "run_local_verification": True, "commit": True, "push_branch": True},
        "prohibitions": {"merge": True, "modify_default_branch": True, "invent_product_reality": True, "persist_private_artifacts": True},
        "result_required": ["job_id", "packet_id", "repository", "branch", "base_sha", "exact_head_sha", "status", "verification", "changed_paths", "evidence_ref"],
    }


def validate_laptop_result(job: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Validate laptop evidence before it can enter worker_transport."""
    if not isinstance(result, dict):
        raise ValueError("laptop result must be an object")
    required = set(job["result_required"])
    missing = sorted(required - result.keys())
    if missing:
        raise ValueError(f"laptop result missing required fields: {missing}")
    for field in ("job_id", "packet_id", "repository", "branch", "base_sha"):
        if result[field] != job[field]:
            raise ValueError(f"laptop result {field} mismatch")
    head = result["exact_head_sha"]
    if not isinstance(head, str) or len(head) != 40 or any(c not in "0123456789abcdef" for c in head):
        raise ValueError("exact_head_sha must be a lowercase 40-character SHA")
    if result.get("merged") is True or result.get("product_reality_verified") is True:
        raise ValueError("laptop actor exceeded authority")
    changed = sorted(set(result["changed_paths"]))
    authorized = set(job["paths"])
    if authorized and not set(changed).issubset(authorized):
        raise ValueError("laptop actor changed unauthorized paths")
    if result["status"] not in {"PASS", "FAIL", "REVIEW_REQUIRED", "PRODUCT_REALITY_REQUIRED", "ENVIRONMENT_FAILURE"}:
        raise ValueError("unsupported laptop result status")
    return {
        "job_id": job["job_id"],
        "packet_id": job["packet_id"],
        "project": result.get("project"),
        "repository": job["repository"],
        "branch": job["branch"],
        "base_sha": job["base_sha"],
        "head_sha": head,
        "status": result["status"],
        "verification": result["verification"],
        "changed_paths": changed,
        "evidence_ref": result["evidence_ref"],
        "merged": False,
        "product_reality_verified": False,
    }


def next_actor_action(*, idle: bool, active_job: dict[str, Any] | None, queued_jobs: list[dict[str, Any]]) -> dict[str, Any]:
    """Keep the laptop productive without requiring a human prompt."""
    if active_job:
        return {"action": "CONTINUE", "job_id": active_job["job_id"]}
    if not idle:
        return {"action": "WAIT_FOR_IDLE", "job_id": None}
    if queued_jobs:
        ordered = sorted(queued_jobs, key=lambda item: item["job_id"])
        return {"action": "CLAIM", "job_id": ordered[0]["job_id"]}
    return {"action": "POLL", "job_id": None}
