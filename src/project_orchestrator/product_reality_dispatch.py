from __future__ import annotations

import hashlib
import json
from typing import Any

from .product_reality import validate_product_reality_debt


_AUTOMATABLE_KINDS = {
    "packaged_app",
    "unreal_playtest",
    "real_game_playback",
    "private_source_validation",
}
_HUMAN_KINDS = {
    "visual_acceptance",
    "timing_acceptance",
    "tone_acceptance",
    "fingering_acceptance",
    "human_acceptance",
}


def dispatch_plan(entry: dict[str, Any], *, branch: str, base_sha: str) -> dict[str, Any]:
    debt = validate_product_reality_debt(entry)
    if debt["state"] not in {"PENDING", "FAILED"}:
        return {"status": "NO_ACTIVE_DEBT", "debt_id": debt["debt_id"]}
    if debt["kind"] in _HUMAN_KINDS:
        return {
            "status": "HUMAN_EVIDENCE_REQUIRED",
            "debt_id": debt["debt_id"],
            "repository": debt["repository"],
            "dependency_scope": debt["dependency_scope"],
        }
    if debt["kind"] not in _AUTOMATABLE_KINDS:
        return {"status": "UNSUPPORTED", "debt_id": debt["debt_id"]}
    if not branch.strip() or len(base_sha) != 40:
        raise ValueError("branch and exact 40-character base_sha are required")
    seed = json.dumps([debt["debt_id"], branch, base_sha], separators=(",", ":"))
    job_id = "pr-" + hashlib.sha256(seed.encode()).hexdigest()[:16]
    return {
        "status": "DISPATCHABLE",
        "debt_id": debt["debt_id"],
        "job": {
            "job_id": job_id,
            "packet_id": "product-reality-" + debt["debt_id"],
            "repository": debt["repository"],
            "branch": branch,
            "base_sha": base_sha,
            "purpose": "product_reality_evidence_collection",
            "product_reality_authority": False,
            "merge_authority": False,
            "dependency_scope": debt["dependency_scope"],
            "source_ref": debt["source_ref"],
            "kind": debt["kind"],
        },
    }
