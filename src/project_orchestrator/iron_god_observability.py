"""Read-only, fail-closed Iron God diagnostics and append-only development events.

Canonical owner grants, worker leases, roadmaps, and product acceptance remain
outside this module. SQLite is a projection of observed state, never authority.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
FRESH_SECONDS = 900
MAX_JSON_BYTES = 3_000_000
EVENTS_LIMIT = 30
REPOSITORIES = (
    "jweter/project-orchestrator",
    "jweter/Project-Everward",
    "jweter/rocksmith-cdlc-generator",
    "jweter/VoiceStudio",
    "jweter/knowledge-engine-core",
    "jweter/knowledge-engine-ai",
    "jweter/knowledge-engine-web",
)
_SHA = re.compile(r"^[0-9a-f]{40}$")
_TOKEN = re.compile(r"^[a-zA-Z0-9._:/#@+-]{1,120}$")
_INPUTS = {
    "heartbeat": ("supervisor-generation-heartbeat.json", "heartbeat_at_utc"),
    "authority": ("supervisor-execution-authority.json", "observed_at_utc"),
    "supervisor": ("supervisor.json", "updated_at_utc"),
    "canon": ("iron-god-canon/lane-status.json", "observed_at_utc"),
    "product": ("iron-god-products/lane-status.json", "observed_at_utc"),
    "pool": ("parallel-worker-pool.json", "recorded_at_utc"),
    "memory": ("worker-memory-admission.json", "observed_at_utc"),
    "continuation": ("continuation-current-burst.json", "updated_at_utc"),
    "delivery": ("verified-delivery-throughput.json", None),
    "eligibility": ("iron-god-products/eligibility.json", "observed_at_utc"),
    "refill": ("iron-god-products/roadmap-refill.json", None),
}
_SOURCE_KEYS = {
    "heartbeat": ("status", "process_instance_id", "actor_revision"),
    "authority": ("permitted", "supervisor_instance_id", "candidate_products"),
    "supervisor": ("status", "builder_status", "authorized_products", "activation_reason"),
    "canon": ("status", "repository", "packet_id"),
    "product": ("status", "repository", "packet_id", "worker_status", "head_sha"),
    "pool": (),
    "memory": ("status", "available_mib", "new_worker_budget"),
    "continuation": ("last_builder_status", "last_sequence", "process_instance_id"),
    "delivery": ("status", "verified_deliveries", "product_reality_verified"),
    "eligibility": (),
    "refill": (),
}


def _safe_token(value: Any) -> str | None:
    return value if isinstance(value, str) and _TOKEN.fullmatch(value) else None


def _safe_sha(value: Any) -> str | None:
    return value if isinstance(value, str) and _SHA.fullmatch(value) else None


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo else None
    except ValueError:
        return None


def _source(state_root: Path, name: str, now: datetime) -> tuple[dict[str, Any], dict[str, Any]]:
    rel, time_key = _INPUTS[name]
    filename = state_root / rel
    unavailable = {"status": "UNAVAILABLE", "freshness": "UNVERIFIED", "path": rel}
    try:
        if not filename.is_file() or filename.stat().st_size > MAX_JSON_BYTES:
            return {}, unavailable
        raw = json.loads(filename.read_text(encoding="utf-8-sig"))
        if not isinstance(raw, dict):
            return {}, unavailable
        # The delivery projection has no in-payload timestamp; mtime is only
        # a filesystem observation, never equivalent to a signed event.
        stamp = _time(raw.get(time_key)) if time_key else datetime.fromtimestamp(filename.stat().st_mtime, UTC)
        age = (now - stamp).total_seconds() if stamp else None
        freshness = ("FRESH" if age is not None and -120 <= age <= FRESH_SECONDS
                     else "STALE" if age is not None else "UNVERIFIED")
        data: dict[str, Any] = {}
        for key in _SOURCE_KEYS[name]:
            value = raw.get(key)
            if key in {"candidate_products", "authorized_products"}:
                data[key] = [x for x in value if isinstance(x, str) and len(x) <= 64][:8] if isinstance(value, list) else []
            elif key in {"permitted"}:
                data[key] = value if type(value) is bool else None
            elif key in {"available_mib", "new_worker_budget", "last_sequence",
                         "verified_deliveries", "product_reality_verified"}:
                data[key] = value if type(value) is int and 0 <= value < 1_000_000_000 else None
            elif key in {"actor_revision", "head_sha"}:
                data[key] = _safe_sha(value)
            else:
                data[key] = _safe_token(value)
        if name == "pool":
            pool = raw.get("pool")
            data["running_total"] = (pool.get("running_total") if isinstance(pool, dict)
                                     and type(pool.get("running_total")) is int
                                     and pool["running_total"] >= 0 else None)
            leases = raw.get("runtime_leases")
            existing = leases.get("leases") if isinstance(leases, dict) else None
            data["active_leases"] = len(existing) if isinstance(existing, list) else None
        if name == "product":
            data["worker_summary"] = ("CANONICAL_PREFLIGHT_FAILED"
                                      if raw.get("worker_summary") == "canonical preflight failed"
                                      else None)
        if name == "eligibility":
            items = raw.get("items")
            reasons: dict[str, int] = {}
            per_repository: dict[str, dict[str, int]] = {}
            ready = 0
            if isinstance(items, list):
                for item in items[:1000]:
                    if not isinstance(item, dict):
                        continue
                    eligible = item.get("eligible") is True
                    ready += int(eligible)
                    reason = _safe_token(item.get("reason")) or "UNCLASSIFIED"
                    reasons[reason] = reasons.get(reason, 0) + 1
                    repo = item.get("repository")
                    if repo in REPOSITORIES:
                        entry = per_repository.setdefault(repo, {"total": 0, "eligible": 0})
                        entry["total"] += 1
                        entry["eligible"] += int(eligible)
            data["total"] = sum(reasons.values())
            data["eligible"] = ready
            data["reasons"] = dict(sorted(reasons.items(), key=lambda v: (-v[1], v[0]))[:12])
            data["per_repository"] = per_repository
        if name == "refill":
            items = raw.get("items")
            if isinstance(items, list):
                data["approved_roadmap_items"] = sum(
                    1 for item in items[:1000] if isinstance(item, dict) and item.get("status") == "approved"
                )
                data["roadmap_items"] = min(len(items), 1000)
        return data, {"status": "OBSERVED", "freshness": freshness, "path": rel,
                      "observed_at_utc": stamp.isoformat() if stamp else None,
                      "age_seconds": int(max(0, age)) if age is not None else None,
                      "time_basis": "PAYLOAD" if time_key else "FILE_MTIME"}
    except (OSError, ValueError, TypeError):
        return {}, unavailable


def snapshot(state_root: Path, *, now: datetime | None = None) -> dict[str, Any]:
    """Join bounded current projections, exposing uncertainty without any grants."""
    now = (now or datetime.now(UTC)).astimezone(UTC)
    state_root = Path(state_root)
    observed: dict[str, dict[str, Any]] = {}
    evidence: dict[str, dict[str, Any]] = {}
    for name in _INPUTS:
        observed[name], evidence[name] = _source(state_root, name, now)
    hb, auth, supervisor = observed["heartbeat"], observed["authority"], observed["supervisor"]
    canon, product, pool = observed["canon"], observed["product"], observed["pool"]
    memory, continuation, delivery = observed["memory"], observed["continuation"], observed["delivery"]
    eligibility, refill = observed["eligibility"], observed["refill"]
    core_fresh = all(evidence[name]["freshness"] == "FRESH" for name in ("heartbeat", "authority", "pool"))
    same_generation = (bool(hb.get("process_instance_id")) and
                       hb["process_instance_id"] == auth.get("supervisor_instance_id"))
    current = core_fresh and same_generation and hb.get("status") == "RUNNING_CURRENT"
    workers = pool.get("running_total")
    if not current:
        reason = "RUNTIME_EVIDENCE_STALE_OR_GENERATION_MISMATCH"
        status = "UNVERIFIED"
        fix = "Verify current supervisor generation and authoritative owner control"
    elif evidence["product"]["freshness"] == "FRESH" and product.get("status") in {
        "DISPATCHED", "IMPLEMENTING", "REPAIRING", "PUBLISH_PENDING", "AWAITING_CI",
    }:
        # Commissioning owns its own product worker/lease; the legacy pool is
        # deliberately empty. Never call it idle because the legacy pool is 0.
        reason = "PRODUCT_LANE_" + str(product["status"])
        status = "ACTIVE_UNVERIFIED_PROGRESS"
        fix = "Correlate the Product-lane packet with worker, CI and delivery evidence before claiming progress"
    elif evidence["canon"]["freshness"] == "FRESH" and canon.get("status") in {
        "DISPATCHED", "IMPLEMENTING", "REPAIRING", "AWAITING_CI",
    }:
        reason = "CANON_LANE_" + str(canon["status"])
        status = "ACTIVE_UNVERIFIED_PROGRESS"
        fix = "Correlate the Canon-lane packet with worker and release evidence"
    elif evidence["product"]["freshness"] == "FRESH" and product.get("status") in {
        "WAITING_FOR_MEMORY", "WAITING_FOR_CALIBRATION", "WAITING_FOR_RESOURCES",
    }:
        # The commissioning Product lane has its own admission path. Preserve
        # its fresh resource/capability blocker before the idle Canon fallback.
        waiting = str(product["status"])
        reason = "PRODUCT_LANE_" + waiting
        status = "BLOCKED"
        fix = {
            "WAITING_FOR_MEMORY": "Inspect model-route memory requirements and wait for safe admission; do not lower memory floors",
            "WAITING_FOR_CALIBRATION": "Inspect measured local builder capability and approved routes; do not substitute unapproved cloud models",
            "WAITING_FOR_RESOURCES": "Inspect Product-lane admission and active leases; retry only when existing resource gates permit",
        }[waiting]
    elif evidence["product"]["freshness"] == "FRESH" and product.get("status") in {
        "WAITING_FOR_RUNTIME_LEASE", "WORKER_LAUNCH_UNCONFIRMED",
        "ORPHANED_PRODUCT_WORKER_OWNERSHIP", "ACTIVE_WORKER_STATE_UNVERIFIED",
    }:
        # A still-owned runtime lease or uncertain worker identity is NOT a
        # shortage of approved work. Neither permits replaying or stealing it.
        blocked = str(product["status"])
        reason, status = "PRODUCT_LANE_" + blocked, "BLOCKED"
        fix = {
            "WAITING_FOR_RUNTIME_LEASE": (
                "Check current lease holder, authenticated worker identity and expiry "
                "using the existing runtime lease pool; never force reclaim or dispatch"
            ),
            "WORKER_LAUNCH_UNCONFIRMED": (
                "Reconcile exact worker PID, packet, worktree and lease; preserve "
                "possible active work and refuse duplicate dispatch"
            ),
            "ORPHANED_PRODUCT_WORKER_OWNERSHIP": (
                "Reconcile ledger packet ownership with lease, process and worktree "
                "before any new dispatch"
            ),
            "ACTIVE_WORKER_STATE_UNVERIFIED": (
                "Inspect corrupt or missing worker state and reconcile live ownership "
                "before any new dispatch"
            ),
        }[blocked]
    elif workers is not None and workers > 0:
        reason = "LEGACY_WORKERS_RUNNING_REPORTED"
        status = "ACTIVE_UNVERIFIED_PROGRESS"
        fix = "Correlate worker execution phase and product acceptance evidence"
    elif evidence["product"]["freshness"] == "FRESH" and product.get("status") in {
        "BLOCKED", "REPAIR_PENDING", "FAILED", "ACTIVE_WORKER_STATE_UNVERIFIED"
    }:
        reason = ("PRODUCT_CANONICAL_PREFLIGHT_FAILED"
                  if product.get("worker_summary") == "CANONICAL_PREFLIGHT_FAILED"
                  else "PRODUCT_LANE_" + str(product.get("status")))
        status = "BLOCKED"
        fix = "Inspect the product lane's existing preflight/test evidence; repair under existing approved work and rerun exact-head gates"
    elif evidence["canon"]["freshness"] == "FRESH" and canon.get("status") == "IDLE_NO_READY_SLICE":
        reason = "CANON_NO_READY_SLICE"
        if (evidence["eligibility"]["freshness"] == "FRESH"
                and eligibility.get("eligible") == 0):
            reason = "NO_ELIGIBLE_PRODUCT_SLICES"
            if refill.get("approved_roadmap_items", 0) > 0:
                reason = "APPROVED_ROADMAP_WITH_NO_ELIGIBLE_SLICES"
        status = "IDLE"
        fix = "Inspect current per-repository eligibility exclusions and roadmap refill; select only independently authorized alternatives"
    elif supervisor.get("builder_status") == "NO_READY_EXECUTION_SLICE":
        reason = "LEGACY_SUPERVISOR_NO_READY_SLICE"
        status = "IDLE"
        fix = "Inspect Canon and Product lanes; commissioning suppresses legacy product dispatch by design"
    else:
        reason = "CURRENT_LANE_OUTCOME_NOT_PROVEN"
        status = "UNVERIFIED"
        fix = "Inspect bounded recent lane events and worker lifecycle evidence"
    # A permitted supervisor is not proof that any slice has an execution grant.
    if current and auth.get("permitted") is False:
        reason, status = "OWNER_EXECUTION_NOT_PERMITTED", "BLOCKED"
        fix = "Preserve owner control; owner must explicitly authorize any restart"
    # Memory admission restricts new work, but never supplies authority.
    resource = memory.get("status") if evidence["memory"]["freshness"] == "FRESH" else None
    if status == "UNVERIFIED":
        recovery_action = "REVERIFY_CURRENT_GENERATION"
    elif reason == "OWNER_EXECUTION_NOT_PERMITTED":
        recovery_action = "OWNER_CONTROL_MAINTENANCE_ONLY"
    elif status == "ACTIVE_UNVERIFIED_PROGRESS":
        recovery_action = "OBSERVE_CURRENT_PACKET_NO_DUPLICATE_DISPATCH"
    elif reason == "PRODUCT_LANE_WAITING_FOR_MEMORY":
        recovery_action = "WAIT_FOR_SAFE_MEMORY_ADMISSION"
    elif reason == "PRODUCT_LANE_WAITING_FOR_CALIBRATION":
        recovery_action = "REVIEW_MEASURED_LOCAL_MODEL_CAPABILITY"
    elif reason == "PRODUCT_LANE_WAITING_FOR_RESOURCES":
        recovery_action = "RECHECK_PRODUCT_RESOURCE_ADMISSION"
    elif reason == "PRODUCT_LANE_WAITING_FOR_RUNTIME_LEASE":
        recovery_action = "RECONCILE_EXISTING_RUNTIME_LEASE_NO_FORCE_RELEASE"
    elif reason in {
        "PRODUCT_LANE_WORKER_LAUNCH_UNCONFIRMED",
        "PRODUCT_LANE_ORPHANED_PRODUCT_WORKER_OWNERSHIP",
        "PRODUCT_LANE_ACTIVE_WORKER_STATE_UNVERIFIED",
    }:
        recovery_action = "RECONCILE_WORKER_PACKET_AND_LEASE_NO_DUPLICATE_DISPATCH"
    elif resource == "CRITICAL_MEMORY_NO_NEW_WORK":
        recovery_action = "WAIT_FOR_SAFE_MEMORY_ADMISSION"
    elif reason == "PRODUCT_CANONICAL_PREFLIGHT_FAILED":
        recovery_action = "REPAIR_EXISTING_PRODUCT_PREFLIGHT_OR_ROTATE_SAFE_LANE"
    elif reason in {"NO_ELIGIBLE_PRODUCT_SLICES", "APPROVED_ROADMAP_WITH_NO_ELIGIBLE_SLICES"}:
        recovery_action = "REFILL_AND_REEVALUATE_WITH_EXISTING_OWNER_GRANTS"
    else:
        recovery_action = "INSPECT_CANON_AND_PRODUCT_FRONTIER"
    return {
        "schema_version": SCHEMA_VERSION, "observed_at_utc": now.isoformat(),
        "status": status, "reason": reason, "remediation": fix,
        "classification": "OBSERVED_DIAGNOSTIC_NOT_AN_EXECUTION_DECISION",
        "recovery_advice": {"action": recovery_action, "automatic_execution": False,
                            "owner_grant_created": False,
                            "acceptance": "EXACT_HEAD_NATIVE_GATES_AND_PRODUCT_EVIDENCE_REQUIRED"},
        "supervisor": {"instance_id": hb.get("process_instance_id"),
                       "release_sha": hb.get("actor_revision"),
                       "status": hb.get("status"),
                       "execution_permitted": auth.get("permitted"),
                       "legacy_authorized_products": supervisor.get("authorized_products")},
        "lanes": {"canon": canon, "product": product},
        "workers": {"running": workers, "leases": pool.get("active_leases")},
        "resources": {"memory_status": resource, "free_mib": memory.get("available_mib"),
                      "new_worker_budget": memory.get("new_worker_budget")},
        "delivery": delivery, "continuation": continuation,
        "frontier": {"eligibility": eligibility, "refill": refill,
                     "eligibility_fresh": evidence["eligibility"]["freshness"] == "FRESH",
                     "refill_fresh": evidence["refill"]["freshness"] == "FRESH"},
        "evidence": evidence, "repositories": list(REPOSITORIES),
        "authority": "READ_ONLY_NO_OWNER_GRANTS_NO_RECOVERY_EXECUTION",
    }


def _database(state_root: Path) -> Path:
    return Path(state_root) / "observability" / "development-events.sqlite"


def record_supervisor_pass(state_root: Path, *, now: datetime | None = None) -> bool:
    """Append one immutable observation per supervisor continuation sequence.

    Designed for the existing single-generation supervisor, not an independent
    scanner/worker. Returning False is never permission to retry work.
    """
    report = snapshot(state_root, now=now)
    continuation = report["continuation"]
    instance = report["supervisor"]["instance_id"]
    sequence = continuation.get("last_sequence")
    if (report["status"] == "UNVERIFIED" or not isinstance(instance, str)
            or type(sequence) is not int or sequence < 1
            or report["evidence"]["continuation"]["freshness"] != "FRESH"
            or continuation.get("process_instance_id") != instance):
        return False
    event_key = hashlib.sha256(f"{instance}:{sequence}".encode()).hexdigest()
    payload = {key: report[key] for key in ("status", "reason", "supervisor", "lanes",
                                            "workers", "resources", "delivery", "continuation", "frontier",
                                            "recovery_advice")}
    db_path = _database(state_root)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path, timeout=5) as db:
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        with db:
            db.execute("""CREATE TABLE IF NOT EXISTS development_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key TEXT NOT NULL UNIQUE,
                supervisor_instance TEXT NOT NULL,
                continuation_sequence INTEGER NOT NULL,
                observed_at_utc TEXT NOT NULL,
                status TEXT NOT NULL,
                reason TEXT NOT NULL,
                evidence_json TEXT NOT NULL
            )""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS development_events_no_update
                BEFORE UPDATE ON development_events BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_EVENT'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS development_events_no_delete
                BEFORE DELETE ON development_events BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_EVENT'); END""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_development_events_recent ON development_events(seq DESC)")
            before = db.total_changes
            db.execute("""INSERT OR IGNORE INTO development_events
                (event_key,supervisor_instance,continuation_sequence,observed_at_utc,status,reason,evidence_json)
                VALUES (?,?,?,?,?,?,?)""",
                (event_key, instance, sequence, report["observed_at_utc"], report["status"],
                 report["reason"], json.dumps(payload, sort_keys=True)))
            return db.total_changes > before


def recent_events(state_root: Path, *, limit: int = 10) -> dict[str, Any]:
    """Read the separate ledger without creating a DB or modifying live state."""
    if type(limit) is not int or not 1 <= limit <= EVENTS_LIMIT:
        raise ValueError("INVALID_EVENT_LIMIT")
    path = _database(state_root)
    if not path.is_file():
        return {"status": "UNAVAILABLE", "events": []}
    try:
        with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=3) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("""SELECT seq,supervisor_instance,continuation_sequence,
                observed_at_utc,status,reason FROM development_events
                ORDER BY seq DESC LIMIT ?""", (limit,)).fetchall()
        return {"status": "OK", "events": [dict(row) for row in rows]}
    except (sqlite3.Error, OSError, ValueError):
        return {"status": "UNVERIFIED", "events": []}


def diagnostics(state_root: Path, *, now: datetime | None = None,
                history_limit: int = 8) -> dict[str, Any]:
    return {**snapshot(state_root, now=now), "recent_events": recent_events(state_root, limit=history_limit)}
