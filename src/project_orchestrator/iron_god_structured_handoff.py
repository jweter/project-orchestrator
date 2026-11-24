"""Versioned engineering handoff envelopes, using the existing tracked request ledger.

Validation establishes shape and scope only. GitHub evidence and owner authorization
must be checked independently before any proposed work is executed.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from contextlib import closing

from . import iron_god_observability as observability
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
