from __future__ import annotations

import hashlib
import json
from typing import Any

ALLOWED_PROJECTS = {"knowledge-engine", "rocksmith", "everward"}
ALLOWED_STATES = {"PENDING", "VERIFIED", "FAILED", "SUPERSEDED"}
ALLOWED_KINDS = {
    "packaged_app",
    "unreal_playtest",
    "visual_acceptance",
    "real_game_playback",
    "timing_acceptance",
    "tone_acceptance",
    "fingering_acceptance",
    "private_source_validation",
    "human_acceptance",
    "other",
}


def _id(entry: dict[str, Any]) -> str:
    stable = {k: entry[k] for k in sorted(entry) if k != "debt_id"}
    return hashlib.sha256(
        json.dumps(stable, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:20]


def validate_product_reality_debt(entry: dict[str, Any]) -> dict[str, Any]:
    """Validate sanitized Product Reality debt metadata.

    Raw screenshots, audio, private score material, logs, payloads, credentials,
    and subjective PASS/FAIL claims are intentionally excluded from this store.
    """
    if not isinstance(entry, dict):
        raise ValueError("Product Reality debt must be an object")
    required = {
        "project",
        "repository",
        "kind",
        "state",
        "summary",
        "dependency_scope",
        "source_ref",
    }
    missing = required - entry.keys()
    if missing:
        raise ValueError("Product Reality debt missing fields: " + ", ".join(sorted(missing)))

    if entry["project"] not in ALLOWED_PROJECTS:
        raise ValueError("Product Reality debt project is not in the portfolio")
    if entry["kind"] not in ALLOWED_KINDS:
        raise ValueError("unsupported Product Reality debt kind")
    if entry["state"] not in ALLOWED_STATES:
        raise ValueError("unsupported Product Reality debt state")

    for field in ("repository", "summary", "dependency_scope", "source_ref"):
        if not isinstance(entry[field], str) or not entry[field].strip():
            raise ValueError(f"{field} must be non-empty text")

    forbidden = {
        "raw_payload",
        "screenshot",
        "image",
        "audio",
        "score",
        "private_source",
        "credentials",
        "token",
        "secret",
        "verdict_details",
    }
    if forbidden & set(entry):
        raise ValueError("Product Reality debt contains forbidden payload-bearing fields")

    normalized = {key: entry[key].strip() if isinstance(entry[key], str) else entry[key] for key in required}
    normalized["debt_id"] = _id(normalized)
    supplied = entry.get("debt_id")
    if supplied is not None and supplied != normalized["debt_id"]:
        raise ValueError("debt_id does not match sanitized content")
    return normalized


def aggregate_product_reality_debt(entries: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(entries, list):
        raise ValueError("Product Reality debt input must be a list")
    normalized = [validate_product_reality_debt(entry) for entry in entries]
    ids: set[str] = set()
    unique: list[dict[str, Any]] = []
    for item in normalized:
        if item["debt_id"] in ids:
            continue
        ids.add(item["debt_id"])
        unique.append(item)

    active = [item for item in unique if item["state"] in {"PENDING", "FAILED"}]
    by_project = {}
    for project in sorted(ALLOWED_PROJECTS):
        project_items = [item for item in active if item["project"] == project]
        by_project[project] = {
            "active_count": len(project_items),
            "pending_count": sum(item["state"] == "PENDING" for item in project_items),
            "failed_count": sum(item["state"] == "FAILED" for item in project_items),
            "items": sorted(project_items, key=lambda item: (item["repository"], item["kind"], item["debt_id"])),
        }

    return {
        "schema_version": 1,
        "active_debt_count": len(active),
        "by_project": by_project,
        "rules": [
            "Product Reality debt is metadata-only and contains no raw private payloads",
            "PENDING/FAILED debt blocks only its declared dependency_scope",
            "VERIFIED/SUPERSEDED items remain historical evidence but are not active debt",
            "the aggregator never invents human PASS/FAIL",
        ],
    }


def project_debt_summaries(entries: list[dict[str, Any]]) -> dict[str, list[str]]:
    aggregate = aggregate_product_reality_debt(entries)
    result: dict[str, list[str]] = {}
    for project, data in aggregate["by_project"].items():
        result[project] = [
            f"{item['kind']}: {item['summary']} [scope: {item['dependency_scope']}]"
            for item in data["items"]
        ]
    return result
