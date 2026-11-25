"""Versioned engineering handoff envelopes, using the existing tracked request ledger.

Validation establishes shape and scope only. GitHub evidence and owner authorization
must be checked independently before any proposed work is executed.
"""
from __future__ import annotations

import json
import re
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from . import iron_god_observability as observability
from . import iron_god_mcp_readonly_bridge as bridge
from . import mcp_coordination as mail
from . import mcp_coordination_accountability as accountability

PROTOCOL = "engineering_handoff_v1"
_FIELDS = frozenset({
    "protocol", "repository", "milestone", "slice_id", "commit_sha", "status",
    "tests", "pull_request", "dependencies", "next_action", "failure",
})
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#@+-]{0,119}$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_STATUSES = frozenset({"PROPOSED", "IN_PROGRESS", "BLOCKED", "REVIEW_REQUIRED", "TESTED"})
_ACTIONS = frozenset({"INVESTIGATE", "IMPLEMENT", "TEST", "REVIEW", "RECONCILE"})
_FAILURES = frozenset({"NONE", "TEST", "ENVIRONMENT", "DEPENDENCY", "CAPABILITY", "UNKNOWN"})


def _token(value: object) -> bool:
    return isinstance(value, str) and bool(_TOKEN.fullmatch(value))


def _tokens(value: object, *, maximum: int) -> bool:
    return (isinstance(value, list) and len(value) <= maximum
            and all(_token(item) for item in value)
            and len(set(value)) == len(value))


def encode_handoff(payload: dict) -> str:
    """Strict allowlist, canonical JSON, no arbitrary commands or embedded secrets."""
    if not isinstance(payload, dict) or payload.keys() != _FIELDS:
        raise ValueError("INVALID_HANDOFF_FIELDS")
    if payload["protocol"] != PROTOCOL:
        raise ValueError("UNSUPPORTED_HANDOFF_PROTOCOL")
    if not isinstance(payload["repository"], str) or payload["repository"] not in observability.REPOSITORIES:
        raise ValueError("UNKNOWN_HANDOFF_REPOSITORY")
    if not all(_token(payload[field]) for field in ("milestone", "slice_id")):
        raise ValueError("INVALID_HANDOFF_SCOPE")
    sha = payload["commit_sha"]
    if not isinstance(sha, str) or not _SHA.fullmatch(sha) or sha == "0" * 40:
        raise ValueError("EXACT_HANDOFF_SHA_REQUIRED")
    for field, allowed in (("status", _STATUSES), ("next_action", _ACTIONS), ("failure", _FAILURES)):
        if not isinstance(payload[field], str) or payload[field] not in allowed:
            raise ValueError("INVALID_HANDOFF_" + field.upper())
    if not _tokens(payload["tests"], maximum=8) or not _tokens(payload["dependencies"], maximum=8):
        raise ValueError("INVALID_HANDOFF_EVIDENCE")
    if payload["slice_id"] in payload["dependencies"]:
        raise ValueError("HANDOFF_SELF_DEPENDENCY")
    pr = payload["pull_request"]
    if pr is not None and (type(pr) is not int or not 1 <= pr <= 2**31 - 1):
        raise ValueError("INVALID_HANDOFF_PULL_REQUEST")
    if payload["status"] == "TESTED" and not payload["tests"]:
        raise ValueError("HANDOFF_TEST_EVIDENCE_REQUIRED")
    if (payload["status"] == "BLOCKED") != (payload["failure"] != "NONE"):
        raise ValueError("HANDOFF_FAILURE_STATUS_MISMATCH")
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    if len(body) > mail.MAX_MESSAGE_CHARS or mail._SENSITIVE.search(body):
        raise ValueError("UNSAFE_HANDOFF_PAYLOAD")
    return body


def decode_handoff(body: str) -> dict:
    """Reject ambiguous duplicate JSON keys before validating the envelope."""
    if not isinstance(body, str) or len(body) > mail.MAX_MESSAGE_CHARS:
        raise ValueError("INVALID_HANDOFF_BODY")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("DUPLICATE_HANDOFF_FIELD")
            result[key] = value
        return result

    try:
        payload = json.loads(body, object_pairs_hook=unique)
    except (ValueError, RecursionError) as exc:
        raise ValueError("INVALID_HANDOFF_JSON") from exc
    encode_handoff(payload)
    return payload


def open_engineering_handoff(path: Path, *, sender_label: str, recipient: str,
                             payload: dict, idempotency_key: str,
                             deadline_at_utc: str | None = None) -> dict:
    body = encode_handoff(payload)
    # Use tracked requests and their existing idempotent reply/disposition lifecycle.
    reference = payload["repository"] + "@" + payload["commit_sha"]
    return accountability.open_request(
        path, sender_label=sender_label, recipient=recipient, body=body,
        reference=reference, idempotency_key=idempotency_key,
        deadline_at_utc=deadline_at_utc,
    )

def read_engineering_handoff(path: Path, *, participant: str, request_id: str) -> dict:
    """Read only an explicitly tracked, participant-scoped engineering envelope.

    The immutable message and tracking record must agree. A raw mailbox post
    resembling the protocol is not a tracked engineering handoff.
    """
    mail._check_principal(participant)
    if not isinstance(request_id, str) or not accountability._REQUEST_ID.fullmatch(request_id):
        raise ValueError("INVALID_REQUEST_ID")
    db_path = mail._path(path)
    if not db_path.is_file():
        raise ValueError("TRACKED_REQUEST_NOT_FOUND")
    with closing(mail._connect(db_path, read_only=True)) as db:
        db.execute("BEGIN")
        if db.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise ValueError("UNSUPPORTED_COORDINATION_SCHEMA")
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tracked_requests'"
        ).fetchone():
            raise ValueError("TRACKED_REQUEST_NOT_FOUND")
        row = db.execute(
            "SELECT t.sender_label,t.recipient,t.reference,t.deadline_at_utc,"
            "m.sender_label AS message_sender,m.recipient AS message_recipient,"
            "m.reference AS message_reference,m.kind,m.body "
            "FROM tracked_requests t JOIN messages m ON m.message_id=t.request_id "
            "WHERE t.request_id=? AND (t.sender_label=? OR t.recipient=?)",
            (request_id, participant, participant),
        ).fetchone()
        if row is None:
            raise ValueError("TRACKED_REQUEST_NOT_FOUND")
        if (row["sender_label"], row["recipient"], row["reference"], row["kind"]) != (
            row["message_sender"], row["message_recipient"], row["message_reference"], "decision_request",
        ):
            raise ValueError("HANDOFF_ENVELOPE_MISMATCH")
        payload = decode_handoff(row["body"])
        if row["reference"] != payload["repository"] + "@" + payload["commit_sha"]:
            raise ValueError("HANDOFF_REFERENCE_MISMATCH")
        return {
            "request_id": request_id, "sender": row["sender_label"], "recipient": row["recipient"],
            "deadline_at_utc": row["deadline_at_utc"], "payload": payload,
            "verification": "STRUCTURAL_ONLY", "can_execute": False,
        }

def assess_handoff_freshness(path: Path, *, participant: str, request_id: str,
                             configured_repository: str, repo_root: Path,
                             now: datetime | None = None) -> dict:
    """Cheap, opt-in local freshness check. No network, writes or dispatch.

    The repository identity and checkout root are host configuration, never
    handoff fields or client-selected filesystem paths. Local HEAD equality
    does not establish remote PR freshness, CI success or owner permission.
    """
    handoff = read_engineering_handoff(path, participant=participant, request_id=request_id)
    payload = handoff["payload"]
    result = {
        "request_id": request_id,
        "repository": payload["repository"],
        "commit_sha": payload["commit_sha"],
        "work_type": payload["next_action"],
        "status": "UNVERIFIED",
        "reason": "CONFIGURED_REPOSITORY_MISMATCH",
        "remote_head_verified": False,
        "test_evidence_verified": False,
        "dependencies_verified": False,
        "can_execute": False,
    }
    moment = now if now is not None else datetime.now(timezone.utc)
    if not isinstance(moment, datetime) or moment.utcoffset() is None:
        raise ValueError("INVALID_ASSESSMENT_TIME")
    deadline = handoff["deadline_at_utc"]
    if deadline is not None and accountability._time(deadline) <= moment:
        return {**result, "status": "EXPIRED", "reason": "HANDOFF_DEADLINE_ELAPSED"}
    if (configured_repository not in observability.REPOSITORIES
            or configured_repository != payload["repository"]
            or not repo_root.is_absolute()):
        return result
    head = bridge._repository_head(repo_root)
    if head.get("status") != "OK":
        return {**result, "reason": "LOCAL_HEAD_UNAVAILABLE"}
    observed = head.get("head_sha")
    if not isinstance(observed, str) or not _SHA.fullmatch(observed):
        return {**result, "reason": "LOCAL_HEAD_INVALID"}
    if observed != payload["commit_sha"]:
        return {**result, "status": "HEAD_MISMATCH", "reason": "LOCAL_HEAD_DIFFERS",
                "observed_local_head": observed}
    return {**result, "status": "LOCAL_HEAD_MATCH", "reason": "LOCAL_ONLY_NOT_REMOTE_PROOF",
            "observed_local_head": observed}

def pending_handoff_page(path: Path, *, participant: str, after_seq: int = 0,
                         limit: int = 5, now: datetime | None = None) -> dict:
    """Bounded, recipient-only routing view; never a second execution queue.

    Only tracked, unacknowledged incoming requests are scanned. Legacy or
    invalid envelopes consume a page slot but cannot strand later requests.
    Cursor wrap makes interrupted scans replay-safe without claiming work.
    """
    mail._check_principal(participant)
    if type(after_seq) is not int or after_seq < 0 or type(limit) is not int or not 1 <= limit <= 30:
        raise ValueError("INVALID_HANDOFF_PAGE")
    moment = now if now is not None else datetime.now(timezone.utc)
    if not isinstance(moment, datetime) or moment.utcoffset() is None:
        raise ValueError("INVALID_ASSESSMENT_TIME")
    db_path = mail._path(path)
    empty = {"status": "NOT_CONFIGURED", "next_seq": after_seq, "items": [],
             "scanned": 0, "can_execute": False}
    if not db_path.is_file():
        return {**empty, "status": "UNINITIALIZED"}
    with closing(mail._connect(db_path, read_only=True)) as db:
        db.execute("BEGIN")
        if db.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise ValueError("UNSUPPORTED_COORDINATION_SCHEMA")
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='tracked_requests' AND type='table'").fetchone():
            return empty
        query = (
            "SELECT m.seq,m.message_id FROM tracked_requests t JOIN messages m ON m.message_id=t.request_id "
            "WHERE t.recipient=? AND m.seq>? AND NOT EXISTS "
            "(SELECT 1 FROM receipts r WHERE r.message_id=m.message_id AND r.recipient_label=t.recipient) "
            "ORDER BY m.seq LIMIT ?"
        )
        rows = db.execute(query, (participant, after_seq, limit)).fetchall()
        if not rows and after_seq:
            rows = db.execute(query, (participant, 0, limit)).fetchall()
    items = []
    for row in rows:
        ident = row["message_id"]
        try:
            envelope = read_engineering_handoff(db_path, participant=participant, request_id=ident)
            payload = envelope["payload"]
            deadline = envelope["deadline_at_utc"]
            expired = deadline is not None and accountability._time(deadline) <= moment
        except ValueError:
            # A legacy or malformed request is not actionable structured work.
            # Do not leak raw message contents or exception text.
            items.append({"request_id": ident, "status": "UNCLASSIFIED_REVIEW"})
            continue
        items.append({
            "request_id": ident, "repository": payload["repository"],
            "slice_id": payload["slice_id"], "work_type": payload["next_action"],
            "commit_sha": payload["commit_sha"],
            "status": "EXPIRED" if expired else "STRUCTURED_REVIEW",
        })
    return {"status": "OBSERVED", "items": items, "scanned": len(rows),
            "next_seq": rows[-1]["seq"] if rows else 0, "can_execute": False}
