"""Bounded local-first Iron God coding worker; no GitHub, schedules or restarts."""
from __future__ import annotations

import ctypes
import hashlib
import json
import msvcrt
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))
ENGINE = BASE.parent
FLOW = ENGINE / "flow-state"
STATUS = BASE / "local-worker-status.json"
CONTROLLER_STATE = BASE / "controller-handoff-state.json"
TERMINAL_SLICE_STATES = {"VERIFIED", "ACCEPTED"}
RUNNABLE_SLICE_STATES = {"ELIGIBLE", "REPAIR_QUEUED"}
HANDOFF_DIR = BASE / "handoffs"
SLICE_LEASE_DB = BASE / "controller-slice-leases.sqlite"


class StageFailure(RuntimeError):
    """A bounded child command failed with enough evidence for repair routing."""

    def __init__(self, *, stage: str, command: list[str], log_path: Path, reason: str):
        super().__init__(reason)
        self.stage = stage
        self.command = tuple(command)
        self.log_path = Path(log_path)
        self.reason = reason


def sanitize_failure_log(text: str, *, limit: int = 16000) -> str:
    """Bound and redact common credential-shaped material before persistence."""
    if not isinstance(text, str):
        text = str(text)
    text = re.sub(r"gh[pousr]_[A-Za-z0-9_]{10,}", "<redacted-github-token>", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{10,}\b", "<redacted-api-key>", text)
    text = re.sub(r"\bAKIA[0-9A-Z]{16}\b", "<redacted-access-key>", text)
    text = re.sub(
        r"(?i)\b(Bearer)\s+[A-Za-z0-9._~+/-]{8,}",
        r"\1 <redacted>", text,
    )
    text = re.sub(
        r"(?i)(--(?:token|password|secret|api[-_]?key)\s+)(?:\"[^\"]*\"|'[^']*'|\S+)",
        r"\1<redacted>", text,
    )
    text = re.sub(
        r"(?i)\b(token|password|secret|api[_-]?key)\s*[:=]\s*[^\s]+",
        lambda match: match.group(1) + "=<redacted>",
        text,
    )
    text = "".join(ch for ch in text if ch in "\n\r\t" or ord(ch) >= 32)
    return text[-limit:]


def stage_failure_evidence(exc: StageFailure) -> dict:
    try:
        raw = exc.log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        raw = exc.reason
    log = sanitize_failure_log(raw)
    # Key retries by the stable terminal diagnostic, not timestamps or
    # append-only runner output; otherwise identical failures evade quarantine.
    lines = [line.strip() for line in log.splitlines() if line.strip()]
    diagnostic = next((line for line in reversed(lines) if re.search(
        r"(?i)\b(?:AssertionError|[A-Za-z]+Error|[A-Za-z]+Exception):\s*\S", line
    )), None)
    if diagnostic is None:
        diagnostic = next((line for line in reversed(lines) if re.search(
            r"(?i)\b(?:failed|error)\b", line
        )), exc.reason)
    diagnostic = re.sub(r"\b\d+(?:\.\d+)?s\b", "<duration>", diagnostic)
    # Repair slice IDs change on every retry (R01, R02, ...). Without
    # normalization, identical failures never share a quarantine signature.
    def stable_failure_identity(value: str) -> str:
        return re.sub(r"(?i)\b(IG-\d+)-R\d+(?=[-:]|\b)", r"\1", value)

    signature_material = (
        stable_failure_identity(exc.stage) + "\0"
        + stable_failure_identity(exc.reason) + "\0"
        + stable_failure_identity(diagnostic)
    ).encode("utf-8", errors="replace")
    return {
        "failure_signature": hashlib.sha256(signature_material).hexdigest(),
        "reproduce": sanitize_failure_log(
            subprocess.list2cmdline(list(exc.command)), limit=4000
        ),
        "assertion_trace": log[-8000:] or exc.reason,
        "sanitized_log": log or exc.reason,
        "environment_fingerprint": (
            f"python={sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro};"
            f"os={os.name};platform={sys.platform}"
        ),
        "suspected_root_cause": "unverified; bounded command failed at stage " + exc.stage,
    }


def gate() -> bool:
    try:
        owner = json.loads((FLOW / "human-mode.json").read_text("utf-8-sig"))
        policy = json.loads((FLOW / "iron-god-exclusive.json").read_text("utf-8-sig"))
        return (type(owner.get("enabled")) is bool and owner["enabled"] is False
                and policy.get("enabled") is True and policy.get("target_product") == "iron-god")
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def controller_payload() -> dict:
    """Load and validate the durable handoff projection; never infer authority."""
    try:
        payload = json.loads(CONTROLLER_STATE.read_text("utf-8-sig"))
        if payload.get("controller") != "iron-god-handoff-v1":
            raise ValueError("unexpected controller identity")
        if payload.get("mode") not in {"shadow_state_only", "candidate_mode"}:
            raise ValueError("unsupported controller mode")
        rows = payload.get("slices")
        if not isinstance(rows, list):
            raise TypeError("slices must be a list")
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                raise TypeError("slice row must be an object")
            ident = row.get("id")
            state = row.get("state")
            if not isinstance(ident, str) or not ident or ident in seen:
                raise ValueError("slice identity invalid")
            if not isinstance(state, str) or not state:
                raise ValueError("slice state invalid")
            seen.add(ident)
        return payload
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("CONTROLLER_STATE_UNVERIFIED") from exc


def reconciled_terminal_slices() -> set[str]:
    """Read-only shadow reconciliation against exact Git HEAD and pinned receipts.

    The JSON projection alone is not verification authority. Never rebuild a
    disputed terminal slice: stop for independent reconciliation instead.
    This does not advance accepted refs or confer production authority.
    """
    verified: set[str] = set()
    for row in controller_payload()["slices"]:
        if row["state"] not in TERMINAL_SLICE_STATES:
            continue
        ident = row["id"]
        if row["state"] != "VERIFIED":
            raise RuntimeError("ACCEPTED_SLICE_REQUIRES_FENCED_RECONCILER:" + ident)
        sha = row.get("head_sha")
        name = row.get("worktree_name")
        branch = row.get("branch")
        digest = row.get("receipt_sha256")
        if (not isinstance(sha, str) or len(sha) != 40
                or any(ch not in "0123456789abcdef" for ch in sha)
                or not isinstance(name, str) or not name
                or Path(name).name != name
                or not isinstance(branch, str) or not branch.startswith("iron-god/")
                or not isinstance(digest, str) or len(digest) != 64
                or any(ch not in "0123456789abcdef" for ch in digest)):
            raise RuntimeError("TERMINAL_IDENTITY_UNVERIFIED:" + ident)
        work = (BASE / name).resolve()
        if work.parent != BASE.resolve() or not work.is_dir():
            raise RuntimeError("TERMINAL_WORKTREE_UNVERIFIED:" + ident)
        try:
            raw = (work / "preflight-evidence.json").read_bytes()
            receipt = json.loads(raw)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("TERMINAL_RECEIPT_UNREADABLE:" + ident) from exc
        results = receipt.get("results") if isinstance(receipt, dict) else None
        if (hashlib.sha256(raw).hexdigest() != digest
                or not isinstance(receipt, dict)
                or receipt.get("head_sha") != sha
                or receipt.get("mode") != "FULL"
                or receipt.get("status") != "GREEN"
                or not isinstance(results, list) or len(results) != 26
                or any(not isinstance(item, dict)
                       or not isinstance(item.get("name"), str)
                       or not item["name"].strip()
                       or item.get("returncode") != 0
                       or item.get("timed_out") is not False
                       or item.get("interrupted") is not False for item in results)
                or len({item["name"] for item in results}) != 26):
            raise RuntimeError("TERMINAL_RECEIPT_UNVERIFIED:" + ident)
        if (git(work, "rev-parse", "HEAD").stdout.strip() != sha
                or git(work, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() != branch
                or Path(git(work, "rev-parse", "--show-toplevel").stdout.strip()).resolve() != work):
            raise RuntimeError("TERMINAL_GIT_IDENTITY_MISMATCH:" + ident)
        verified.add(ident)
    return verified


def runnable_handoffs(repair_db_path: Path | None = None) -> list[dict]:
    """Resolve runnable SliceSpecs only from durable controller state + handoff packets."""
    payload = controller_payload()
    resolved: list[dict] = []
    for row in payload["slices"]:
        if row["state"] not in RUNNABLE_SLICE_STATES:
            continue
        ident = row["id"]
        packet_path = HANDOFF_DIR / (ident + ".json")
        try:
            packet = json.loads(packet_path.read_text("utf-8-sig"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("HANDOFF_UNVERIFIED:" + ident) from exc
        if packet.get("schema_version") != 1 or packet.get("slice_id") != ident:
            raise RuntimeError("HANDOFF_IDENTITY_MISMATCH:" + ident)
        if packet.get("state") != row["state"]:
            raise RuntimeError("HANDOFF_STATE_MISMATCH:" + ident)
        objective = packet.get("objective")
        branch = packet.get("branch")
        worktree_name = packet.get("worktree_name")
        base_sha = packet.get("exact_base_sha")
        allowed = packet.get("allowed_paths")
        forbidden = packet.get("forbidden", [])
        acceptance = packet.get("acceptance", [])
        if not isinstance(objective, str) or not objective.strip():
            raise RuntimeError("HANDOFF_OBJECTIVE_INVALID:" + ident)
        if not isinstance(branch, str) or not branch.startswith("iron-god/"):
            raise RuntimeError("HANDOFF_BRANCH_INVALID:" + ident)
        if (not isinstance(worktree_name, str) or not worktree_name
                or Path(worktree_name).name != worktree_name):
            raise RuntimeError("HANDOFF_WORKTREE_INVALID:" + ident)
        if (not isinstance(base_sha, str) or len(base_sha) != 40
                or any(ch not in "0123456789abcdef" for ch in base_sha)):
            raise RuntimeError("HANDOFF_BASE_SHA_INVALID:" + ident)
        if not isinstance(allowed, list) or not allowed:
            raise RuntimeError("HANDOFF_PATHS_INVALID:" + ident)
        if any(not isinstance(item, str) or Path(item).is_absolute() or ".." in Path(item).parts
               for item in allowed):
            raise RuntimeError("HANDOFF_PATHS_INVALID:" + ident)
        # Repository-aware source-root (2026-10-18): reuses the same
        # trusted, closed mapping already implemented and tested in
        # accepted_event_dispatch_coordinator.py, rather than duplicating
        # it -- an unlisted repository keeps the unchanged "src/" default.
        from accepted_event_dispatch_coordinator import _source_root_for_repository
        source_root = _source_root_for_repository(packet.get("repository"))
        sources = [item for item in allowed if item.startswith(source_root)]
        tests = [item for item in allowed if item.startswith("tests/")]
        if len(sources) != 1 or len(tests) != 1 or len(allowed) != 2:
            raise RuntimeError("HANDOFF_PATH_SHAPE_UNSUPPORTED:" + ident)
        if not isinstance(forbidden, list) or not all(isinstance(item, str) for item in forbidden):
            raise RuntimeError("HANDOFF_FORBIDDEN_INVALID:" + ident)
        if not isinstance(acceptance, list) or not all(isinstance(item, str) for item in acceptance):
            raise RuntimeError("HANDOFF_ACCEPTANCE_INVALID:" + ident)
        work = (BASE / worktree_name).resolve()
        if work.parent != BASE.resolve():
            raise RuntimeError("HANDOFF_WORKTREE_ESCAPES_ROOT:" + ident)
        # Reject symlink/junction escapes (additive; never loosens the
        # string-level ".."/absolute-path checks above), mirroring the
        # identical hardening already applied in
        # accepted_event_dispatch_coordinator.py's _require_exact_handoff_task.
        for item in allowed:
            if not (work / item).resolve().is_relative_to(work):
                raise RuntimeError("HANDOFF_PATH_ESCAPES_WORKTREE:" + ident)
        prompt = (
            "Objective: " + objective.strip()
            + " Allowed paths: " + ", ".join(allowed)
            + ". Forbidden: " + "; ".join(forbidden)
            + ". Acceptance: " + "; ".join(acceptance)
        )
        resolved.append({
            "id": ident, "source": sources[0], "tests": tests[0],
            "work": work, "branch": branch, "base_sha": base_sha, "prompt": prompt,
            "parent_id": None, "repair": False,
        })
    repair_db_path = SLICE_LEASE_DB if repair_db_path is None else repair_db_path
    blocked_parents = repair_blocked_parents(repair_db_path)
    outcome_blocked = pending_outcome_slices(repair_db_path)
    resolved = [
        task for task in resolved
        if task["id"] not in blocked_parents and task["id"] not in outcome_blocked
    ]
    return queued_repair_handoffs(repair_db_path) + resolved


def handoff_queue_diagnostics() -> list[dict]:
    """Explain why handoff packets cannot become runnable; NEVER schedule them.

    Only the durable ledger may confer eligibility. A packet on disk is not
    proof that a slice is authorized, pending, incomplete, or at the right SHA.
    In particular, never replay directly supervised or merged work.
    """
    rows = {row["id"]: row for row in controller_payload()["slices"]}
    if not HANDOFF_DIR.is_dir():
        return [{"slice_id": "*", "reasons": ["HANDOFF_DIRECTORY_MISSING"]}]
    diagnostics: list[dict] = []
    for packet_path in sorted(HANDOFF_DIR.glob("IG-*.json")):
        ident = packet_path.stem
        row = rows.get(ident)
        reasons: list[str] = []
        if row is None:
            reasons.append("NOT_REGISTERED_IN_CONTROLLER_LEDGER")
        elif row["state"] not in RUNNABLE_SLICE_STATES:
            reasons.append("NOT_RUNNABLE_IN_LEDGER:" + row["state"])
        try:
            packet = json.loads(packet_path.read_text("utf-8-sig"))
            if not isinstance(packet, dict) or packet.get("slice_id") != ident:
                reasons.append("PACKET_IDENTITY_UNVERIFIED")
            else:
                if row is not None and row["state"] in RUNNABLE_SLICE_STATES:
                    if packet.get("state") != row["state"]:
                        reasons.append("PACKET_STATE_DISAGREES_WITH_LEDGER")
                base_sha = packet.get("exact_base_sha")
                if (not isinstance(base_sha, str) or len(base_sha) != 40
                        or any(c not in "0123456789abcdef" for c in base_sha)):
                    reasons.append("EXACT_BASE_SHA_MISSING_OR_INVALID")
        except (OSError, ValueError, TypeError):
            reasons.append("PACKET_UNREADABLE_OR_MALFORMED")
        if reasons:
            diagnostics.append({"slice_id": ident, "reasons": reasons})
    for ident, row in sorted(rows.items()):
        if (row["state"] in RUNNABLE_SLICE_STATES
                and not (HANDOFF_DIR / (ident + ".json")).is_file()):
            diagnostics.append({
                "slice_id": ident,
                "reasons": ["RUNNABLE_LEDGER_ROW_MISSING_HANDOFF"],
            })
    return sorted(diagnostics, key=lambda item: item["slice_id"])


def _lease_connect(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path, timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=10000")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=FULL")
    return db


def init_slice_lease_db(path: Path = SLICE_LEASE_DB) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = _lease_connect(path)
    try:
        db.execute("BEGIN IMMEDIATE")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, 2, 3, 4):
            raise RuntimeError("SLICE_LEASE_SCHEMA_UNSUPPORTED")
        db.execute("""
            CREATE TABLE IF NOT EXISTS slice_generations(
                slice_id TEXT PRIMARY KEY,
                generation INTEGER NOT NULL CHECK(generation >= 0)
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS slice_leases(
                slice_id TEXT PRIMARY KEY,
                attempt_id TEXT NOT NULL,
                generation INTEGER NOT NULL,
                owner_pid INTEGER NOT NULL,
                base_sha TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('LEASED','BUILDING','VERIFYING')),
                lease_until REAL NOT NULL,
                changed_at REAL NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS slice_lease_events(
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                slice_id TEXT NOT NULL,
                attempt_id TEXT NOT NULL,
                generation INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                detail TEXT NOT NULL,
                occurred_at REAL NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS failure_signatures(
                parent_slice_id TEXT NOT NULL,
                failure_signature TEXT NOT NULL,
                attempts INTEGER NOT NULL CHECK(attempts >= 1),
                PRIMARY KEY(parent_slice_id, failure_signature)
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS repair_slices(
                repair_id TEXT PRIMARY KEY,
                parent_slice_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                failure_signature TEXT NOT NULL,
                reproduce TEXT NOT NULL,
                assertion_trace TEXT NOT NULL,
                sanitized_log TEXT NOT NULL,
                environment_fingerprint TEXT NOT NULL,
                candidate_head TEXT NOT NULL,
                changed_paths_json TEXT NOT NULL,
                suspected_root_cause TEXT NOT NULL,
                required_tests_json TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('REPAIR_QUEUED','VERIFIED','FAILED','QUARANTINED')),
                created_at REAL NOT NULL,
                changed_at REAL NOT NULL,
                UNIQUE(parent_slice_id, sequence)
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS slice_outcomes(
                slice_id TEXT PRIMARY KEY,
                attempt_id TEXT NOT NULL,
                generation INTEGER NOT NULL,
                base_sha TEXT NOT NULL,
                candidate_sha TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('VERIFIED_CANDIDATE','FAILED_ROUTED')),
                evidence_json TEXT NOT NULL,
                changed_at REAL NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS outcome_reconciliations(
                slice_id TEXT PRIMARY KEY,
                candidate_sha TEXT NOT NULL,
                receipt_sha256 TEXT NOT NULL,
                reconciled_at REAL NOT NULL
            )
        """)
        db.execute("PRAGMA user_version = 4")
        db.execute("COMMIT")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("SLICE_LEASE_DB_INTEGRITY_FAILED")
    except BaseException:
        try:
            db.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        db.close()


def _validate_slice_identity(slice_id: str, base_sha: str) -> None:
    if not isinstance(slice_id, str) or not slice_id or len(slice_id) > 128:
        raise RuntimeError("SLICE_ID_INVALID")
    if (not isinstance(base_sha, str) or len(base_sha) != 40
            or any(ch not in "0123456789abcdef" for ch in base_sha)):
        raise RuntimeError("SLICE_BASE_SHA_INVALID")


def claim_slice_lease(
    *, slice_id: str, base_sha: str, owner_pid: int, ttl_seconds: float,
    path: Path = SLICE_LEASE_DB, now: float | None = None,
) -> dict:
    _validate_slice_identity(slice_id, base_sha)
    if owner_pid <= 0 or ttl_seconds <= 0:
        raise RuntimeError("SLICE_LEASE_ARGUMENT_INVALID")
    now = time.time() if now is None else now
    init_slice_lease_db(path)
    db = _lease_connect(path)
    attempt_id = uuid.uuid4().hex
    try:
        db.execute("BEGIN IMMEDIATE")
        previous = db.execute(
            "SELECT * FROM slice_leases WHERE slice_id=?", (slice_id,)
        ).fetchone()
        if previous is not None and float(previous["lease_until"]) > now:
            raise RuntimeError("SLICE_LEASE_OCCUPIED")
        if previous is not None:
            db.execute(
                "INSERT INTO slice_lease_events VALUES(NULL,?,?,?,?,?,?)",
                (
                    slice_id, previous["attempt_id"], previous["generation"],
                    "STALE_LEASE_RECOVERED", previous["state"], now,
                ),
            )
            db.execute("DELETE FROM slice_leases WHERE slice_id=?", (slice_id,))
        row = db.execute(
            "SELECT generation FROM slice_generations WHERE slice_id=?", (slice_id,)
        ).fetchone()
        generation = (int(row["generation"]) if row is not None else 0) + 1
        db.execute(
            "INSERT INTO slice_generations(slice_id,generation) VALUES(?,?) "
            "ON CONFLICT(slice_id) DO UPDATE SET generation=excluded.generation",
            (slice_id, generation),
        )
        db.execute(
            "INSERT INTO slice_leases VALUES(?,?,?,?,?,?,?,?)",
            (
                slice_id, attempt_id, generation, owner_pid, base_sha,
                "LEASED", now + ttl_seconds, now,
            ),
        )
        db.execute(
            "INSERT INTO slice_lease_events VALUES(NULL,?,?,?,?,?,?)",
            (slice_id, attempt_id, generation, "SLICE_LEASED", base_sha, now),
        )
        db.execute("COMMIT")
        return {
            "slice_id": slice_id, "attempt_id": attempt_id, "generation": generation,
            "owner_pid": owner_pid, "base_sha": base_sha, "state": "LEASED",
            "lease_until": now + ttl_seconds,
        }
    except BaseException:
        try:
            db.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        db.close()


def _fenced_lease(
    db: sqlite3.Connection, *, slice_id: str, attempt_id: str, generation: int,
    now: float | None = None,
) -> sqlite3.Row:
    row = db.execute("SELECT * FROM slice_leases WHERE slice_id=?", (slice_id,)).fetchone()
    if row is None or row["attempt_id"] != attempt_id or int(row["generation"]) != generation:
        raise RuntimeError("SLICE_LEASE_FENCE_REJECTED")
    if float(row["lease_until"]) <= (time.time() if now is None else now):
        raise RuntimeError("SLICE_LEASE_EXPIRED")
    return row


def transition_slice_lease(
    *, slice_id: str, attempt_id: str, generation: int,
    expected_state: str, next_state: str, path: Path = SLICE_LEASE_DB,
    now: float | None = None,
) -> None:
    allowed = {"LEASED": {"BUILDING"}, "BUILDING": {"VERIFYING"}, "VERIFYING": set()}
    if next_state not in allowed.get(expected_state, set()):
        raise RuntimeError("SLICE_LEASE_TRANSITION_INVALID")
    now = time.time() if now is None else now
    init_slice_lease_db(path)
    db = _lease_connect(path)
    try:
        db.execute("BEGIN IMMEDIATE")
        row = _fenced_lease(
            db, slice_id=slice_id, attempt_id=attempt_id, generation=generation,
            now=now,
        )
        if row["state"] != expected_state:
            raise RuntimeError("SLICE_LEASE_STATE_CHANGED")
        db.execute(
            "UPDATE slice_leases SET state=?,changed_at=? WHERE slice_id=?",
            (next_state, now, slice_id),
        )
        db.execute(
            "INSERT INTO slice_lease_events VALUES(NULL,?,?,?,?,?,?)",
            (slice_id, attempt_id, generation, "SLICE_" + next_state, "", now),
        )
        db.execute("COMMIT")
    except BaseException:
        try:
            db.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        db.close()


def heartbeat_slice_lease(
    *, slice_id: str, attempt_id: str, generation: int, ttl_seconds: float,
    path: Path = SLICE_LEASE_DB, now: float | None = None,
) -> float:
    if ttl_seconds <= 0:
        raise RuntimeError("SLICE_LEASE_ARGUMENT_INVALID")
    now = time.time() if now is None else now
    init_slice_lease_db(path)
    db = _lease_connect(path)
    try:
        db.execute("BEGIN IMMEDIATE")
        _fenced_lease(db, slice_id=slice_id, attempt_id=attempt_id, generation=generation,
                      now=now)
        lease_until = now + ttl_seconds
        db.execute(
            "UPDATE slice_leases SET lease_until=?,changed_at=? WHERE slice_id=?",
            (lease_until, now, slice_id),
        )
        db.execute("COMMIT")
        return lease_until
    except BaseException:
        try:
            db.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        db.close()


def release_slice_lease(
    *, slice_id: str, attempt_id: str, generation: int, outcome: str,
    path: Path = SLICE_LEASE_DB, now: float | None = None,
) -> None:
    if not isinstance(outcome, str) or not outcome:
        raise RuntimeError("SLICE_LEASE_OUTCOME_INVALID")
    now = time.time() if now is None else now
    init_slice_lease_db(path)
    db = _lease_connect(path)
    try:
        db.execute("BEGIN IMMEDIATE")
        row = _fenced_lease(
            db, slice_id=slice_id, attempt_id=attempt_id, generation=generation,
            now=now,
        )
        db.execute("DELETE FROM slice_leases WHERE slice_id=?", (slice_id,))
        db.execute(
            "INSERT INTO slice_lease_events VALUES(NULL,?,?,?,?,?,?)",
            (
                slice_id, attempt_id, generation, "SLICE_RELEASED",
                row["state"] + ":" + outcome, now,
            ),
        )
        db.execute("COMMIT")
    except BaseException:
        try:
            db.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        db.close()


def record_slice_outcome(
    *, slice_id: str, attempt_id: str, generation: int, base_sha: str,
    candidate_sha: str, state: str, evidence: dict,
    path: Path = SLICE_LEASE_DB, now: float | None = None,
) -> None:
    _validate_slice_identity(slice_id, base_sha)
    _validate_slice_identity(slice_id, candidate_sha)
    if state not in {"VERIFIED_CANDIDATE", "FAILED_ROUTED"}:
        raise RuntimeError("SLICE_OUTCOME_INVALID")
    if not isinstance(evidence, dict):
        raise RuntimeError("SLICE_OUTCOME_EVIDENCE_INVALID")
    encoded = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
    if len(encoded) > 32000:
        raise RuntimeError("SLICE_OUTCOME_EVIDENCE_TOO_LARGE")
    now = time.time() if now is None else now
    init_slice_lease_db(path)
    db = _lease_connect(path)
    try:
        db.execute("BEGIN IMMEDIATE")
        _fenced_lease(
            db, slice_id=slice_id, attempt_id=attempt_id, generation=generation,
            now=now,
        )
        repair = db.execute(
            "SELECT state FROM repair_slices WHERE repair_id=?", (slice_id,)
        ).fetchone()
        if state == "VERIFIED_CANDIDATE" and repair is not None:
            if repair["state"] != "REPAIR_QUEUED":
                raise RuntimeError("REPAIR_STATE_CHANGED")
            db.execute(
                "UPDATE repair_slices SET state='VERIFIED',changed_at=? WHERE repair_id=?",
                (now, slice_id),
            )
        existing = db.execute(
            "SELECT attempt_id,generation,base_sha,candidate_sha,state,evidence_json "
            "FROM slice_outcomes WHERE slice_id=?", (slice_id,)
        ).fetchone()
        identity = (
            attempt_id, generation, base_sha, candidate_sha, state, encoded,
        )
        if existing is not None:
            if tuple(existing) != identity:
                raise RuntimeError("SLICE_OUTCOME_IDENTITY_CONFLICT")
            db.execute("COMMIT")
            return
        db.execute(
            "INSERT INTO slice_outcomes VALUES(?,?,?,?,?,?,?,?)",
            (
                slice_id, attempt_id, generation, base_sha, candidate_sha,
                state, encoded, now,
            ),
        )
        db.execute(
            "INSERT INTO slice_lease_events VALUES(NULL,?,?,?,?,?,?)",
            (
                slice_id, attempt_id, generation, "SLICE_OUTCOME_" + state,
                candidate_sha, now,
            ),
        )
        db.execute("COMMIT")
    except BaseException:
        try:
            db.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        db.close()


def pending_outcome_slices(path: Path = SLICE_LEASE_DB) -> set[str]:
    if not path.exists():
        return set()
    init_slice_lease_db(path)
    db = _lease_connect(path)
    try:
        rows = db.execute(
            "SELECT o.slice_id FROM slice_outcomes o "
            "LEFT JOIN outcome_reconciliations r ON r.slice_id=o.slice_id "
            "WHERE o.state='VERIFIED_CANDIDATE' AND r.slice_id IS NULL"
        ).fetchall()
        return {str(row["slice_id"]) for row in rows}
    finally:
        db.close()


def _outcome_identity(
    slice_id: str, path: Path, *, base_root: Path = BASE, handoff_dir: Path = HANDOFF_DIR,
) -> dict:
    packet_path = handoff_dir / (slice_id + ".json")
    if packet_path.exists():
        try:
            packet = json.loads(packet_path.read_text("utf-8-sig"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("OUTCOME_HANDOFF_UNVERIFIED:" + slice_id) from exc
        if packet.get("slice_id") != slice_id:
            raise RuntimeError("OUTCOME_HANDOFF_IDENTITY_MISMATCH:" + slice_id)
        worktree_name = packet.get("worktree_name")
        branch = packet.get("branch")
        parent_id = None
    else:
        init_slice_lease_db(path)
        db = _lease_connect(path)
        try:
            repair = db.execute(
                "SELECT parent_slice_id FROM repair_slices WHERE repair_id=?",
                (slice_id,),
            ).fetchone()
        finally:
            db.close()
        if repair is None:
            raise RuntimeError("OUTCOME_SLICE_IDENTITY_UNKNOWN:" + slice_id)
        parent_id = str(repair["parent_slice_id"])
        worktree_name = slice_id.lower() + "-worker-1"
        branch = "iron-god/repair/" + slice_id.lower()
    if (not isinstance(worktree_name, str) or not worktree_name
            or Path(worktree_name).name != worktree_name):
        raise RuntimeError("OUTCOME_WORKTREE_INVALID:" + slice_id)
    if not isinstance(branch, str) or not branch.startswith("iron-god/"):
        raise RuntimeError("OUTCOME_BRANCH_INVALID:" + slice_id)
    work = (base_root / worktree_name).resolve()
    if work.parent != base_root.resolve():
        raise RuntimeError("OUTCOME_WORKTREE_ESCAPES_ROOT:" + slice_id)
    return {
        "slice_id": slice_id,
        "parent_id": parent_id,
        "work": work,
        "branch": branch,
    }


def reconcile_verified_candidate_outcomes(
    path: Path = SLICE_LEASE_DB, *, base_root: Path = BASE,
    handoff_dir: Path = HANDOFF_DIR,
) -> list[dict]:
    """Re-prove durable VERIFIED_CANDIDATE outcomes against Git + native receipt.

    This is read-only reconciliation. It neither advances accepted refs nor
    mutates the controller projection.
    """
    if not path.exists():
        return []
    init_slice_lease_db(path)
    db = _lease_connect(path)
    try:
        rows = db.execute(
            "SELECT o.* FROM slice_outcomes o "
            "LEFT JOIN outcome_reconciliations r ON r.slice_id=o.slice_id "
            "WHERE o.state='VERIFIED_CANDIDATE' AND r.slice_id IS NULL "
            "ORDER BY o.changed_at,o.slice_id"
        ).fetchall()
    finally:
        db.close()
    verified: list[dict] = []
    for row in rows:
        slice_id = str(row["slice_id"])
        base_sha = str(row["base_sha"])
        candidate_sha = str(row["candidate_sha"])
        _validate_slice_identity(slice_id, base_sha)
        _validate_slice_identity(slice_id, candidate_sha)
        if candidate_sha == base_sha:
            raise RuntimeError("OUTCOME_NO_PROGRESS:" + slice_id)
        try:
            evidence = json.loads(row["evidence_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("OUTCOME_EVIDENCE_INVALID:" + slice_id) from exc
        receipt_digest = evidence.get("receipt_sha256")
        if (not isinstance(receipt_digest, str) or len(receipt_digest) != 64
                or any(ch not in "0123456789abcdef" for ch in receipt_digest)):
            raise RuntimeError("OUTCOME_RECEIPT_DIGEST_INVALID:" + slice_id)
        identity = _outcome_identity(
            slice_id, path, base_root=base_root, handoff_dir=handoff_dir
        )
        work = identity["work"]
        if not work.is_dir():
            raise RuntimeError("OUTCOME_WORKTREE_MISSING:" + slice_id)
        if (git(work, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
                != identity["branch"]):
            raise RuntimeError("OUTCOME_BRANCH_MISMATCH:" + slice_id)
        if git(work, "rev-parse", "HEAD").stdout.strip() != candidate_sha:
            raise RuntimeError("OUTCOME_HEAD_MISMATCH:" + slice_id)
        if Path(git(work, "rev-parse", "--show-toplevel").stdout.strip()).resolve() != work:
            raise RuntimeError("OUTCOME_WORKTREE_MISMATCH:" + slice_id)
        # Reconciliation must independently reject drift after verification.
        # A matching receipt and HEAD alone do not establish a clean worktree.
        check_paths(work, set())
        receipt_path = work / "preflight-evidence.json"
        if receipt_path.is_symlink():
            raise RuntimeError("OUTCOME_RECEIPT_SYMLINK:" + slice_id)
        try:
            raw = receipt_path.read_bytes()
            receipt = json.loads(raw)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("OUTCOME_RECEIPT_UNREADABLE:" + slice_id) from exc
        results = receipt.get("results") if isinstance(receipt, dict) else None
        if (hashlib.sha256(raw).hexdigest() != receipt_digest
                or not isinstance(receipt, dict)
                or receipt.get("head_sha") != candidate_sha
                or receipt.get("mode") != "FULL"
                or receipt.get("status") != "GREEN"
                or not isinstance(results, list) or len(results) != 26
                or any(not isinstance(item, dict)
                       or not isinstance(item.get("name"), str)
                       or not item["name"].strip()
                       or item.get("returncode") != 0
                       or item.get("timed_out") is not False
                       or item.get("interrupted") is not False for item in results)
                or len({item["name"] for item in results}) != 26):
            raise RuntimeError("OUTCOME_RECEIPT_UNVERIFIED:" + slice_id)
        verified.append({
            "slice_id": slice_id,
            "parent_id": identity["parent_id"],
            "base_sha": base_sha,
            "candidate_sha": candidate_sha,
            "receipt_sha256": receipt_digest,
            "checks": 26,
            "state": "VERIFIED_CANDIDATE_RECONCILED",
        })
    return verified


def acknowledge_outcome_reconciliation(
    *, slice_id: str, candidate_sha: str, receipt_sha256: str,
    path: Path = SLICE_LEASE_DB, now: float | None = None,
) -> bool:
    _validate_slice_identity(slice_id, candidate_sha)
    if (not isinstance(receipt_sha256, str) or len(receipt_sha256) != 64
            or any(ch not in "0123456789abcdef" for ch in receipt_sha256)):
        raise RuntimeError("RECONCILIATION_RECEIPT_INVALID")
    now = time.time() if now is None else now
    init_slice_lease_db(path)
    db = _lease_connect(path)
    try:
        db.execute("BEGIN IMMEDIATE")
        outcome = db.execute(
            "SELECT candidate_sha,evidence_json,state FROM slice_outcomes WHERE slice_id=?",
            (slice_id,),
        ).fetchone()
        if outcome is None or outcome["state"] != "VERIFIED_CANDIDATE":
            raise RuntimeError("RECONCILIATION_OUTCOME_MISSING")
        try:
            evidence = json.loads(outcome["evidence_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("RECONCILIATION_EVIDENCE_INVALID") from exc
        if (outcome["candidate_sha"] != candidate_sha
                or evidence.get("receipt_sha256") != receipt_sha256):
            raise RuntimeError("RECONCILIATION_IDENTITY_CONFLICT")
        previous = db.execute(
            "SELECT candidate_sha,receipt_sha256 FROM outcome_reconciliations "
            "WHERE slice_id=?", (slice_id,)
        ).fetchone()
        if previous is not None:
            if tuple(previous) != (candidate_sha, receipt_sha256):
                raise RuntimeError("RECONCILIATION_IDENTITY_CONFLICT")
            db.execute("COMMIT")
            return False
        db.execute(
            "INSERT INTO outcome_reconciliations VALUES(?,?,?,?)",
            (slice_id, candidate_sha, receipt_sha256, now),
        )
        db.execute("COMMIT")
        return True
    except BaseException:
        try:
            db.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        db.close()


def apply_shadow_candidate_reconciliation(
    path: Path = SLICE_LEASE_DB, *, controller_state_path: Path = CONTROLLER_STATE,
    controller_events_path: Path | None = None, base_root: Path = BASE,
    handoff_dir: Path = HANDOFF_DIR,
) -> list[dict]:
    """Advance only the local controller projection after exact evidence recheck.

    This never advances refs/iron-god/accepted, publication state, or Product
    Reality. The existing journal is read and checked for corruption/identity
    conflicts before any projection mutation, so a rejected journal leaves the
    projection file byte-for-byte unchanged. Projection write still precedes
    SQLite acknowledgement for crash replay.
    """
    proofs = reconcile_verified_candidate_outcomes(
        path, base_root=base_root, handoff_dir=handoff_dir
    )
    if not proofs:
        return []

    existing_events: dict[str, tuple[str, str]] = {}
    if controller_events_path is not None and controller_events_path.exists():
        try:
            for line in controller_events_path.read_text(encoding="utf-8").splitlines():
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError("event must be an object")
                if event.get("event") != "CANDIDATE_OUTCOME_RECONCILED":
                    continue
                ident = event.get("slice_id")
                pair = (event.get("candidate_sha"), event.get("receipt_sha256"))
                if (not isinstance(ident, str)
                        or not all(isinstance(value, str) for value in pair)):
                    raise ValueError("event identity missing")
                expected_id = hashlib.sha256(
                    json.dumps([ident, *pair], separators=(",", ":")).encode("utf-8")
                ).hexdigest()
                if event.get("event_id") not in (None, expected_id):
                    raise ValueError("journal event id mismatch")
                if ident in existing_events and existing_events[ident] != pair:
                    raise ValueError("conflicting journal identities")
                existing_events[ident] = pair
        except (OSError, ValueError, TypeError) as exc:
            raise RuntimeError("RECONCILIATION_JOURNAL_UNVERIFIED") from exc
    for proof in proofs:
        ident = proof["slice_id"]
        pair = (proof["candidate_sha"], proof["receipt_sha256"])
        if ident in existing_events and existing_events[ident] != pair:
            raise RuntimeError("RECONCILIATION_JOURNAL_IDENTITY_CONFLICT")

    try:
        payload = json.loads(controller_state_path.read_text("utf-8-sig"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("CONTROLLER_PROJECTION_UNREADABLE") from exc
    if payload.get("controller") != "iron-god-handoff-v1":
        raise RuntimeError("CONTROLLER_PROJECTION_IDENTITY_MISMATCH")
    if payload.get("mode") not in {"shadow_state_only", "candidate_mode"}:
        raise RuntimeError("CONTROLLER_PROJECTION_MODE_INVALID")
    rows = payload.get("slices")
    if not isinstance(rows, list):
        raise RuntimeError("CONTROLLER_PROJECTION_INVALID")
    by_id = {
        row.get("id"): row for row in rows
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    if len(by_id) != len(rows):
        raise RuntimeError("CONTROLLER_PROJECTION_INVALID")
    applied: list[dict] = []
    for proof in proofs:
        target_id = proof["parent_id"] or proof["slice_id"]
        row = by_id.get(target_id)
        if row is None:
            raise RuntimeError("CONTROLLER_PROJECTION_SLICE_MISSING:" + target_id)
        identity = _outcome_identity(
            proof["slice_id"], path, base_root=base_root, handoff_dir=handoff_dir
        )
        if row.get("state") == "VERIFIED":
            if (row.get("head_sha") != proof["candidate_sha"]
                    or row.get("receipt_sha256") != proof["receipt_sha256"]):
                raise RuntimeError("CONTROLLER_PROJECTION_VERIFIED_CONFLICT:" + target_id)
        elif row.get("state") in {"ELIGIBLE", "REPAIR_QUEUED"}:
            row["state"] = "VERIFIED"
            row["head_sha"] = proof["candidate_sha"]
            row["worktree_name"] = identity["work"].name
            row["branch"] = identity["branch"]
            row["receipt_sha256"] = proof["receipt_sha256"]
            row["evidence"] = {
                "preflight": "26/26 GREEN",
                "source_slice_id": proof["slice_id"],
                "projection_authority": "IRON_GOD_CANDIDATE_ONLY_NOT_ACCEPTED",
            }
            row["next_action"] = "DO_NOT_REBUILD"
        else:
            raise RuntimeError(
                "CONTROLLER_PROJECTION_STATE_NOT_RECONCILABLE:"
                + target_id + ":" + str(row.get("state"))
            )
        applied.append({
            **proof,
            "projection_slice_id": target_id,
            "worktree_name": identity["work"].name,
            "branch": identity["branch"],
        })
    # The owner may take control while exact-head evidence is being checked.
    # Recheck before publishing the reconciled projection.
    if not gate():
        raise RuntimeError("OWNER_OR_EXCLUSIVE_VETO")
    temp = controller_state_path.with_suffix(controller_state_path.suffix + ".reconcile-new")
    temp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if not gate():
        raise RuntimeError("OWNER_OR_EXCLUSIVE_VETO")
    os.replace(temp, controller_state_path)

    # Journal each proof durably BEFORE acknowledging it in SQLite. The
    # journal was already read and conflict-checked above, so this only
    # appends entries not already present (replay-safe).
    if controller_events_path is not None:
        if not gate():
            raise RuntimeError("OWNER_OR_EXCLUSIVE_VETO")
        with controller_events_path.open("a", encoding="utf-8") as handle:
            for proof in applied:
                if not gate():
                    raise RuntimeError("OWNER_OR_EXCLUSIVE_VETO")
                ident = proof["slice_id"]
                pair = (proof["candidate_sha"], proof["receipt_sha256"])
                if ident in existing_events:
                    continue
                event_id = hashlib.sha256(
                    json.dumps([ident, *pair], separators=(",", ":")).encode("utf-8")
                ).hexdigest()
                handle.write(json.dumps({
                    "event": "CANDIDATE_OUTCOME_RECONCILED",
                    "event_id": event_id,
                    "slice_id": ident,
                    "projection_slice_id": proof["projection_slice_id"],
                    "candidate_sha": pair[0],
                    "receipt_sha256": pair[1],
                    "authority": "CANDIDATE_ONLY_NOT_ACCEPTED",
                    "occurred_at": time.time(),
                }, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
                existing_events[ident] = pair
    # A crash after journaling but before SQLite acknowledgement replays
    # the exact proof and deduplicates the durable event.
    for proof in applied:
        if not gate():
            raise RuntimeError("OWNER_OR_EXCLUSIVE_VETO")
        acknowledge_outcome_reconciliation(
            slice_id=proof["slice_id"],
            candidate_sha=proof["candidate_sha"],
            receipt_sha256=proof["receipt_sha256"],
            path=path,
        )
    return applied


def record_failure_and_queue_repair(
    *, parent_slice_id: str, attempt_id: str, generation: int,
    failure_signature: str, reproduce: str, assertion_trace: str,
    attempt_slice_id: str | None = None,
    sanitized_log: str, environment_fingerprint: str, candidate_head: str,
    changed_paths: list[str], suspected_root_cause: str, required_tests: list[str],
    max_same_signature_attempts: int = 3, path: Path = SLICE_LEASE_DB,
    now: float | None = None,
) -> dict:
    _validate_slice_identity(parent_slice_id, candidate_head)
    lease_slice_id = attempt_slice_id or parent_slice_id
    _validate_slice_identity(lease_slice_id, candidate_head)
    required_text = {
        "failure_signature": failure_signature,
        "reproduce": reproduce,
        "assertion_trace": assertion_trace,
        "sanitized_log": sanitized_log,
        "environment_fingerprint": environment_fingerprint,
        "suspected_root_cause": suspected_root_cause,
    }
    limits = {
        "failure_signature": 512,
        "reproduce": 4000,
        "assertion_trace": 8000,
        "sanitized_log": 16000,
        "environment_fingerprint": 4000,
        "suspected_root_cause": 4000,
    }
    for name, value in required_text.items():
        if not isinstance(value, str) or not value.strip() or len(value) > limits[name]:
            raise RuntimeError("REPAIR_EVIDENCE_INVALID:" + name)
    if max_same_signature_attempts < 1:
        raise RuntimeError("REPAIR_BUDGET_INVALID")
    if not isinstance(changed_paths, list) or not all(
        isinstance(item, str) and item and not Path(item).is_absolute()
        and ".." not in Path(item).parts for item in changed_paths
    ):
        raise RuntimeError("REPAIR_CHANGED_PATHS_INVALID")
    if not isinstance(required_tests, list) or not required_tests or not all(
        isinstance(item, str) and item for item in required_tests
    ):
        raise RuntimeError("REPAIR_REQUIRED_TESTS_INVALID")
    now = time.time() if now is None else now
    init_slice_lease_db(path)
    db = _lease_connect(path)
    try:
        db.execute("BEGIN IMMEDIATE")
        lease = _fenced_lease(
            db, slice_id=lease_slice_id, attempt_id=attempt_id, generation=generation,
            now=now,
        )
        if lease_slice_id != parent_slice_id:
            current_repair = db.execute(
                "SELECT parent_slice_id,state FROM repair_slices WHERE repair_id=?",
                (lease_slice_id,),
            ).fetchone()
            if (current_repair is None
                    or current_repair["parent_slice_id"] != parent_slice_id
                    or current_repair["state"] != "REPAIR_QUEUED"):
                raise RuntimeError("REPAIR_ATTEMPT_STATE_CHANGED")
            db.execute(
                "UPDATE repair_slices SET state='FAILED',changed_at=? WHERE repair_id=?",
                (now, lease_slice_id),
            )
        previous = db.execute(
            "SELECT attempts FROM failure_signatures "
            "WHERE parent_slice_id=? AND failure_signature=?",
            (parent_slice_id, failure_signature),
        ).fetchone()
        previous_attempts = int(previous["attempts"]) if previous is not None else 0
        if previous_attempts > max_same_signature_attempts:
            raise RuntimeError("FAILURE_SIGNATURE_QUARANTINED")
        attempts = previous_attempts + 1
        db.execute(
            "INSERT INTO failure_signatures(parent_slice_id,failure_signature,attempts) "
            "VALUES(?,?,?) ON CONFLICT(parent_slice_id,failure_signature) "
            "DO UPDATE SET attempts=excluded.attempts",
            (parent_slice_id, failure_signature, attempts),
        )
        seq = int(db.execute(
            "SELECT COALESCE(MAX(sequence),0)+1 FROM repair_slices "
            "WHERE parent_slice_id=?", (parent_slice_id,)
        ).fetchone()[0])
        repair_id = f"{parent_slice_id}-R{seq:02d}"
        state = "QUARANTINED" if attempts > max_same_signature_attempts else "REPAIR_QUEUED"
        db.execute(
            "INSERT INTO repair_slices VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                repair_id, parent_slice_id, seq, failure_signature,
                reproduce, assertion_trace, sanitized_log, environment_fingerprint,
                candidate_head, json.dumps(changed_paths, sort_keys=True),
                suspected_root_cause, json.dumps(required_tests, sort_keys=True),
                state, now, now,
            ),
        )
        event_type = "FAILURE_QUARANTINED" if state == "QUARANTINED" else "REPAIR_QUEUED"
        db.execute(
            "INSERT INTO slice_lease_events VALUES(NULL,?,?,?,?,?,?)",
            (
                parent_slice_id, attempt_id, generation, event_type,
                repair_id + ":" + lease_slice_id + ":" + lease["state"], now,
            ),
        )
        db.execute("COMMIT")
        return {
            "repair_id": repair_id,
            "parent_slice_id": parent_slice_id,
            "failure_signature": failure_signature,
            "signature_attempts": attempts,
            "state": state,
        }
    except BaseException:
        try:
            db.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        db.close()


def mark_repair_result(
    *, repair_id: str, expected_state: str, outcome: str,
    path: Path = SLICE_LEASE_DB, now: float | None = None,
) -> None:
    if expected_state != "REPAIR_QUEUED" or outcome not in {"VERIFIED", "FAILED"}:
        raise RuntimeError("REPAIR_TRANSITION_INVALID")
    now = time.time() if now is None else now
    init_slice_lease_db(path)
    db = _lease_connect(path)
    try:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT state FROM repair_slices WHERE repair_id=?", (repair_id,)
        ).fetchone()
        if row is None or row["state"] != expected_state:
            raise RuntimeError("REPAIR_STATE_CHANGED")
        db.execute(
            "UPDATE repair_slices SET state=?,changed_at=? WHERE repair_id=?",
            (outcome, now, repair_id),
        )
        db.execute("COMMIT")
    except BaseException:
        try:
            db.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        db.close()


def repair_snapshot(path: Path = SLICE_LEASE_DB) -> list[dict]:
    init_slice_lease_db(path)
    db = _lease_connect(path)
    try:
        rows = db.execute(
            "SELECT repair_id,parent_slice_id,sequence,failure_signature,state,"
            "candidate_head,changed_paths_json,required_tests_json,created_at,changed_at "
            "FROM repair_slices ORDER BY parent_slice_id,sequence"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        db.close()


def repair_blocked_parents(path: Path = SLICE_LEASE_DB) -> set[str]:
    if not path.exists():
        return set()
    db = _lease_connect(path)
    try:
        rows = db.execute(
            "SELECT DISTINCT parent_slice_id FROM repair_slices"
        ).fetchall()
        return {str(row["parent_slice_id"]) for row in rows}
    finally:
        db.close()


def queued_repair_handoffs(path: Path = SLICE_LEASE_DB) -> list[dict]:
    if not path.exists():
        return []
    db = _lease_connect(path)
    try:
        rows = db.execute(
            "SELECT * FROM repair_slices WHERE state='REPAIR_QUEUED' "
            "ORDER BY created_at,repair_id"
        ).fetchall()
    finally:
        db.close()
    tasks: list[dict] = []
    for row in rows:
        repair_id = str(row["repair_id"])
        parent_id = str(row["parent_slice_id"])
        packet_path = HANDOFF_DIR / (parent_id + ".json")
        try:
            packet = json.loads(packet_path.read_text("utf-8-sig"))
            changed = json.loads(row["changed_paths_json"])
            required_tests = json.loads(row["required_tests_json"])
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("REPAIR_HANDOFF_UNVERIFIED:" + repair_id) from exc
        parent_allowed = packet.get("allowed_paths")
        forbidden = packet.get("forbidden", [])
        if not isinstance(parent_allowed, list) or not set(changed).issubset(set(parent_allowed)):
            raise RuntimeError("REPAIR_PATH_SCOPE_INVALID:" + repair_id)
        # A failed builder may have changed only one declared file. The repair
        # still needs the complete parent source/test scope to finish that slice.
        repair_paths = list(parent_allowed)
        # Repository-aware source-root (2026-10-18): same trusted, closed
        # mapping as runnable_handoffs() above and
        # accepted_event_dispatch_coordinator.py's own
        # _require_exact_handoff_task -- not duplicated, reused.
        from accepted_event_dispatch_coordinator import _source_root_for_repository
        repair_source_root = _source_root_for_repository(packet.get("repository"))
        sources = [item for item in repair_paths if isinstance(item, str) and item.startswith(repair_source_root)]
        tests = [item for item in repair_paths if isinstance(item, str) and item.startswith("tests/")]
        if len(sources) != 1 or len(tests) != 1 or len(repair_paths) != 2:
            raise RuntimeError("REPAIR_PATH_SHAPE_UNSUPPORTED:" + repair_id)
        if not isinstance(required_tests, list) or not required_tests:
            raise RuntimeError("REPAIR_TESTS_INVALID:" + repair_id)
        base_sha = str(row["candidate_head"])
        _validate_slice_identity(repair_id, base_sha)
        worktree_name = repair_id.lower() + "-worker-1"
        work = (BASE / worktree_name).resolve()
        if work.parent != BASE.resolve():
            raise RuntimeError("REPAIR_WORKTREE_ESCAPES_ROOT:" + repair_id)
        for item in repair_paths:
            if not (work / item).resolve().is_relative_to(work):
                raise RuntimeError("REPAIR_PATH_ESCAPES_WORKTREE:" + repair_id)
        branch = "iron-god/repair/" + repair_id.lower()
        prompt = (
            "Repair parent slice " + parent_id
            + ". Failure signature: " + str(row["failure_signature"])
            + ". Reproduce: " + str(row["reproduce"])
            + ". Assertion/trace: " + str(row["assertion_trace"])
            + ". Sanitized log: " + str(row["sanitized_log"])
            + ". Environment: " + str(row["environment_fingerprint"])
            + ". Suspected root cause: " + str(row["suspected_root_cause"])
            + ". Required tests: " + "; ".join(str(x) for x in required_tests)
            + ". Previously changed paths: " + ", ".join(changed)
            + ". Allowed repair paths: " + ", ".join(repair_paths)
            + ". Forbidden: " + "; ".join(str(x) for x in forbidden)
        )
        tasks.append({
            "id": repair_id,
            "parent_id": parent_id,
            "repair": True,
            "source": sources[0],
            "tests": tests[0],
            "work": work,
            "branch": branch,
            "base_sha": base_sha,
            "prompt": prompt,
        })
    return tasks


def resources() -> bool:
    if shutil.disk_usage("C:\\").free < 25 * 1024**3:
        return False
    class Memory(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    m = Memory()
    m.dwLength = ctypes.sizeof(m)
    return bool(ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))) and m.ullAvailPhys >= 2 * 1024**3


def publish(stage: str, **extra: object) -> None:
    if not gate():
        return
    payload = {"schema_version": 1, "stage": stage, "worker_pid": os.getpid(),
               "updated_at_utc": datetime.now(UTC).isoformat(), **extra}
    temp = STATUS.with_suffix(".new")
    temp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, STATUS)


def recover_interrupted_worker_status() -> dict | None:
    """Classify a stale RUNNING record only after the controller mutex is held.

    This repairs observability, not worker ownership or SQLite leases. The
    controller's independent reconciliation remains responsible for outcomes.
    """
    if not gate():
        raise RuntimeError("OWNER_OR_EXCLUSIVE_VETO")
    try:
        previous = json.loads(STATUS.read_text("utf-8-sig"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeError("WORKER_STATUS_UNVERIFIED") from exc
    if not isinstance(previous, dict) or not isinstance(previous.get("stage"), str):
        raise RuntimeError("WORKER_STATUS_UNVERIFIED")
    if previous["stage"] != "RUNNING":
        return None
    pid = previous.get("worker_pid")
    task = previous.get("task")
    updated = previous.get("updated_at_utc")
    if (type(pid) is not int or pid <= 0
            or not isinstance(task, str) or not task
            or not isinstance(updated, str) or not updated):
        raise RuntimeError("WORKER_STATUS_UNVERIFIED")
    recovery = {
        "interrupted_worker_pid": pid,
        "interrupted_task": task,
        "previous_update_utc": updated,
        "outcome": "UNVERIFIED_REQUIRES_RECONCILIATION",
    }
    publish("INTERRUPTED_WORKER_DETECTED", **recovery)
    return recovery


def git(work: Path, *args: str) -> subprocess.CompletedProcess[str]:
    if not gate():
        raise RuntimeError("OWNER_OR_EXCLUSIVE_VETO")
    clean_env = {key: value for key, value in os.environ.items()
                 if not key.upper().startswith("GIT_")}
    p = subprocess.run(["git", "-C", str(work), *args], text=True,
                       capture_output=True, timeout=45, check=False, env=clean_env)
    if p.returncode:
        raise RuntimeError("GIT_FAILED:" + args[0] + ":" + p.stderr[-200:])
    return p


def stop_owned_process(pid: int) -> None:
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=15, check=False, creationflags=subprocess.CREATE_NO_WINDOW)


def run(work: Path, cmd: list[str], stage: str, *, timeout: int,
        lease_heartbeat=None) -> None:
    if not gate() or not resources():
        raise RuntimeError("OWNER_OR_RESOURCE_VETO")
    publish("RUNNING", task=stage)
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env["PYTHONPATH"] = str(work / "src")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    path = BASE / ("run-" + stage.replace("/", "_") + ".log")
    with path.open("a", encoding="utf-8") as out:
        out.write("\n" + datetime.now(UTC).isoformat() + " " + repr(cmd[:2]) + "\n")
        out.flush()
        p = subprocess.Popen(cmd, cwd=work, env=env, stdin=subprocess.DEVNULL,
                             stdout=out, stderr=subprocess.STDOUT,
                             creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
        until = time.monotonic() + timeout
        next_lease_heartbeat = time.monotonic() + 30
        while p.poll() is None:
            if not gate() or not resources():
                stop_owned_process(p.pid)
                raise RuntimeError("OWNER_OR_RESOURCE_VETO")
            if lease_heartbeat is not None and time.monotonic() >= next_lease_heartbeat:
                try:
                    lease_heartbeat()
                except Exception:
                    stop_owned_process(p.pid)
                    raise
                next_lease_heartbeat = time.monotonic() + 30
            if time.monotonic() > until:
                stop_owned_process(p.pid)
                raise StageFailure(
                    stage=stage, command=cmd, log_path=path,
                    reason="COMMAND_TIMEOUT:" + stage,
                )
            time.sleep(2)
        if p.returncode != 0:
            raise StageFailure(
                stage=stage, command=cmd, log_path=path,
                reason="COMMAND_FAILED:" + stage + ":" + str(p.returncode),
            )
    # Do not leave completed stages falsely advertised as active workers.
    publish("STAGE_COMPLETED", task=stage)


def check_paths(work: Path, paths: set[str]) -> None:
    # Git status may conceal modified tracked files carrying skip-worktree or
    # assume-unchanged index flags. Refuse verification while either is set.
    index_entries = git(work, "ls-files", "-v", "-z").stdout.split("\0")
    for entry in index_entries:
        if entry and (entry[0].islower() or entry[0] == "S"):
            raise RuntimeError("HIDDEN_GIT_INDEX_FLAG:" + entry[2:])
    output = git(work, "status", "--porcelain=v1", "-uall").stdout
    for row in output.splitlines():
        changed = row[3:].replace("\\", "/").strip('"')
        if changed not in paths and changed != "preflight-evidence.json":
            raise RuntimeError("UNEXPECTED_WORKTREE_CHANGE:" + changed)


def verify_and_commit(task: dict, *, lease_heartbeat=None) -> dict:
    ident = task["id"]
    source = task["source"]
    tests = task["tests"]
    work = task["work"]
    if not (work / source).is_file() or not (work / tests).is_file():
        raise RuntimeError("EXPECTED_SOURCE_OR_TEST_MISSING:" + ident)
    check_paths(work, {source, tests})
    run(work, ["ruff", "format", source, tests], ident + "-format", timeout=90,
        lease_heartbeat=lease_heartbeat)
    run(work, ["ruff", "check", source, tests], ident + "-lint", timeout=90,
        lease_heartbeat=lease_heartbeat)
    run(work, [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", tests],
        ident + "-focused", timeout=600, lease_heartbeat=lease_heartbeat)
    check_paths(work, {source, tests})
    git(work, "add", "--", source, tests)
    names = {row.strip() for row in git(work, "diff", "--cached", "--name-only").stdout.splitlines()}
    if names != {source, tests}:
        raise RuntimeError("STAGED_PATH_MISMATCH:" + repr(names))
    git(work, "commit", "-m", "feat(iron-god): " + ident + " local verified slice")
    head = git(work, "rev-parse", "HEAD").stdout.strip()
    publish("LOCAL_COMMITTED", task=ident, head_sha=head)
    run(work, [sys.executable, "engineering/preflight.py"], ident + "-full", timeout=1200,
        lease_heartbeat=lease_heartbeat)
    # Full preflight may modify the checkout after the candidate was committed.
    # Never certify a receipt for a different HEAD or a dirty candidate tree.
    if git(work, "rev-parse", "HEAD").stdout.strip() != head:
        raise RuntimeError("PREFLIGHT_CHANGED_HEAD:" + ident)
    check_paths(work, set())
    receipt_path = work / "preflight-evidence.json"
    if receipt_path.is_symlink():
        raise RuntimeError("EXACT_HEAD_NATIVE_PREFLIGHT_UNVERIFIED:" + ident)
    try:
        raw = receipt_path.read_bytes()
        evidence = json.loads(raw)
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeError("EXACT_HEAD_NATIVE_PREFLIGHT_UNVERIFIED:" + ident) from exc
    if not isinstance(evidence, dict):
        raise RuntimeError("EXACT_HEAD_NATIVE_PREFLIGHT_UNVERIFIED:" + ident)
    results = evidence.get("results")
    if (evidence.get("head_sha") != head or evidence.get("mode") != "FULL"
            or evidence.get("status") != "GREEN"
            or not isinstance(results, list) or len(results) != 26
            or any(not isinstance(row, dict)
                   or not isinstance(row.get("name"), str)
                   or not row["name"].strip()
                   or row.get("returncode") != 0
                   or row.get("timed_out") is not False
                   or row.get("interrupted") is not False for row in results)
            or len({row["name"] for row in results}) != 26):
        raise RuntimeError("EXACT_HEAD_NATIVE_PREFLIGHT_UNVERIFIED:" + ident)
    receipt_sha256 = hashlib.sha256(raw).hexdigest()
    publish("EXACT_HEAD_NATIVE_GREEN", task=ident, head_sha=head, checks=len(results),
            receipt_sha256=receipt_sha256)
    return {"head_sha": head, "receipt_sha256": receipt_sha256, "checks": len(results)}


def changed_paths(work: Path) -> list[str]:
    output = git(work, "status", "--porcelain=v1", "-uall").stdout
    paths: set[str] = set()
    for row in output.splitlines():
        path = row[3:].replace("\\", "/").strip('"')
        if path:
            paths.add(path)
    return sorted(paths)


def validate_candidate_worktree(task: dict) -> None:
    ident = task["id"]
    work = task["work"]
    if not work.is_dir():
        raise RuntimeError("SLICE_WORKTREE_NOT_PROVISIONED:" + ident)
    if git(work, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() != task["branch"]:
        raise RuntimeError("UNEXPECTED_BRANCH:" + ident)
    if Path(git(work, "rev-parse", "--show-toplevel").stdout.strip()).resolve() != work.resolve():
        raise RuntimeError("UNEXPECTED_WORKTREE:" + ident)
    if git(work, "rev-parse", "HEAD").stdout.strip() != task["base_sha"]:
        raise RuntimeError("SLICE_BASE_SHA_MISMATCH:" + ident)
    # Apply the same hidden-index-flag guard before leasing or launching an
    # agent; checking only after preflight would allow a concealed dirty base.
    check_paths(work, set())
    initial = [item for item in changed_paths(work) if item != "preflight-evidence.json"]
    if initial:
        raise RuntimeError("SLICE_WORKTREE_NOT_CLEAN:" + ident + ":" + repr(initial))


def build_candidate(task: dict, *, lease_heartbeat=None) -> None:
    ident = task["id"]
    work = task["work"]
    extra = (
        "You are an Iron God LOCAL-FIRST coding worker on a separate, isolated "
        "laptop worktree. The user requires small verified changes with NO GitHub "
        "operations until major milestones. Do NOT run commands or Git, touch "
        "other repos, owner control, schedules, AGENTS.md, DoWork.txt or configs. "
    )
    run(
        work,
        [
            "claude", "-p", extra + task["prompt"], "--restricted", "--tools",
            "Read,Edit,Write,Glob,Grep", "--permission-mode", "acceptEdits",
            "--permission-prompts", "none", "--no-session-persistence",
            "--output-format", "text",
        ],
        ident + "-agent",
        timeout=1500,
        lease_heartbeat=lease_heartbeat,
    )


def execute_candidate_attempt(
    task: dict, *, lease_path: Path = SLICE_LEASE_DB,
    builder_fn=None, verifier_fn=None, authorized_fn=None, resource_fn=None,
    dispatch_bind_fn=None,
) -> dict:
    """Run one fenced candidate attempt; failures become durable repair work.

    This function has no publication or accepted-baseline authority. A
    dispatch-originated task must bind its exact claim token to the newly
    acquired worker lease before any builder or verifier is invoked.
    """
    builder_fn = build_candidate if builder_fn is None else builder_fn
    verifier_fn = verify_and_commit if verifier_fn is None else verifier_fn
    dispatch_token = task.get("dispatch_claim_token")
    if dispatch_token is not None and dispatch_bind_fn is None:
        raise RuntimeError("DISPATCH_PROVENANCE_BINDING_REQUIRED")
    if dispatch_bind_fn is not None and (
        not isinstance(dispatch_token, str)
        or re.fullmatch(r"[0-9a-f]{32}", dispatch_token) is None
    ):
        raise RuntimeError("DISPATCH_CLAIM_TOKEN_INVALID")
    # Production calls must re-check controller authority at the execution
    # boundary, not rely solely on the scheduler's earlier decision.
    if authorized_fn is None:
        def authorized_fn():
            try:
                state = json.loads(CONTROLLER_STATE.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                return False
            return (state.get("mode") == "candidate_mode"
                    and state.get("candidate_execution_authorized") is True
                    and gate())
    resource_fn = resources if resource_fn is None else resource_fn
    ident = task["id"]
    parent_id = task.get("parent_id") or ident
    if not authorized_fn() or not resource_fn():
        raise RuntimeError("OWNER_OR_RESOURCE_VETO")
    validate_candidate_worktree(task)
    lease = claim_slice_lease(
        slice_id=ident,
        base_sha=task["base_sha"],
        owner_pid=os.getpid(),
        ttl_seconds=1800,
        path=lease_path,
    )
    attempt_id = lease["attempt_id"]
    generation = lease["generation"]

    def renew_lease() -> None:
        if not authorized_fn() or not resource_fn():
            raise RuntimeError("OWNER_OR_RESOURCE_VETO")
        heartbeat_slice_lease(
            slice_id=ident, attempt_id=attempt_id, generation=generation,
            ttl_seconds=1800, path=lease_path,
        )

    try:
        if dispatch_bind_fn is not None:
            binding = dispatch_bind_fn(
                task=task, attempt_id=attempt_id, generation=generation,
                lease_path=lease_path,
            )
            if (
                getattr(binding, "slice_id", None) != ident
                or getattr(binding, "claim_token", None) != dispatch_token
                or getattr(binding, "attempt_id", None) != attempt_id
                or getattr(binding, "generation", None) != generation
                or getattr(binding, "accepted_base_sha", None) != task["base_sha"]
            ):
                raise RuntimeError("DISPATCH_WORKER_BINDING_IDENTITY_MISMATCH")
            if not authorized_fn() or not resource_fn():
                raise RuntimeError("OWNER_OR_RESOURCE_VETO")
        transition_slice_lease(
            slice_id=ident, attempt_id=attempt_id, generation=generation,
            expected_state="LEASED", next_state="BUILDING", path=lease_path,
        )
        if builder_fn is build_candidate:
            builder_fn(task, lease_heartbeat=renew_lease)
        else:
            builder_fn(task)
        renew_lease()
        transition_slice_lease(
            slice_id=ident, attempt_id=attempt_id, generation=generation,
            expected_state="BUILDING", next_state="VERIFYING", path=lease_path,
        )
        try:
            if verifier_fn is verify_and_commit:
                verification = verifier_fn(task, lease_heartbeat=renew_lease)
            else:
                verification = verifier_fn(task)
        except RuntimeError as exc:
            # Deterministic verifier failures must enter the existing fenced
            # repair path, not strand the lease until TTL expiry. Never turn
            # owner/resource veto, lease fencing, or unknown faults into repairs.
            failure_code = str(exc).split(":", 1)[0]
            repairable = {
                "EXPECTED_SOURCE_OR_TEST_MISSING",
                "STAGED_PATH_MISMATCH",
                "EXACT_HEAD_NATIVE_PREFLIGHT_UNVERIFIED",
            }
            if failure_code not in repairable:
                raise
            raise StageFailure(
                stage=ident + "-verification",
                command=["verify_and_commit", ident],
                log_path=BASE / ("run-" + ident + "-verification.log"),
                reason=failure_code + ":" + ident,
            ) from exc
        if not authorized_fn() or not resource_fn():
            raise RuntimeError("OWNER_OR_RESOURCE_VETO")
        candidate_sha = str(verification["head_sha"])
        current_head = git(task["work"], "rev-parse", "HEAD").stdout.strip()
        if candidate_sha != current_head:
            raise RuntimeError("VERIFIER_HEAD_MISMATCH:" + ident)
        if candidate_sha == task["base_sha"]:
            raise StageFailure(
                stage=ident + "-no-progress",
                command=["candidate-no-progress", ident],
                log_path=BASE / ("run-" + ident + "-no-progress.log"),
                reason="AGENT_NO_PROGRESS:" + ident,
            )
        record_slice_outcome(
            slice_id=ident,
            attempt_id=attempt_id,
            generation=generation,
            base_sha=task["base_sha"],
            candidate_sha=candidate_sha,
            state="VERIFIED_CANDIDATE",
            evidence={
                "receipt_sha256": verification.get("receipt_sha256"),
                "checks": verification.get("checks"),
                "parent_id": task.get("parent_id"),
            },
            path=lease_path,
        )
        release_slice_lease(
            slice_id=ident, attempt_id=attempt_id, generation=generation,
            outcome="VERIFIED_CANDIDATE", path=lease_path,
        )
        return {
            "status": "VERIFIED_CANDIDATE",
            "slice_id": ident,
            "candidate_sha": candidate_sha,
            "attempt_id": attempt_id,
            "generation": generation,
        }
    except StageFailure as exc:
        # Owner/resource takeover means stop immediately; leave the fenced lease
        # for stale recovery rather than mutating orchestration state after veto.
        if not authorized_fn() or not resource_fn():
            raise
        actual_paths = changed_paths(task["work"])
        allowed = {task["source"], task["tests"], "preflight-evidence.json"}
        unexpected = sorted(set(actual_paths) - allowed)
        if unexpected:
            raise RuntimeError(
                "UNEXPECTED_WORKTREE_CHANGE:" + ident + ":" + repr(unexpected)
            ) from exc
        current_sha = git(task["work"], "rev-parse", "HEAD").stdout.strip()
        evidence = stage_failure_evidence(exc)
        repair = record_failure_and_queue_repair(
            parent_slice_id=parent_id,
            attempt_slice_id=ident,
            attempt_id=attempt_id,
            generation=generation,
            failure_signature=evidence["failure_signature"],
            reproduce=evidence["reproduce"],
            assertion_trace=evidence["assertion_trace"],
            sanitized_log=evidence["sanitized_log"],
            environment_fingerprint=evidence["environment_fingerprint"],
            candidate_head=current_sha,
            changed_paths=[item for item in actual_paths if item != "preflight-evidence.json"],
            suspected_root_cause=evidence["suspected_root_cause"],
            required_tests=[task["tests"]],
            path=lease_path,
        )
        record_slice_outcome(
            slice_id=ident,
            attempt_id=attempt_id,
            generation=generation,
            base_sha=task["base_sha"],
            candidate_sha=current_sha,
            state="FAILED_ROUTED",
            evidence={"repair_id": repair["repair_id"], "failure_signature": evidence["failure_signature"]},
            path=lease_path,
        )
        release_slice_lease(
            slice_id=ident, attempt_id=attempt_id, generation=generation,
            outcome="FAILED_ROUTED", path=lease_path,
        )
        return {
            "status": repair["state"],
            "slice_id": ident,
            "repair_id": repair["repair_id"],
            "attempt_id": attempt_id,
            "generation": generation,
        }

def execute_reserved_dispatch_claim(
    *, claim, task: dict, claims_path: Path, state, refs,
    owner_state_root: Path, lease_path: Path = SLICE_LEASE_DB,
) -> dict:
    """Bind a reserved accepted-event claim to the existing fenced builder.

    The caller must first use the durable dispatch claim's invoke_once()
    reservation. This adapter does not create claims, grant execution
    authority, advance accepted refs, or start a second controller.
    """
    if (
        getattr(claim, "state", None) != "CLAIMED"
        or type(getattr(claim, "event_id", None)) is not int
        or claim.event_id < 1
        or not isinstance(getattr(claim, "token", None), str)
        or re.fullmatch(r"[0-9a-f]{32}", claim.token) is None
        or not isinstance(getattr(claim, "accepted_sha", None), str)
        or re.fullmatch(r"[0-9a-f]{40}", claim.accepted_sha) is None
        or task.get("id") != claim.slice_id
        or task.get("base_sha") != claim.accepted_sha
        or task.get("dispatch_claim_token") not in (None, claim.token)
    ):
        raise RuntimeError("DISPATCH_CLAIM_TASK_IDENTITY_MISMATCH")
    if Path(owner_state_root).resolve() != FLOW.resolve() or not gate():
        raise RuntimeError("OWNER_OR_RESOURCE_VETO")

    # Reject a claim that has not been durably reserved by invoke_once()
    # BEFORE acquiring a worker lease. Otherwise a caller could strand an
    # unreserved lease merely by supplying a valid-looking claim object.
    from project_orchestrator.local_dispatch_event_claim import inspect_claims
    from project_orchestrator.local_dispatch_worker_binding import (
        _admission,
        bind_worker_lease,
    )

    reservations = [
        row for row in inspect_claims(Path(claims_path))
        if row["claim_token"] == claim.token
    ]
    if (
        len(reservations) != 1
        or reservations[0]["state"] != "INVOCATION_RESERVED"
        or reservations[0]["acceptance_event_id"] != claim.event_id
        or reservations[0]["slice_id"] != claim.slice_id
        or reservations[0]["accepted_sha"] != claim.accepted_sha
    ):
        raise RuntimeError("DISPATCH_RESERVATION_UNVERIFIED")

    # A reserved token is not proof of an accepted product. Check the
    # original acceptance event, protected refs, source DB and owner root
    # BEFORE leasing a worker. The binder checks them again after leasing.
    admitted = _admission(
        claim=claim,
        claims_path=Path(claims_path),
        state=state,
        refs=refs,
        owner_state_root=Path(owner_state_root),
    )

    # A dispatch token authorizes a specific controller handoff, not merely
    # any branch/source pair that a caller supplies for the same slice ID.
    # Resolve through the existing strict handoff parser, including its
    # controller eligibility and path/branch/worktree checks.
    eligible = [
        row for row in runnable_handoffs(repair_db_path=lease_path)
        if row["id"] == claim.slice_id and not row.get("repair")
    ]
    if len(eligible) != 1 or any(
        task.get(field) != eligible[0][field]
        for field in ("id", "work", "branch", "base_sha",
                      "source", "tests", "prompt")
    ):
        raise RuntimeError("DISPATCH_HANDOFF_TASK_IDENTITY_MISMATCH")
    packet_path = HANDOFF_DIR / (claim.slice_id + ".json")
    try:
        packet = json.loads(packet_path.read_text("utf-8-sig"))
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeError("DISPATCH_HANDOFF_PACKET_UNVERIFIED") from exc
    dependencies = packet.get("requires_milestones")
    # Match the accepted-event selector's dependency contract. An eligible
    # independent slice may require no milestones; other slices may depend
    # on any already committed milestone from the SAME repository, not
    # necessarily the milestone that triggered this refill.
    accepted_milestones = {
        row["milestone_id"]
        for row in state.snapshot()["milestones"]
        if row["repository"] == admitted["repository"]
    }
    if (
        packet.get("repository") != admitted["repository"]
        or not isinstance(dependencies, list)
        or any(not isinstance(dep, str) for dep in dependencies)
        or not set(dependencies).issubset(accepted_milestones)
    ):
        raise RuntimeError("DISPATCH_HANDOFF_ACCEPTANCE_IDENTITY_MISMATCH")

    def bind(*, task, attempt_id, generation, lease_path):
        if (
            task.get("id") != claim.slice_id
            or task.get("base_sha") != claim.accepted_sha
            or task.get("dispatch_claim_token") != claim.token
        ):
            raise RuntimeError("DISPATCH_CLAIM_TASK_IDENTITY_MISMATCH")
        return bind_worker_lease(
            claim=claim,
            claims_path=Path(claims_path),
            state=state,
            refs=refs,
            owner_state_root=Path(owner_state_root),
            worker_db_path=Path(lease_path),
            attempt_id=attempt_id,
            generation=generation,
        )

    bound_task = dict(task)
    bound_task["dispatch_claim_token"] = claim.token
    return execute_candidate_attempt(
        bound_task, lease_path=lease_path, dispatch_bind_fn=bind,
    )


def main() -> int:
    # Owner takeover must not create or modify even the controller mutex.
    # Recheck after acquiring the mutex to catch a concurrent takeover.
    if not gate():
        return 20
    lock = (BASE / "local-worker-mutex").open("a+b")
    lock.seek(0)
    if not lock.read(1):
        lock.write(b"X")
        lock.flush()
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        # Another controller may own the mutex. Do not leave this failed
        # contender's file handle open (Windows would retain an extra lock).
        lock.close()
        return 11
    try:
        if not gate() or not resources():
            return 20
        # The mutex proves that a previous RUNNING status from this worker
        # cannot still be owned by another live controller invocation.
        # Recover observability only; leases and candidate outcomes remain
        # governed by independent exact-evidence reconciliation.
        recover_interrupted_worker_status()
        recovered = apply_shadow_candidate_reconciliation(
            SLICE_LEASE_DB,
            controller_state_path=CONTROLLER_STATE,
            controller_events_path=BASE / "controller-events.jsonl",
            base_root=BASE,
            handoff_dir=HANDOFF_DIR,
        )
        payload = controller_payload()
        terminal = sorted(reconciled_terminal_slices())
        queue = runnable_handoffs()
        next_slice = queue[0]["id"] if queue else None
        if payload["mode"] == "shadow_state_only":
            publish("SHADOW_READY", terminal=terminal, next_slice=next_slice,
                    handoff_queue_diagnostics=handoff_queue_diagnostics(),
                    reconciled_outcomes=[item["slice_id"] for item in recovered])
            return 0
        if payload.get("candidate_execution_authorized") is not True:
            raise RuntimeError("CANDIDATE_MODE_NOT_EXPLICITLY_AUTHORIZED")
        # A candidate-mode flag alone is insufficient authority to launch.
        # Until the acceptance-event coordinator is connected to this
        # resident entrypoint, refuse all legacy queue-driven execution.
        raise RuntimeError("DISPATCH_ACCEPTANCE_COORDINATOR_NOT_CONNECTED")
        # The protected ledger remains authoritative; handoffs alone cannot
        # confer execution authority.
        completed: list[dict] = []
        for _ in range(4):
            if not gate() or not resources():
                raise RuntimeError("OWNER_OR_RESOURCE_VETO")
            current = controller_payload()
            if (current["mode"] != "candidate_mode"
                    or current.get("candidate_execution_authorized") is not True):
                raise RuntimeError("CANDIDATE_AUTHORIZATION_REVOKED")
            queue = runnable_handoffs()
            if not queue:
                publish("CANDIDATE_QUEUE_IDLE", terminal=sorted(reconciled_terminal_slices()),
                        handoff_queue_diagnostics=handoff_queue_diagnostics(),
                        completed=completed)
                return 0
            # Runnable handoffs have already passed the controller's eligibility
            # filter. An unprovisioned worktree blocks only that slice, not
            # another independently eligible, provisioned slice in this queue.
            # Never fabricate or provision a missing worktree here.
            task = next((item for item in queue if item["work"].is_dir()), None)
            if task is None:
                publish("CANDIDATE_WAITING_WORKTREE", next_slice=queue[0]["id"],
                        waiting_slices=[item["id"] for item in queue],
                        completed=completed)
                return 0
            if task is not queue[0]:
                publish("CANDIDATE_ROTATED_MISSING_WORKTREE",
                        waiting_slices=[
                            item["id"] for item in queue
                            if not item["work"].is_dir()
                        ],
                        next_slice=task["id"], completed=completed)
            # The legacy queue has no durable acceptance-event reservation.
            # Never launch an unbound worker: only the separately fenced
            # execute_reserved_dispatch_claim() path may invoke the builder.
            raise RuntimeError("DISPATCH_ACCEPTANCE_RESERVATION_REQUIRED")
        return 0
    except Exception as exc:
        publish("BLOCKED", error=str(exc)[:800])
        return 2
    finally:
        # Even a failed unlock must not leak a Windows file handle.
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
