"""Read-only source-of-truth selector for verified difficult-slice offload.

This module intentionally does not call offer_slice, claim_slice, launch a
worker or authorize a mutation. It projects a candidate only when the *same*
recorded failure is present in the canonical backlog, packet and SQLite
development-memory. Live GitHub ownership and owner-control checks are still
required in a future authorized producer.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from . import iron_god_backlog as backlog
from . import iron_god_development_memory as memory
from . import iron_god_offload as offload
from . import iron_god_roadmap_refill as roadmap_refill

_CAPABILITY_BLOCKERS = frozenset({
    "ATTEMPTS_EXHAUSTED",
    "NEEDS_DIFFERENT_CAPABILITY",
    "REPEATED_FAILED_SOURCE",
    "DIRECTED_ATTEMPT_SPENT",
    "LEVEL_ABOVE_MEASURED_CAPABILITY",
})
# A saturated local attempt is not necessarily a failed implementation. In
# particular, non-discriminating acceptance tests fail before the model runs;
# conflicting ownership, environmental faults and unclassified outcomes must
# not be offered to a stronger model as proven capability failures.
_IMPLEMENTATION_FAILURE_CLASSES = frozenset({
    "REPLY_UNEXPECTED_UNIT", "REPLY_DUPLICATE_UNIT", "REPLY_MALFORMED_BLOCK",
    "REPLY_MISSING_UNITS", "REPLY_NO_CODE", "REPLY_UNNAMED_CODE",
    "SLICE_TESTS_FAILED", "STATIC_MYPY_UNTYPED", "STATIC_LINE_LENGTH",
    "STATIC_MYPY",
})
MAX_SCAN = 1_000


def _blocked(reason: str) -> dict[str, Any]:
    return {
        "status": "NO_VERIFIED_OFFLOAD_CANDIDATE",
        "reason": reason,
        "authority": "ADVISORY_ONLY",
        "execution_authorized": False,
        "can_claim_worker": False,
    }


def _attempt(lane_root: Path, packet_id: str) -> dict[str, Any] | None:
    """Read the existing failure history without creating or migrating SQLite."""
    db_path = memory.db_path(lane_root)
    if not db_path.is_file() or db_path.is_symlink():
        return None
    try:
        with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro",
                                     uri=True, timeout=3)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            row = db.execute(
                "SELECT item_id,repository,base_sha,failure_class,outcome "
                "FROM attempts WHERE packet_id=?", (packet_id,),
            ).fetchone()
            return dict(row) if row else None
    except (sqlite3.Error, OSError, ValueError):
        return None


def inspect_difficult_slice(
    release_repo: Path,
    lane_root: Path,
    repository: str,
    *,
    observed_remote_head: str,
    measured_local_level: int,
    open_pr_paths: list[str],
    active_packet_id: str | None,
) -> dict[str, Any]:
    """Identify at most one documented failure from the existing approved lane.

    All *live* arguments are untrusted observations from the caller. This
    read-only output is evidence for an independently authorized controller,
    never proof that owner authorization or a live GitHub lease exists.
    """
    if repository not in offload._REPO:
        return _blocked("REPOSITORY_UNREGISTERED")
    if (not isinstance(observed_remote_head, str)
            or not offload._SHA.fullmatch(observed_remote_head)):
        return _blocked("EXACT_LIVE_GIT_HEAD_REQUIRED")
    if type(measured_local_level) is not int or not 0 <= measured_local_level <= 5:
        return _blocked("MEASURED_CAPABILITY_REQUIRED")
    if not isinstance(open_pr_paths, list) or any(
        not isinstance(p, str) for p in open_pr_paths
    ):
        return _blocked("LIVE_GITHUB_PATH_EVIDENCE_REQUIRED")
    if active_packet_id is not None:
        return _blocked("ACTIVE_PRODUCT_PACKET_OWNS_LANE")
    if not backlog.state_path(lane_root).is_file() or backlog.state_path(lane_root).is_symlink():
        return _blocked("CANONICAL_BACKLOG_STATE_MISSING")
    items, rejected = backlog.load_backlog(release_repo)
    if rejected:
        return _blocked("BACKLOG_REJECTED_ITEMS_PRESENT")
    if not 0 < len(items) <= MAX_SCAN:
        return _blocked("BACKLOG_UNAVAILABLE_OR_UNBOUNDED")
    try:
        state = backlog.load_state(lane_root)
    except (ValueError, OSError):
        return _blocked("BACKLOG_STATE_INVALID")
    if not isinstance(state.get("items"), dict):
        return _blocked("BACKLOG_STATE_INVALID")
    if len(state["items"]) > MAX_SCAN:
        return _blocked("BACKLOG_STATE_UNBOUNDED")
    # Never feed malformed local-state rows to backlog.is_done/exclusion:
    # those helpers assume a canonical engine-written row structure.
    for item_id, row in state["items"].items():
        if (not isinstance(item_id, str) or not isinstance(row, dict)
                or type(row.get("attempts", 0)) is not int
                or row.get("attempts", 0) < 0
                or not isinstance(row.get("outcome", ""), str)):
            return _blocked("BACKLOG_STATE_INVALID")
        for field in ("packets", "environment_packets", "test_failed_packets"):
            values = row.get(field, [])
            if not isinstance(values, list) or any(not isinstance(p, str) for p in values):
                return _blocked("BACKLOG_STATE_INVALID")
        fingerprints = row.get("failed_source_by_packet", {})
        if not isinstance(fingerprints, dict) or any(
            not isinstance(k, str) or not isinstance(v, str)
            for k, v in fingerprints.items()
        ):
            return _blocked("BACKLOG_STATE_INVALID")
    done = {item["id"] for item in items if backlog.is_done(item, state)}
    # The resident product lane considers both release backlog and admitted
    # runtime roadmap items. A static-only read misses the latter's canonical
    # in-flight reservations. Do not select new candidates from runtime items;
    # only include them to veto occupied paths.
    runtime_path = roadmap_refill.store_path(lane_root)
    if runtime_path.is_symlink():
        return _blocked("RUNTIME_BACKLOG_STORE_INVALID")
    if runtime_path.is_file():
        try:
            runtime_data = json.loads(runtime_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return _blocked("RUNTIME_BACKLOG_STORE_INVALID")
        if not isinstance(runtime_data, dict) or not isinstance(runtime_data.get("items"), list):
            return _blocked("RUNTIME_BACKLOG_STORE_INVALID")
    runtime_items = roadmap_refill.admitted_items(lane_root, items)
    if len(items) + len(runtime_items) > MAX_SCAN:
        return _blocked("BACKLOG_UNAVAILABLE_OR_UNBOUNDED")
    # Canonical in-flight rows reserve files even when the other item is
    # assigned to Iron God. backlog.exclusion() detects different-developer
    # conflicts, but not concurrent reservations from the same agent.
    in_flight_paths = {
        other["id"]: backlog.edit_files(other)
        for other in [*items, *runtime_items]
        if other["repository"] == repository
        and state["items"].get(other["id"], {}).get("outcome") in backlog.IN_FLIGHT
    }
    for item in items:
        if item["repository"] != repository:
            continue
        if item["status"] != "approved" or item.get("assignee") != backlog.AGENT_ID:
            continue
        if item["id"] in done or any(dep not in done for dep in item["depends_on"]):
            continue
        row = state["items"].get(item["id"])
        if not isinstance(row, dict) or row.get("outcome") in backlog.IN_FLIGHT:
            continue
        failed_packet_id = backlog.failed_worker_evidence(row)
        if not failed_packet_id or not offload._TOKEN.fullmatch(failed_packet_id):
            continue
        excluded = backlog.exclusion(
            item, state, items=items, done=done, max_level=measured_local_level,
        )
        if excluded is None or excluded[0] not in _CAPABILITY_BLOCKERS:
            continue
        allowed = sorted(backlog.edit_files(item))
        if not 1 <= len(allowed) <= offload.MAX_PATHS:
            continue
        try:
            allowed = [offload._allowed_path(path) for path in allowed]
        except offload.OffloadRejected:
            continue
        # Caller-supplied active_packet_id and GitHub occupancy can be stale.
        # Consult every other canonical in-flight reservation before offering
        # any overlapping files, including reservations owned by this agent.
        if any(set(allowed).intersection(paths) for owner, paths in in_flight_paths.items()
               if owner != item["id"]):
            continue
        if set(allowed).intersection(open_pr_paths):
            continue
        packet_path = Path(lane_root) / "packets" / f"{failed_packet_id}.json"
        if not packet_path.is_file() or packet_path.is_symlink():
            continue
        try:
            packet = json.loads(packet_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if not isinstance(packet, dict) or (
            packet.get("packet_id") != failed_packet_id
            or packet.get("repository") != repository
            or packet.get("base_sha") != observed_remote_head
            or not isinstance(packet.get("slice"), dict)
            or packet["slice"].get("id") != item["id"]
        ):
            continue
        # The worker packet stores only these execution-relevant fields,
        # not the entire mutable backlog row. An edited build/direct spec,
        # scope, or roadmap must not inherit a previous model's failure.
        original = packet["slice"]
        snapshot_fields = (
            "id", "title", "roadmap_ref", "addresses", "why", "consumer",
            "level", "build", "direct",
        )
        if any(original.get(key) != item.get(key) for key in snapshot_fields):
            continue
        packet_scope = original.get("build") or original.get("direct")
        if not isinstance(packet_scope, dict):
            continue
        attempted_files = {
            packet_scope.get("module_path"), packet_scope.get("test_path"),
        }
        # Interface files are not individually snapshotted in legacy packets;
        # refuse unproven extra scope rather than treating it as attempted.
        if any(path not in attempted_files for path in allowed):
            continue
        attempt = _attempt(lane_root, failed_packet_id)
        if (attempt is None or attempt.get("item_id") != item["id"]
                or attempt.get("repository") != repository
                or attempt.get("base_sha") != observed_remote_head
                or attempt.get("failure_class") not in _IMPLEMENTATION_FAILURE_CLASSES
                or not str(attempt.get("outcome", "")).startswith("FAIL")):
            continue
        try:
            identity = offload._offer_fields(
                repository, item["id"], observed_remote_head, allowed,
                str(item["roadmap_ref"]), f"packet:{failed_packet_id}",
            )
        except offload.OffloadRejected:
            continue
        return {
            "status": "CANDIDATE_UNVERIFIED",
            "candidate": identity,
            "source": "APPROVED_BACKLOG_AND_EXISTING_FAILED_PACKET_SQLITE",
            "reason": excluded[0],
            "authority": "ADVISORY_ONLY",
            "execution_authorized": False,
            "can_claim_worker": False,
            "requires": [
                "CURRENT_AUTHENTICATED_OWNER_SESSION",
                "CURRENT_CANONICAL_WORKER_LEASE",
                "FRESH_GITHUB_HEAD_AND_PATH_OWNERSHIP",
                "NATIVE_PREFLIGHT_AND_PR_CI_REVIEW",
            ],
        }
    return _blocked("NO_EXACT_HEAD_CAPABILITY_FAILURE")
