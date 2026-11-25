"""Read-only dependency reconciliation against existing canonical backlog state.

Local completion records are observations, not remote acceptance or execution grants.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import iron_god_backlog as backlog
from . import iron_god_roadmap_refill as refill
from . import iron_god_structured_handoff as structured
from . import mcp_coordination_accountability as accountability

MAX_BYTES = 4_000_000
MAX_ITEMS = 1000


def _snapshot(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError("SOURCE_UNAVAILABLE")
    with path.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("SOURCE_TOO_LARGE")
    return data


def _decode(data: bytes) -> Any:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("DUPLICATE_SOURCE_KEY")
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=unique)


def reconcile_dependencies(
    database: Path, *, participant: str, request_id: str,
    release_repo: Path, state_root: Path, now: datetime | None = None,
) -> dict[str, Any]:
    envelope = structured.read_engineering_handoff(
        database, participant=participant, request_id=request_id)
    payload = envelope["payload"]
    moment = now if now is not None else datetime.now(timezone.utc)
    if moment.utcoffset() is None:
        raise ValueError("INVALID_ASSESSMENT_TIME")
    result: dict[str, Any] = {
        "request_id": request_id, "repository": payload["repository"],
        "slice_id": payload["slice_id"], "status": "UNVERIFIED",
        "reason": "CANONICAL_SOURCE_UNAVAILABLE", "dependencies": [],
        "canonical_dependencies_satisfied": False, "dependencies_verified": False,
        "can_execute": False,
    }
    deadline = envelope["deadline_at_utc"]
    if deadline is not None and accountability._time(deadline) <= moment:
        return {**result, "status": "EXPIRED", "reason": "HANDOFF_DEADLINE_ELAPSED"}
    lane = state_root / "iron-god-products"
    paths = [release_repo / backlog.BACKLOG_PATH, backlog.state_path(lane)]
    runtime = refill.store_path(lane)
    try:
        if runtime.exists() or runtime.is_symlink():
            paths.append(runtime)
        raw = [_snapshot(path) for path in paths]
        definitions, state = [_decode(data) for data in raw[:2]]
        if (not isinstance(definitions, dict) or not isinstance(definitions.get("items"), list)
                or not isinstance(state, dict) or type(state.get("schema_version")) is not int
                or state["schema_version"] != 1 or not isinstance(state.get("items"), dict)):
            raise ValueError("INVALID_CANONICAL_SCHEMA")
        items, rejected = backlog.load_backlog(release_repo)
        if rejected or len(items) != len(definitions["items"]):
            raise ValueError("INVALID_RELEASE_BACKLOG")
        if len(paths) == 3:
            store = _decode(raw[2])
            if (not isinstance(store, dict) or type(store.get("schema_version")) is not int
                    or store["schema_version"] != 1 or not isinstance(store.get("items"), list)):
                raise ValueError("INVALID_RUNTIME_BACKLOG")
            runtime_items = refill.admitted_items(lane, items)
            if len(runtime_items) != len(store["items"]):
                raise ValueError("AMBIGUOUS_RUNTIME_BACKLOG")
            items += runtime_items
        rows = state["items"]
        if len(items) > MAX_ITEMS or len(rows) > MAX_ITEMS:
            raise ValueError("SOURCE_TOO_LARGE")
        if any(not isinstance(row, dict) or not isinstance(row.get("outcome", ""), str)
               for row in rows.values()):
            raise ValueError("INVALID_CANONICAL_ROW")
        if any(count != 1 for count in Counter(item["id"] for item in items).values()):
            raise ValueError("AMBIGUOUS_ITEM_ID")
        if raw != [_snapshot(path) for path in paths] or (len(paths) == 2 and runtime.exists()):
            return {**result, "reason": "CANONICAL_SOURCE_CHANGED"}
    except (OSError, ValueError, TypeError, AttributeError, RecursionError):
        return result
    index = {item["id"]: item for item in items}
    target = index.get(payload["slice_id"])
    if target is None or target["repository"] != payload["repository"]:
        return {**result, "reason": "CANONICAL_TARGET_UNKNOWN"}
    if target["status"] == "retired" or target.get("superseded_by"):
        return {**result, "reason": "CANONICAL_TARGET_SUPERSEDED"}
    expected = target["depends_on"]
    if len(expected) != len(set(expected)) or set(expected) != set(payload["dependencies"]):
        return {**result, "reason": "DEPENDENCY_DECLARATION_MISMATCH"}
    observed = []
    for ident in payload["dependencies"]:
        item = index.get(ident)
        row = rows.get(ident, {})
        status = "UNKNOWN"
        if item is not None and item["repository"] == payload["repository"]:
            if row.get("terminal_outcome_conflict"):
                status = "CONFLICT"
            elif item["status"] == "retired" or item.get("superseded_by"):
                status = "SUPERSEDED"
            elif backlog.is_done(item, state):
                status = "CANONICAL_COMPLETE"
            elif row.get("outcome") in backlog.IN_FLIGHT:
                status = "IN_FLIGHT"
            else:
                status = "OUTSTANDING"
        observed.append({"slice_id": ident, "status": status})
    satisfied = all(row["status"] == "CANONICAL_COMPLETE" for row in observed)
    return {**result, "status": "OBSERVED", "reason": "LOCAL_CANONICAL_STATE_ONLY",
            "dependencies": observed, "canonical_dependencies_satisfied": satisfied}
