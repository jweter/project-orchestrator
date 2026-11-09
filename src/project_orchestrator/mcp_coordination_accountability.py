"""Durable, advisory-only request/disposition ledger for issue #675.

This is a protocol, not a worker: it never starts a model, executes a task,
authorizes engineering changes, or treats a response as independent proof.
The existing mailbox identity labels are local configuration, not remote
authentication. Reads never initialize or mutate the database.
"""
from __future__ import annotations

import hashlib
import json
import re
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import mcp_coordination as mail

STATES = frozenset({"ACCEPTED", "WORKING", "BLOCKED", "NEEDS_OWNER", "DECLINED", "COMPLETED"})
TERMINAL = frozenset({"BLOCKED", "NEEDS_OWNER", "DECLINED", "COMPLETED"})
_STEPS = {
    "QUEUED": STATES - {"WORKING"},
    "ACCEPTED": STATES - {"ACCEPTED"},
    "WORKING": TERMINAL,
}
_REQUEST_ID = re.compile(r"^[0-9a-f]{32}$")
_STALE_AFTER = timedelta(hours=24)
MAX_PAGE = 100


def _reply_key(request_id: str, event_key: str) -> str:
    return "request-reply-" + hashlib.sha256(
        (request_id + ":" + event_key).encode("utf-8")
    ).hexdigest()[:42]


def _reply_row(db, request, event):
    """Verify correlation and envelope, not merely the existence of a key."""
    if event is None:
        return None
    row = db.execute(
        "SELECT * FROM messages WHERE sender_label=? AND idempotency_key=?",
        (request["recipient"], _reply_key(request["request_id"], event["event_key"])),
    ).fetchone()
    if row is None:
        return None
    if (row["recipient"] != request["sender_label"] or row["reference"] != request["reference"]
            or row["kind"] != "status"):
        return None
    try:
        payload = json.loads(row["body"])
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or any((
        payload.get("protocol") != "handoff_disposition_v1",
        payload.get("response_to") != request["request_id"],
        payload.get("disposition") != event["state"],
        payload.get("evidence_reference") != event["evidence_reference"],
        payload.get("authority") != "ADVISORY_ONLY",
        payload.get("verification") != "RECIPIENT_SELF_REPORTED",
    )):
        return None
    return row


def _time(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("INVALID_DEADLINE")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.utcoffset() is None:
            raise ValueError("NO_TZ")
        return stamp.astimezone(timezone.utc)
    except (ValueError, TypeError) as exc:
        raise ValueError("INVALID_DEADLINE") from exc


def _validate_reference(value: str) -> str:
    if not isinstance(value, str) or not value or not mail._REFERENCE.fullmatch(value):
        raise ValueError("ACTIONABLE_REFERENCE_REQUIRED")
    return value


def _validate_key(key: str) -> str:
    if not isinstance(key, str) or not mail._KEY.fullmatch(key):
        raise ValueError("INVALID_EVENT_KEY")
    return key


def _setup(path: Path) -> None:
    """Additive tables only, leaving immutable v1 mailbox and existing records untouched."""
    with closing(mail._connect(path)) as db:
        if db.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise ValueError("UNSUPPORTED_COORDINATION_SCHEMA")
        db.executescript("""
        BEGIN IMMEDIATE;
        CREATE TABLE IF NOT EXISTS tracked_requests (
            request_id TEXT PRIMARY KEY REFERENCES messages(message_id),
            sender_label TEXT NOT NULL,
            recipient TEXT NOT NULL,
            reference TEXT NOT NULL,
            deadline_at_utc TEXT,
            created_at_utc TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS request_dispositions (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT NOT NULL REFERENCES tracked_requests(request_id),
            actor_label TEXT NOT NULL,
            state TEXT NOT NULL,
            evidence_reference TEXT NOT NULL,
            event_key TEXT NOT NULL,
            reply_key TEXT NOT NULL,
            created_at_utc TEXT NOT NULL,
            UNIQUE(request_id, event_key)
        );
        CREATE TRIGGER IF NOT EXISTS tracked_requests_no_update
          BEFORE UPDATE ON tracked_requests BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_REQUEST'); END;
        CREATE TRIGGER IF NOT EXISTS tracked_requests_no_delete
          BEFORE DELETE ON tracked_requests BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_REQUEST'); END;
        CREATE TRIGGER IF NOT EXISTS request_dispositions_no_update
          BEFORE UPDATE ON request_dispositions BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_DISPOSITION'); END;
        CREATE TRIGGER IF NOT EXISTS request_dispositions_no_delete
          BEFORE DELETE ON request_dispositions BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_DISPOSITION'); END;
        COMMIT;
        """)


def open_request(path: Path, *, sender_label: str, recipient: str, body: str,
                 reference: str, idempotency_key: str,
                 deadline_at_utc: str | None = None) -> dict:
    """Post a tracked advisory with crash/replay-safe registration."""
    mail._check_principal(sender_label)
    mail._check_principal(recipient, recipient=True)
    if recipient in {"all", sender_label}:
        raise ValueError("DIRECT_DISTINCT_RECIPIENT_REQUIRED")
    _validate_reference(reference)
    _validate_key(idempotency_key)
    deadline = (_time(deadline_at_utc).isoformat() if deadline_at_utc is not None else None)
    # Messaging and tracking are two idempotent steps. Retrying after a crash
    # between them repairs registration for the exact same request.
    posted = mail.post_message(path, sender_label=sender_label, recipient=recipient,
                               kind="decision_request", body=body,
                               reference=reference, idempotency_key=idempotency_key)
    _setup(path)
    with closing(mail._connect(path)) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            old = db.execute("SELECT * FROM tracked_requests WHERE request_id=?",
                             (posted["message_id"],)).fetchone()
            if old is None:
                db.execute("INSERT INTO tracked_requests "
                           "(request_id,sender_label,recipient,reference,deadline_at_utc,created_at_utc) "
                           "VALUES (?,?,?,?,?,?)",
                           (posted["message_id"], sender_label, recipient, reference, deadline,
                            posted["created_at_utc"]))
            elif old["deadline_at_utc"] != deadline or old["reference"] != reference:
                raise ValueError("TRACKED_REQUEST_REPLAY_CONFLICT")
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
    return {"status": "QUEUED", "request_id": posted["message_id"],
            "message_seq": posted["seq"], "replayed": posted["replayed"],
            "recipient": recipient, "reference": reference, "deadline_at_utc": deadline}


def record_disposition(path: Path, *, actor_label: str, request_id: str,
                       state: str, evidence_reference: str = "",
                       event_key: str = "") -> dict:
    """Record recipient's response, then send an idempotent correlated reply.

    An event can survive a crash before its reply is posted. Calling this
    again repairs the missing reply and receipt; neither step is conflated
    with verified work or permission to execute.
    """
    mail._check_principal(actor_label)
    if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
        raise ValueError("INVALID_REQUEST_ID")
    if state not in STATES:
        raise ValueError("INVALID_DISPOSITION")
    if evidence_reference:
        _validate_reference(evidence_reference)
    if state == "COMPLETED" and not evidence_reference:
        raise ValueError("COMPLETION_REQUIRES_EVIDENCE_REFERENCE")
    key = _validate_key(event_key or hashlib.sha256(
        "\x1f".join((request_id, actor_label, state, evidence_reference)).encode("utf-8")
    ).hexdigest()[:40])
    # A request must have been explicitly tracked, not merely posted as an
    # arbitrary untrusted mailbox message.
    db_path = mail._path(path)
    if not db_path.exists():
        raise ValueError("TRACKED_REQUEST_NOT_FOUND")
    with closing(mail._connect(db_path, read_only=True)) as probe:
        if probe.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='tracked_requests'"
        ).fetchone() is None:
            raise ValueError("TRACKED_REQUEST_NOT_FOUND")
    with closing(mail._connect(db_path)) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            req = db.execute("SELECT * FROM tracked_requests WHERE request_id=?",
                             (request_id,)).fetchone()
            if req is None:
                raise ValueError("TRACKED_REQUEST_NOT_FOUND")
            if req["recipient"] != actor_label:
                raise ValueError("ONLY_REQUEST_RECIPIENT_MAY_DISPOSE")
            existing = db.execute(
                "SELECT * FROM request_dispositions WHERE request_id=? AND event_key=?",
                (request_id, key),
            ).fetchone()
            replayed = existing is not None
            if existing is not None:
                if (existing["state"], existing["evidence_reference"]) != (state, evidence_reference):
                    raise ValueError("DISPOSITION_REPLAY_CONFLICT")
            else:
                latest = db.execute(
                    "SELECT * FROM request_dispositions WHERE request_id=? ORDER BY seq DESC LIMIT 1",
                    (request_id,),
                ).fetchone()
                if latest is not None and _reply_row(db, req, latest) is None:
                    raise ValueError("PREVIOUS_DISPOSITION_REPLY_PENDING")
                old_state = latest["state"] if latest else "QUEUED"
                if state not in _STEPS.get(old_state, frozenset()):
                    raise ValueError("INVALID_DISPOSITION_TRANSITION")
                db.execute(
                    "INSERT INTO request_dispositions "
                    "(request_id,actor_label,state,evidence_reference,event_key,reply_key,created_at_utc) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (request_id, actor_label, state, evidence_reference, key,
                     _reply_key(request_id, key), datetime.now(timezone.utc).isoformat()),
                )
            reply_to = req["sender_label"]
            reference = req["reference"]
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise

    # No arbitrary instructions in the reply. Self-report is not a verified
    # GitHub outcome and never grants authority.
    reply = json.dumps({
        "protocol": "handoff_disposition_v1", "response_to": request_id,
        "disposition": state, "evidence_reference": evidence_reference,
        "authority": "ADVISORY_ONLY", "verification": "RECIPIENT_SELF_REPORTED",
    }, sort_keys=True)
    response = mail.post_message(
        path, sender_label=actor_label, recipient=reply_to, kind="status",
        body=reply, reference=reference,
        idempotency_key=_reply_key(request_id, key),
    )
    # A consumed receipt is recorded only after the answer is durably posted.
    mail.ack_message(path, recipient_label=actor_label, message_id=request_id)
    return {"status": state, "request_id": request_id,
            "response_message_id": response["message_id"],
            "replayed": replayed, "evidence_reference": evidence_reference,
            "verification": "RECIPIENT_SELF_REPORTED"}


def requests_snapshot(path: Path, *, participant: str, role: str = "incoming",
                      limit: int = 30, now: datetime | None = None) -> dict:
    """Bounded read-only status: explicit disposition vs overdue silent mailbox."""
    mail._check_principal(participant)
    if role not in {"incoming", "outgoing", "all"}:
        raise ValueError("INVALID_REQUEST_ROLE")
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE:
        raise ValueError("INVALID_PAGE_SIZE")
    db_path = mail._path(path)
    if not db_path.exists():
        return {"status": "UNINITIALIZED", "requests": [], "participant": participant}
    clock = datetime.now(timezone.utc) if now is None else now
    if clock.utcoffset() is None:
        raise ValueError("AWARE_CLOCK_REQUIRED")
    clock = clock.astimezone(timezone.utc)
    with closing(mail._connect(db_path, read_only=True)) as db:
        exists = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='tracked_requests'"
        ).fetchone()
        if not exists:
            return {"status": "NOT_CONFIGURED", "requests": [], "participant": participant}
        where = {
            "incoming": "q.recipient=?",
            "outgoing": "q.sender_label=?",
            "all": "(q.sender_label=? OR q.recipient=?)",
        }[role]
        args = (participant, participant) if role == "all" else (participant,)
        rows = db.execute(
            "SELECT q.*, d.state AS disposition, d.evidence_reference, d.created_at_utc AS disposition_at, "
            "(SELECT r.message_id FROM receipts AS r WHERE r.message_id=q.request_id "
            "AND r.recipient_label=q.recipient) AS acknowledged, d.event_key "
            "FROM tracked_requests q LEFT JOIN request_dispositions d ON d.seq=("
            "SELECT MAX(seq) FROM request_dispositions WHERE request_id=q.request_id) "
            "WHERE " + where + " ORDER BY q.rowid DESC LIMIT ?",
            (*args, limit),
        ).fetchall()
        verified_replies = {}
        for row in rows:
            if row["disposition"]:
                event = {"event_key": row["event_key"], "state": row["disposition"],
                         "evidence_reference": row["evidence_reference"]}
                response = _reply_row(db, row, event)
                verified_replies[row["request_id"]] = response["message_id"] if response is not None else None
    out = []
    for row in rows:
        created = _time(row["created_at_utc"])
        deadline = _time(row["deadline_at_utc"]) if row["deadline_at_utc"] else created + _STALE_AFTER
        due = clock > deadline
        state = row["disposition"] or ("WAITING_FOR_AGENT" if due else
                                       "DELIVERED_AWAITING_RESPONSE" if row["acknowledged"] else "QUEUED")
        out.append({
            "request_id": row["request_id"], "sender": row["sender_label"],
            "recipient": row["recipient"], "reference": row["reference"],
            "state": state, "response_at_utc": row["disposition_at"],
            "evidence_reference": row["evidence_reference"] or "",
            "recipient_acknowledged": bool(row["acknowledged"]),
            "age_seconds": max(0, int((clock - created).total_seconds())),
            "overdue": due and state not in TERMINAL,
            "deadline_at_utc": row["deadline_at_utc"],
            "verification": "RECIPIENT_SELF_REPORTED" if row["disposition"] else "NONE",
            "reply_persisted": verified_replies.get(row["request_id"]) is not None,
            "response_message_id": verified_replies.get(row["request_id"]),
            "reply_pending": bool(row["disposition"] and not verified_replies.get(row["request_id"])),
        })
    return {"status": "OK", "participant": participant, "role": role,
            "requests": out,
            "note": "Advisory only; overdue is not proof of an offline agent, and a disposition is not verified completion"}


def recover_pending_replies(path: Path, *, actor_label: str, limit: int = 10) -> dict:
    """Replay only already-recorded responses whose reply or receipt was lost.

    This is a zero-model, bounded crash-recovery operation. It does NOT
    accept fresh work or decide the disposition of unread messages.
    """
    mail._check_principal(actor_label)
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE:
        raise ValueError("INVALID_PAGE_SIZE")
    db_path = mail._path(path)
    if not db_path.exists():
        return {"status": "UNINITIALIZED", "recovered": [], "actor": actor_label}
    with closing(mail._connect(db_path, read_only=True)) as db:
        available = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='request_dispositions'"
        ).fetchone()
        if not available:
            return {"status": "NOT_CONFIGURED", "recovered": [], "actor": actor_label}
        rows = db.execute(
            "SELECT d.request_id, d.state, d.evidence_reference, d.event_key "
            "FROM request_dispositions d JOIN tracked_requests q "
            "ON q.request_id=d.request_id "
            "LEFT JOIN messages reply ON reply.sender_label=d.actor_label "
            "AND reply.idempotency_key=d.reply_key "
            "LEFT JOIN receipts rec ON rec.message_id=d.request_id "
            "AND rec.recipient_label=q.recipient "
            "WHERE d.actor_label=? AND (reply.message_id IS NULL OR rec.message_id IS NULL) "
            "ORDER BY d.seq ASC LIMIT ?",
            (actor_label, limit),
        ).fetchall()
        pending = [dict(row) for row in rows]
    recovered = []
    for row in pending:
        result = record_disposition(
            path, actor_label=actor_label, request_id=row["request_id"],
            state=row["state"], evidence_reference=row["evidence_reference"],
            event_key=row["event_key"],
        )
        recovered.append({"request_id": result["request_id"],
                          "response_message_id": result["response_message_id"]})
    return {"status": "OK", "recovered": recovered, "actor": actor_label,
            "note": "Replayed prior recorded responses only; no fresh work or authority"}
