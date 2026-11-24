"""Read-only multi-surface engineering evidence for Iron God's seven repositories.

One query joins the EXISTING event ledger, the EXISTING development-memory SQLite
store and fresh supervisor projections. It is not a second owner controller,
lease database, acceptance authority, dispatcher, or process. No database is
created or migrated on a read. The existing supervisor alone records events.
"""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import iron_god_calibration as calibration
from . import iron_god_microtask_progress as microtask_progress
from . import iron_god_learning_feedback as learning_feedback
from . import iron_god_model_routing as model_routing
from . import iron_god_development_memory as memory
from . import iron_god_observability as observability

SCHEMA_VERSION = 1
MAX_RESULTS = 20
MAX_SCAN = 200
MAX_JSON_LEN = 60_000
_SAFE = re.compile(r"^[a-zA-Z0-9._:/#@+\\-]{1,120}$")
_SHA = re.compile(r"^[0-9a-f]{40}$")


def _token(value: Any) -> str | None:
    return value if isinstance(value, str) and _SAFE.fullmatch(value) else None


def _source(path: Path) -> dict[str, Any]:
    """One explicit existing database, never open a missing path for writing."""
    return {"path": path.name, "status": "AVAILABLE" if path.is_file() else "UNAVAILABLE"}


def _connect_existing(path: Path, *, supported_versions: tuple[int, ...]) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path.name)
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)
    try:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version not in supported_versions:
            raise ValueError("UNSUPPORTED_EVIDENCE_SCHEMA")
        return db
    except BaseException:
        db.close()
        raise


def event_history(state_root: Path, *, repository: str = "", limit: int = 8) -> dict[str, Any]:
    """Small, provenance-bound historical projection. Never use as live authority."""
    _validate(repository, limit)
    path = Path(state_root) / "observability" / "development-events.sqlite"
    if not path.is_file():
        return {"source": "development-events.sqlite", "status": "UNAVAILABLE",
                "historical_only": True, "events": [], "scan_limit": MAX_SCAN}
    events: list[dict[str, Any]] = []
    try:
        with closing(_connect_existing(path, supported_versions=(0, 1))) as db:
            rows = db.execute("""SELECT seq,event_key,supervisor_instance,continuation_sequence,
                observed_at_utc,status,reason,evidence_json
                FROM development_events ORDER BY seq DESC LIMIT ?""", (MAX_SCAN,)).fetchall()
        for row in rows:
            raw = row["evidence_json"]
            if not isinstance(raw, str) or len(raw) > MAX_JSON_LEN:
                continue
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                continue
            lanes = payload.get("lanes", {})
            supervisor = payload.get("supervisor", {})
            if not isinstance(lanes, dict) or not isinstance(supervisor, dict):
                raise ValueError("MALFORMED_EVENT_EVIDENCE")
            lane = lanes.get("product") or {}
            if not isinstance(lane, dict):
                raise ValueError("MALFORMED_PRODUCT_EVIDENCE")
            repo = lane.get("repository")
            if repo not in observability.REPOSITORIES:
                repo = None
            if repository and repo != repository:
                continue
            packet = _token(lane.get("packet_id"))
            events.append({
                "event_seq": row["seq"], "event_key": row["event_key"],
                "supervisor_generation": _token(row["supervisor_instance"]),
                "continuation_sequence": row["continuation_sequence"],
                "observed_at_utc": row["observed_at_utc"],
                "source": "development-events.sqlite:development_events",
                "repository": repo, "packet_id": packet,
                "status": _token(row["status"]), "reason": _token(row["reason"]),
                "product_lane_status": _token(lane.get("status")),
                "source_revision": _token(supervisor.get("release_sha")),
                "evidence_state": "OBSERVED_NOT_PRODUCT_ACCEPTED",
            })
            if len(events) >= limit:
                break
        return {"source": "development-events.sqlite", "status": "OK",
                "historical_only": True, "events": events,
                "scan_limit": MAX_SCAN,
                "scan_exhaustive": len(rows) < MAX_SCAN}
    except (sqlite3.Error, OSError, ValueError, TypeError, json.JSONDecodeError):
        return {"source": "development-events.sqlite", "status": "UNVERIFIED",
                "historical_only": True, "events": [], "scan_limit": MAX_SCAN}


def attempt_memory(state_root: Path, *, repository: str = "", limit: int = 8) -> dict[str, Any]:
    """Provenance-linked worker results, without prompts, replies or test logs."""
    _validate(repository, limit)
    path = memory.db_path(Path(state_root) / "iron-god-products")
    if not path.is_file():
        return {"source": "development-memory.sqlite3", "status": "UNAVAILABLE",
                "attempts": [], "rules": []}
    try:
        with closing(_connect_existing(path, supported_versions=(0, 1, memory.SCHEMA_VERSION))) as db:
            if repository:
                rows = db.execute("""SELECT packet_id,item_id,repository,base_sha,model,
                    failure_class,outcome,recorded_at_utc FROM attempts WHERE repository=?
                    ORDER BY recorded_at_utc DESC,packet_id DESC LIMIT ?""",
                    (repository, limit)).fetchall()
            else:
                rows = db.execute("""SELECT packet_id,item_id,repository,base_sha,model,
                    failure_class,outcome,recorded_at_utc FROM attempts
                    ORDER BY recorded_at_utc DESC,packet_id DESC LIMIT ?""", (limit,)).fetchall()
            rules = db.execute("""SELECT failure_class,kind,slices,derived_at_utc
                FROM rules ORDER BY slices DESC,failure_class LIMIT ?""", (limit,)).fetchall()
        result = []
        for row in rows:
            # Outcome is worker-reported, never proof of CI/merge/native acceptance.
            if row["repository"] not in observability.REPOSITORIES:
                continue
            result.append({
                "packet_id": _token(row["packet_id"]), "item_id": _token(row["item_id"]),
                "repository": row["repository"],
                "base_sha": row["base_sha"] if _SHA.fullmatch(row["base_sha"] or "") else None,
                "model": _token(row["model"]),
                "failure_class": _token(row["failure_class"]),
                "worker_outcome": "OBSERVED_WORKER_RESULT",
                "outcome_class": "PASS" if row["failure_class"] == "PASSED" else "NOT_VERIFIED_PASS",
                "recorded_at_utc": row["recorded_at_utc"],
            })
        return {
            "source": "development-memory.sqlite3", "status": "OK",
            "attempts": result,
            "rules": [{"failure_class": _token(row["failure_class"]),
                       "kind": _token(row["kind"]), "independent_slices": row["slices"],
                       "derived_at_utc": row["derived_at_utc"],
                       "authority": "ADVISORY_ONLY"} for row in rules],
        }
    except (sqlite3.Error, OSError, ValueError, TypeError):
        return {"source": "development-memory.sqlite3", "status": "UNVERIFIED",
                "attempts": [], "rules": []}


def _validate(repository: str, limit: int) -> None:
    if repository and repository not in observability.REPOSITORIES:
        raise ValueError("UNKNOWN_OR_UNAUTHORIZED_REPOSITORY")
    if type(limit) is not int or not 1 <= limit <= MAX_RESULTS:
        raise ValueError("INVALID_EVIDENCE_LIMIT")


def frontier_evidence(state_root: Path, *, repository: str = "", limit: int = 8,
                      now: datetime | None = None) -> dict[str, Any]:
    """Live eligible/excluded slice IDs, retaining the actual admission gate.

    This is an explanation of the already-produced Product lane eligibility
    artifact, NOT a new scheduler or permission to dispatch stale work.
    """
    _validate(repository, limit)
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    report = observability.snapshot(state_root, now=moment)
    source = report["evidence"]["eligibility"]
    if source["freshness"] != "FRESH":
        return {"status": "UNVERIFIED" if source["status"] != "OBSERVED" else "STALE",
                "source": source, "candidates": [], "next_candidate": None,
                "dispatch_authorized": False}
    path = Path(state_root) / "iron-god-products" / "eligibility.json"
    try:
        if not path.is_file() or path.stat().st_size > observability.MAX_JSON_BYTES:
            raise ValueError("ELIGIBILITY_UNAVAILABLE")
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(raw, dict):
            raise ValueError("ELIGIBILITY_ROOT_INVALID")
        if observability._time(raw.get("observed_at_utc")) != observability._time(
            source.get("observed_at_utc")
        ):
            raise ValueError("ELIGIBILITY_GENERATION_CHANGED_DURING_READ")
        items = raw.get("items")
        if not isinstance(items, list):
            raise ValueError("ELIGIBILITY_ITEMS_INVALID")
        selected: list[dict[str, Any]] = []
        for item in items[:1000]:
            if not isinstance(item, dict) or item.get("repository") not in observability.REPOSITORIES:
                continue
            if repository and item["repository"] != repository:
                continue
            item_id = _token(item.get("id"))
            if item_id is None:
                continue
            selected.append({
                "id": item_id,
                "repository": item["repository"],
                "eligible_as_observed": item.get("eligible") is True,
                "reason": _token(item.get("reason")) or "UNVERIFIED_REASON",
                "attempts": item["attempts"] if type(item.get("attempts")) is int
                             and 0 <= item["attempts"] <= 1000 else None,
                "observed_at_utc": source.get("observed_at_utc"),
            })
        earliest = next((entry for entry in selected
                         if entry["eligible_as_observed"]), None)
        return {"status": "OBSERVED_FRESH", "source": source,
                "candidates": selected[:limit], "next_candidate": earliest,
                "matching_items_scanned": len(selected), "dispatch_authorized": False,
                "new_owner_grant": False, "model_qualified_for_candidate": "UNVERIFIED"}
    except (ValueError, OSError, TypeError, json.JSONDecodeError):
        return {"status": "UNVERIFIED", "source": source,
                "candidates": [], "next_candidate": None,
                "dispatch_authorized": False}


def measured_local_admission(state_root: Path, *, now: datetime | None = None) -> dict[str, Any]:
    """Advisory view of the existing calibrated local-model router.

    Never load Ollama, start models, spend cloud tokens or override the existing
    runtime's live admission, capability gate or resource-pressure cooldown.
    """
    current = observability.snapshot(state_root, now=now)
    memory_source = current["evidence"]["memory"]
    mib = current["resources"]["free_mib"]
    if memory_source["freshness"] != "FRESH" or type(mib) is not int:
        return {"status": "UNVERIFIED_MEMORY", "qualified_level": None,
                "model": None, "new_worker_authorized": False,
                "cloud_spend_authorized": False}
    profiles: dict[str, dict[str, Any]] = {}
    root = Path(state_root)
    for model in model_routing.LADDER:
        full = "ollama/" + model
        file = calibration.profile_path(root, full)
        if not file.is_file() or file.stat().st_size > 128_000:
            continue
        try:
            item = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if isinstance(item, dict) and item.get("model") == full:
            profiles[full] = item
    legacy = calibration.profile_path(root)
    if legacy.is_file() and legacy.stat().st_size <= 128_000:
        try:
            item = json.loads(legacy.read_text(encoding="utf-8"))
            if isinstance(item, dict) and item.get("model") in {
                "ollama/" + name for name in model_routing.LADDER
            }:
                profiles.setdefault(item["model"], item)
        except (OSError, ValueError, TypeError):
            pass
    if not profiles:
        return {"status": "CALIBRATION_UNVERIFIED", "qualified_level": None,
                "model": None, "new_worker_authorized": False,
                "cloud_spend_authorized": False}
    try:
        hardware = calibration.hardware_fingerprint()
        free_gib = mib / 1024
        # Conservative memory-pressure setting; the live controller owns its
        # actual pressure state and decides whether admission may proceed.
        max_level = model_routing.max_level_now(
            profiles, free_gib, hardware=hardware, after_pressure=True,
        )
        route = model_routing.route(
            1, profiles, free_gib, hardware=hardware, after_pressure=True,
        )
        return {"status": "MEASURED_ADVISORY", "qualified_level": max_level,
                "model": _token(route.model), "reason_class":
                "ADMITTED_LOCAL" if route.model else "NOT_ADMITTED_LOCAL",
                "qualified_models": [_token(x) for x in route.qualified],
                "available_gib": round(free_gib, 2),
                "new_worker_authorized": False, "cloud_spend_authorized": False,
                "native_toolchain_qualification": "UNVERIFIED",
                "runtime_pressure_and_owner_grants_must_be_rechecked": True}
    except (OSError, ValueError, TypeError):
        return {"status": "CALIBRATION_UNVERIFIED", "qualified_level": None,
                "model": None, "new_worker_authorized": False,
                "cloud_spend_authorized": False}


def microtask_checkpoint_evidence(
    state_root: Path, *, repository: str = "", limit: int = 8,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Read existing plan-revision checkpoints, not new task or resume grants.

    An old checkpoint is useful historical evidence, but only a checkpoint
    that hashes to an *existing canonical plan* may appear in the result.
    Never infer that a local MERGED outcome has independently passed GitHub CI.
    """
    _validate(repository, limit)
    root = Path(state_root) / "iron-god-products"
    plans = root / "decompositions"
    if not plans.is_dir() or plans.is_symlink():
        return {"status": "UNAVAILABLE", "plans": [], "historical_only": True,
                "resume_authorized": False, "scan_limit": MAX_SCAN}
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    reports: list[dict[str, Any]] = []
    examined = 0
    corrupt_records = 0
    truncated = False
    try:
        for plan_file in plans.glob("*.json"):
            examined += 1
            if examined > MAX_SCAN:
                truncated = True
                break
            try:
                if (plan_file.is_symlink() or not plan_file.is_file()
                        or plan_file.stat().st_size > MAX_JSON_LEN):
                    continue
                plan = json.loads(plan_file.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                corrupt_records += 1
                continue
            if not isinstance(plan, dict) or plan.get("schema_version") != 1:
                continue
            repo = plan.get("repository")
            parent = _token(plan.get("parent_id"))
            head = plan.get("planned_at_head")
            tasks = plan.get("subtasks")
            if (repo not in observability.REPOSITORIES or (repository and repo != repository)
                    or not parent or not isinstance(head, str) or not _SHA.fullmatch(head)
                    or not isinstance(tasks, list) or not 1 <= len(tasks) <= 5):
                continue
            try:
                checkpoint = microtask_progress.checkpoint_path(root, plan)
                if (checkpoint.is_symlink() or not checkpoint.is_file()
                        or checkpoint.stat().st_size > MAX_JSON_LEN):
                    continue
                raw = json.loads(checkpoint.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                corrupt_records += 1
                continue
            if (not isinstance(raw, dict) or raw.get("schema_version") != 1
                    or raw.get("repository") != repo or raw.get("parent_id") != parent
                    or raw.get("planned_at_head") != head
                    or raw.get("product_acceptance") != "UNVERIFIED_BY_THIS_CHECKPOINT"):
                continue
            # Validate the complete collection shapes before projecting anything.
            # Malformed but parseable JSON must not suppress other valid records.
            tasks_in_checkpoint = raw.get("subtasks")
            repairs_in_checkpoint = raw.get("repair_candidates")
            if (not isinstance(tasks_in_checkpoint, list)
                    or not 1 <= len(tasks_in_checkpoint) <= 5
                    or any(not isinstance(task, dict) for task in tasks_in_checkpoint)
                    or not isinstance(repairs_in_checkpoint, list)):
                corrupt_records += 1
                continue
            stamp = observability._time(raw.get("checkpointed_at_utc"))
            seconds = (moment - stamp).total_seconds() if stamp else None
            freshness = ("FRESH" if seconds is not None
                         and -120 <= seconds <= observability.FRESH_SECONDS
                         else "STALE" if seconds is not None else "UNVERIFIED")
            subtasks: list[dict[str, Any]] = []
            for task in tasks_in_checkpoint:
                packets = task.get("packet_ids")
                subtasks.append({
                    "key": _token(task.get("key")),
                    "attempt_id": _token(task.get("attempt_id")),
                    "state_as_reported": _token(task.get("status")),
                    "receipt_as_reported": _token(task.get("receipt")),
                    "packets": [_token(x) for x in packets[:4] if _token(x)]
                               if isinstance(packets, list) else [],
                    "attempts_spent_as_reported": (
                        task["attempts"] if type(task.get("attempts")) is int
                        and 0 <= task["attempts"] <= 1000 else None
                    ),
                    "verified_independent_git_merge": False,
                })
            observed_head = raw.get("observed_repository_head")
            reports.append({
                "repository": repo, "parent_id": parent,
                "plan_sha": head,
                "observed_repository_head": (
                    observed_head if isinstance(observed_head, str)
                    and _SHA.fullmatch(observed_head) else None
                ),
                "checkpoint_at_utc": stamp.isoformat() if stamp else None,
                "freshness": freshness,
                "plan_status_as_reported": _token(raw.get("plan_status")),
                "subtasks": subtasks,
                "repair_candidate_count": (
                    min(len(raw["repair_candidates"]), 5)
                    if isinstance(raw.get("repair_candidates"), list) else 0
                ),
                "source": "microtask-progress/" + checkpoint.name,
                "plan_correlated_by_revision_hash": True,
                "product_accepted": False, "replay_or_dispatch_authorized": False,
            })
        reports.sort(key=lambda x: (x["checkpoint_at_utc"] or "", x["parent_id"]), reverse=True)
        status = ("PARTIAL_SCAN" if truncated else
                  "PARTIAL_UNVERIFIED" if corrupt_records and reports else
                  "UNVERIFIED" if corrupt_records else "OBSERVED_HISTORICAL")
        return {"status": status, "plans": reports[:limit],
                "matching_plans_scanned": len(reports), "corrupt_records": corrupt_records,
                "historical_only": True, "resume_authorized": False,
                "scan_limit": MAX_SCAN,
                "independent_git_and_owner_authority_required": True}
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return {"status": "UNVERIFIED", "plans": [], "historical_only": True,
                "resume_authorized": False, "scan_limit": MAX_SCAN}


def daily_release_update_evidence(
    state_root: Path, *, now: datetime | None = None,
) -> dict[str, Any]:
    """Expose the installed daily checker's advisory result without side effects.

    The report is not GitHub evidence, a release receipt, a worker grant or
    permission to change the pinned release. A stale or mismatched report
    must never appear as a current maintenance instruction in MCP.
    """
    directory = Path(state_root)
    report_path = directory / "iron-god-self-update" / "daily-status.json"
    pin_path = directory / "supervisor-pinned-release.json"
    unavailable = {
        "status": "UNAVAILABLE",
        "reported_update_status": None,
        "pending_update_as_observed": False,
        "owner_control_required": True,
        "install_authorized_by_mcp": False,
        "stop_workers_authorized": False,
        "restart_authorized": False,
        "github_ci_verified": False,
        "installed_release_verified": False,
    }
    if not report_path.exists():
        return unavailable
    try:
        if (report_path.is_symlink() or not report_path.is_file()
                or report_path.stat().st_size > 32_768
                or pin_path.is_symlink() or not pin_path.is_file()
                or pin_path.stat().st_size > 32_768):
            raise ValueError("RELEASE_UPDATE_RECORD_INVALID")
        record = json.loads(report_path.read_text(encoding="utf-8-sig"))
        pin = json.loads(pin_path.read_text(encoding="utf-8-sig"))
        if not isinstance(record, dict) or not isinstance(pin, dict):
            raise ValueError("RELEASE_UPDATE_ROOT_INVALID")
        statuses = {
            "CURRENT", "UPDATE_PENDING_OWNER_CONTROL",
            "UPDATE_PENDING_QUIESCENCE", "UPDATE_READY_DRY_RUN",
            "UPDATED_DISABLED_OWNER_RESUME_REQUIRED", "CHECK_OR_UPDATE_FAILED",
        }
        reported_status = record.get("status")
        pinned_sha = pin.get("accepted_sha")
        prior_sha = record.get("installed_before")
        after_sha = record.get("installed_after")
        latest_sha = record.get("latest_main_observed")
        if (record.get("schema_version") != 1
                or record.get("repository") != "jweter/project-orchestrator"
                or record.get("branch") != "main"
                or reported_status not in statuses
                or not isinstance(pinned_sha, str) or not _SHA.fullmatch(pinned_sha)
                or not isinstance(prior_sha, str) or not _SHA.fullmatch(prior_sha)
                or not isinstance(after_sha, str) or not _SHA.fullmatch(after_sha)
                or (latest_sha is not None and
                    (not isinstance(latest_sha, str) or not _SHA.fullmatch(latest_sha)))
                or record.get("advisory_only") is not True
                or any(record.get(key) is not False for key in
                       ("owner_grant_created", "workers_stopped",
                        "task_triggered_engine_start"))):
            raise ValueError("RELEASE_UPDATE_REPORT_SCHEMA_INVALID")
        observed_at = observability._time(record.get("observed_at_utc"))
        if observed_at is None:
            raise ValueError("RELEASE_UPDATE_TIME_INVALID")
        moment = (now or datetime.now(UTC)).astimezone(UTC)
        age_seconds = (moment - observed_at).total_seconds()
        historical = after_sha != pinned_sha
        freshness = ("STALE" if historical or age_seconds > 36 * 3600 else
                     "UNVERIFIED" if age_seconds < -120 else "FRESH")
        pending = (reported_status in {
            "UPDATE_PENDING_OWNER_CONTROL", "UPDATE_PENDING_QUIESCENCE",
            "UPDATE_READY_DRY_RUN",
        } and latest_sha is not None and latest_sha != pinned_sha)
        if (reported_status.startswith("UPDATE_PENDING_")
                and latest_sha == pinned_sha and not historical):
            freshness = "UNVERIFIED"
        return {
            "status": "OBSERVED_" + freshness,
            "reported_update_status": reported_status,
            "observed_at_utc": observed_at.isoformat(),
            "installed_sha_at_check": prior_sha,
            "current_pinned_sha": pinned_sha,
            "observed_github_main_sha": latest_sha,
            "pending_update_as_observed": pending and freshness == "FRESH",
            "owner_control_required": True,
            "installed_pin_matches_report": not historical,
            "install_authorized_by_mcp": False,
            "stop_workers_authorized": False,
            "restart_authorized": False,
            "github_ci_verified": False,
            "installed_release_verified": False,
            "source": "iron-god-self-update/daily-status.json",
        }
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return {**unavailable, "status": "UNVERIFIED"}


def publication_evidence(state_root: Path, *, repository: str = "",
                         limit: int = 8) -> dict[str, Any]:
    """Correlate local publication receipts with exact SHA, not a GitHub verdict."""
    _validate(repository, limit)
    path = Path(state_root) / "iron-god-products" / "publications.json"
    if not path.is_file():
        return {"status": "UNAVAILABLE", "receipts": [], "github_verified": False}
    try:
        if path.stat().st_size > observability.MAX_JSON_BYTES:
            raise ValueError("PUBLICATION_TOO_LARGE")
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(raw, dict):
            raise ValueError("PUBLICATION_ROOT_INVALID")
        rows = raw.get("items")
        if not isinstance(rows, dict):
            raise ValueError("PUBLICATION_ITEMS_INVALID")
        receipts: list[dict[str, Any]] = []
        for packet_id, item in list(rows.items())[:1000]:
            if not isinstance(item, dict) or item.get("repository") not in observability.REPOSITORIES:
                continue
            if repository and item["repository"] != repository:
                continue
            packet = _token(packet_id)
            if packet is None:
                continue
            pr = item.get("pr") if isinstance(item.get("pr"), dict) else {}
            number = pr.get("number")
            receipts.append({
                "repository": item["repository"], "packet_id": packet,
                "local_state": _token(item.get("state")),
                "pr_number": number if type(number) is int and 0 < number < 1_000_000 else None,
                "head_sha": item["head_sha"] if _SHA.fullmatch(item.get("head_sha") or "") else None,
                "local_merged_sha": (item.get("merged_sha") if
                                    _SHA.fullmatch(item.get("merged_sha") or "") else None),
                "source": "iron-god-products/publications.json",
                "evidence_class": "LOCAL_REPORTED_NOT_INDEPENDENT_GITHUB_PROOF",
                "current_head_ci_verified": False,
                "release_installed_verified": False,
                "product_accepted_verified": False,
            })
            if len(receipts) == limit:
                break
        return {"status": "OBSERVED_LOCAL", "receipts": receipts,
                "github_verified": False,
                "fresh_exact_head_review_required": True}
    except (ValueError, OSError, TypeError, json.JSONDecodeError):
        return {"status": "UNVERIFIED", "receipts": [], "github_verified": False}


def _graph(events: list[dict[str, Any]], attempts: list[dict[str, Any]]) -> dict[str, Any]:
    """Only evidenced relationships; no inferred/LLM-generated dependencies."""
    edges: list[dict[str, str | int]] = []
    seen: set[tuple[str, str, str]] = set()
    for event in events:
        repo, packet = event.get("repository"), event.get("packet_id")
        if not repo or not packet:
            continue
        key = (repo, "REPOSITORY_OBSERVED_PACKET", packet)
        if key in seen:
            continue
        seen.add(key)
        edges.append({"subject": repo, "predicate": key[1], "object": packet,
                      "source_event_seq": event["event_seq"],
                      "evidence_class": "OBSERVED"})
    for attempt in attempts:
        packet, failure = attempt.get("packet_id"), attempt.get("failure_class")
        if not packet or not failure:
            continue
        key = (packet, "ATTEMPT_REPORTED_CLASS", failure)
        if key in seen:
            continue
        seen.add(key)
        edges.append({"subject": packet, "predicate": key[1], "object": failure,
                      "source": "development-memory.sqlite3",
                      "evidence_class": "WORKER_REPORTED"})
    return {"status": "EVIDENCED_ONLY", "edges": edges, "inferred_dependencies": 0}


def engineering_bundle(state_root: Path, *, repository: str = "", limit: int = 8,
                       now: datetime | None = None) -> dict[str, Any]:
    """Bounded, restart-safe, seven-repository evidence. Absolutely no side effects."""
    _validate(repository, limit)
    observed = (now or datetime.now(UTC)).astimezone(UTC)
    live = observability.snapshot(state_root, now=observed)
    history = event_history(state_root, repository=repository, limit=limit)
    learned = attempt_memory(state_root, repository=repository, limit=limit)
    frontier = frontier_evidence(state_root, repository=repository, limit=limit, now=observed)
    microtasks = microtask_checkpoint_evidence(
        state_root, repository=repository, limit=limit, now=observed,
    )
    product = live["lanes"]["product"]
    active = product if product.get("repository") == repository or not repository else {}
    if repository and not active:
        # Another active repository is not proof this repository has no work.
        lane_status = "UNVERIFIED_FOR_REPOSITORY"
    else:
        lane_status = live["status"]
    advice = live["recovery_advice"]
    replay = (history["events"][0] if history["events"] else None)
    return {
        "schema_version": SCHEMA_VERSION,
        "observed_at_utc": observed.isoformat(),
        "repository_scope": repository or "ALL_REGISTERED",
        "registered_repositories": list(observability.REPOSITORIES),
        "authority": "READ_ONLY_ADVISORY_NO_OWNER_GRANTS_NO_WORKER_LAUNCH",
        "durable_continuation": {
            "source": history["source"], "source_status": history["status"],
            "last_observed_checkpoint": replay,
            "resume_authorized": False,
            "requires_fresh_owner_lease_and_exact_head": True,
            "historical_only": True,
            "local_microtask_progress": microtasks,
        },
        "mcp_handoff": {
            "transport": "LOCAL_STDIO", "tool": "get_engineering_evidence",
            "advisory_only": True, "can_acknowledge": False,
            "can_dispatch": False,
        },
        "frontier_and_recovery": {
            "live_status": lane_status,
            "reason": live["reason"] if lane_status != "UNVERIFIED_FOR_REPOSITORY" else "NO_LIVE_REPO_EVIDENCE",
            "advice": advice if lane_status != "UNVERIFIED_FOR_REPOSITORY" else None,
            "eligibility": live["frontier"],
            "observed_candidate_ids": frontier,
            "execution_authorized_by_bundle": False,
        },
        "learning": {**learned, "effectiveness": learning_feedback.snapshot(
            Path(state_root) / "iron-god-products", repository=repository,
        )},
        "evidence_graph": _graph(history["events"], learned["attempts"]),
        "delivery": {
            "runtime_projection": live["delivery"],
            "daily_verified_update": daily_release_update_evidence(
                state_root, now=observed,
            ),
            "local_publication_receipts": publication_evidence(
                state_root, repository=repository, limit=limit,
            ),
            "ci": "UNVERIFIED", "merge": "UNVERIFIED",
            "native_product_acceptance": "UNVERIFIED",
            "git_and_product_reality_must_be_verified_independently": True,
        },
        "resource_admission": {
            "current": live["resources"],
            "calibrated_local_router": measured_local_admission(state_root, now=observed),
            "cloud_spend_authorized": False,
            "worker_launch_authorized": False,
            "local_model_routing_remains_separate_authoritative_gate": True,
        },
        "history": history,
        "live_evidence": live["evidence"],
        "supervisor": live["supervisor"],
    }
