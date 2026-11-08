"""Advisory-only SQLite inbox for cross-assistant handoffs.

No execution, owner-control, lease, product-acceptance or GitHub merge authority
is conveyed by this module. Not wired into the resident engine by default.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

RECIPIENTS = frozenset({"chatgpt", "claude-code", "iron-god", "operator", "all"})
SENDERS = frozenset({"chatgpt", "claude-code", "iron-god", "operator"})
KINDS = frozenset({"status", "handoff", "question", "evidence", "notification", "decision_request"})
MAX_MESSAGE_CHARS = 3000
MAX_REFERENCE_CHARS = 200
MAX_PAGE = 100
_KEY = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_REFERENCE = re.compile(r"^[A-Za-z0-9._:/#@+-]{0,200}$")
_SENSITIVE = re.compile(r"(?i)(?:bearer\s+\S+|-----BEGIN\s+[^-]+PRIVATE KEY-----|\b(?:sk-|ghp_|gho_|github_pat_)[a-zA-Z0-9_\-]{8,})")


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path(path: Path) -> Path:
    db_path = Path(path)
    if not db_path.is_absolute() or db_path.suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
        raise ValueError("ABSOLUTE_SQLITE_DB_PATH_REQUIRED")
    if not db_path.parent.is_dir():
        raise ValueError("COORDINATION_DB_PARENT_MISSING")
    return db_path


def _check_principal(principal: str, *, recipient: bool = False) -> str:
    expected = RECIPIENTS if recipient else SENDERS
    if principal not in expected:
        raise ValueError("UNRECOGNIZED_ADVISORY_LABEL")
    return principal


def _connect(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    db_path = _path(path)
    if read_only:
        # Read-only opens never create or modify a database or journal.
        uri = f"file:{db_path.as_posix()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=10, isolation_level=None)
        connection.execute("PRAGMA busy_timeout=10000")
    else:
        # Two MCP clients can first-create one database at the same instant.
        # SQLite's busy_timeout does not serialize simultaneous changes to
        # journal_mode on a not-yet-initialized file. Retry that narrow race
        # with fresh connections, and fail closed after a bounded interval.
        for attempt in range(20):
            connection = sqlite3.connect(str(db_path), timeout=10, isolation_level=None)
            connection.execute("PRAGMA busy_timeout=10000")
            try:
                if str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower() != "wal":
                    connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA synchronous=FULL")
                break
            except sqlite3.OperationalError as exc:
                connection.close()
                if "locked" not in str(exc).lower() or attempt == 19:
                    raise
                time.sleep(0.03 * (attempt + 1))
    connection.execute("PRAGMA foreign_keys=ON")
    connection.row_factory = sqlite3.Row
    return connection


def initialize(path: Path) -> None:
    """Initialize only the advisory DB; never touch the existing lease DB."""
    with closing(_connect(path)) as db:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            raise ValueError("UNSUPPORTED_COORDINATION_SCHEMA")
        # A single IMMEDIATE transaction prevents concurrent clients from
        # observing a half-created schema after interrupted initialization.
        db.executescript("""
        BEGIN IMMEDIATE;
        CREATE TABLE IF NOT EXISTS messages (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT NOT NULL UNIQUE,
            sender_label TEXT NOT NULL,
            recipient TEXT NOT NULL,
            kind TEXT NOT NULL,
            body TEXT NOT NULL,
            reference TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            created_at_utc TEXT NOT NULL,
            UNIQUE(sender_label, idempotency_key)
        );
        CREATE TABLE IF NOT EXISTS receipts (
            message_id TEXT NOT NULL REFERENCES messages(message_id),
            recipient_label TEXT NOT NULL,
            acknowledged_at_utc TEXT NOT NULL,
            PRIMARY KEY (message_id, recipient_label)
        );
        CREATE TRIGGER IF NOT EXISTS messages_immutable_update
          BEFORE UPDATE ON messages BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_MESSAGE'); END;
        CREATE TRIGGER IF NOT EXISTS messages_immutable_delete
          BEFORE DELETE ON messages BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_MESSAGE'); END;
        CREATE TRIGGER IF NOT EXISTS receipts_immutable_update
          BEFORE UPDATE ON receipts BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_RECEIPT'); END;
        CREATE TRIGGER IF NOT EXISTS receipts_immutable_delete
          BEFORE DELETE ON receipts BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_RECEIPT'); END;
        PRAGMA user_version=1;
        COMMIT;
        """)


def _message(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in ("seq", "message_id", "sender_label", "recipient", "kind",
                                      "body", "reference", "idempotency_key", "created_at_utc")}


def post_message(path: Path, *, sender_label: str, recipient: str, kind: str,
                 body: str, idempotency_key: str, reference: str = "") -> dict:
    """Durably append advisory text; idempotent even across process restarts."""
    _check_principal(sender_label)
    _check_principal(recipient, recipient=True)
    if kind not in KINDS:
        raise ValueError("UNRECOGNIZED_MESSAGE_KIND")
    if not isinstance(body, str) or not body.strip() or len(body) > MAX_MESSAGE_CHARS:
        raise ValueError("INVALID_MESSAGE_BODY")
    if _SENSITIVE.search(body):
        raise ValueError("LIKELY_SECRET_IN_MESSAGE")
    if not isinstance(idempotency_key, str) or not _KEY.fullmatch(idempotency_key):
        raise ValueError("INVALID_IDEMPOTENCY_KEY")
    if not isinstance(reference, str) or not _REFERENCE.fullmatch(reference):
        raise ValueError("INVALID_MESSAGE_REFERENCE")
    initialize(path)
    with closing(_connect(path)) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            existing = db.execute(
                "SELECT * FROM messages WHERE sender_label=? AND idempotency_key=?",
                (sender_label, idempotency_key),
            ).fetchone()
            if existing is not None:
                if (existing["recipient"], existing["kind"], existing["body"], existing["reference"]) != (
                    recipient, kind, body, reference
                ):
                    raise ValueError("IDEMPOTENCY_CONFLICT")
                result = {**_message(existing), "replayed": True}
            else:
                message_id = uuid.uuid4().hex
                db.execute(
                    "INSERT INTO messages (message_id, sender_label, recipient, kind, body, reference, "
                    "idempotency_key, created_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (message_id, sender_label, recipient, kind, body, reference, idempotency_key, _utc()),
                )
                result = {**_message(db.execute(
                    "SELECT * FROM messages WHERE message_id=?", (message_id,)
                ).fetchone()), "replayed": False}
            db.execute("COMMIT")
            return result
        except BaseException:
            db.execute("ROLLBACK")
            raise


def list_messages(path: Path, *, recipient_label: str, after_seq: int = 0,
                  limit: int = 30) -> dict:
    _check_principal(recipient_label)
    if not isinstance(after_seq, int) or after_seq < 0:
        raise ValueError("INVALID_CURSOR")
    if not isinstance(limit, int) or not 1 <= limit <= MAX_PAGE:
        raise ValueError("INVALID_PAGE_SIZE")
    db_path = _path(path)
    if not db_path.exists():
        return {"status": "UNINITIALIZED", "messages": [], "next_seq": after_seq}
    with closing(_connect(db_path, read_only=True)) as db:
        rows = db.execute(
            "SELECT m.*, (r.message_id IS NOT NULL) AS acknowledged "
            "FROM messages AS m LEFT JOIN receipts AS r ON r.message_id=m.message_id "
            "AND r.recipient_label=? WHERE m.seq>? AND (m.recipient=? OR m.recipient='all') "
            "ORDER BY m.seq ASC LIMIT ?",
            (recipient_label, after_seq, recipient_label, limit),
        ).fetchall()
    messages = [{**_message(row), "acknowledged": bool(row["acknowledged"])} for row in rows]
    return {"status": "OK", "messages": messages,
            "next_seq": messages[-1]["seq"] if messages else after_seq}


def ack_message(path: Path, *, recipient_label: str, message_id: str) -> dict:
    """Append a consumption receipt; never releases any ownership claim."""
    _check_principal(recipient_label)
    if not isinstance(message_id, str) or not re.fullmatch(r"[a-f0-9]{32}", message_id):
        raise ValueError("INVALID_MESSAGE_ID")
    db_path = _path(path)
    if not db_path.exists():
        raise ValueError("MESSAGE_NOT_FOUND")
    with closing(_connect(db_path)) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            message = db.execute("SELECT recipient FROM messages WHERE message_id=?", (message_id,)).fetchone()
            if message is None:
                raise ValueError("MESSAGE_NOT_FOUND")
            if message["recipient"] not in {recipient_label, "all"}:
                raise ValueError("RECIPIENT_MISMATCH")
            old = db.execute("SELECT acknowledged_at_utc FROM receipts "
                             "WHERE message_id=? AND recipient_label=?",
                             (message_id, recipient_label)).fetchone()
            if old is None:
                at = _utc()
                db.execute("INSERT INTO receipts (message_id,recipient_label,acknowledged_at_utc) "
                           "VALUES(?,?,?)", (message_id, recipient_label, at))
            else:
                at = old["acknowledged_at_utc"]
            db.execute("COMMIT")
            return {"message_id": message_id, "recipient_label": recipient_label,
                    "acknowledged_at_utc": at, "replayed": old is not None}
        except BaseException:
            db.execute("ROLLBACK")
            raise


def targets_snapshot(repo_root: Path) -> dict:
    """Versioned targets, not a claim of fresh product acceptance."""
    path = Path(repo_root) / "engineering" / "product-usability-gates.json"
    if not path.is_file() or path.stat().st_size > 32_000:
        return {"status": "UNAVAILABLE", "products": {}}
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        products = record["products"]
        if not isinstance(products, dict):
            raise ValueError("invalid products")
        return {"status": "POLICY_SNAPSHOT_NOT_LIVE_PROOF",
                "objective": str(record.get("objective", "")),
                "products": {
                    name: {key: details.get(key) for key in ("current_state", "next_gate", "gate")}
                    for name, details in products.items() if isinstance(name, str)
                    and isinstance(details, dict) and len(name) <= 60
                }}
    except (ValueError, KeyError, TypeError, OSError):
        return {"status": "UNAVAILABLE", "products": {}}


def engine_snapshot(state_root: Path) -> dict:
    """Allowlisted non-sensitive reports, not process-liveness or owner-authority proof."""
    root = Path(state_root)
    sources = {
        "product": root / "iron-god-products" / "lane-status.json",
        "canon": root / "iron-god-canon" / "lane-status.json",
        "supervisor": root / "supervisor-generation-heartbeat.json",
    }
    permitted = ("status", "observed_at_utc", "heartbeat_at_utc", "repository",
                 "packet_id", "backlog_item", "actor_revision", "worker_status")
    values = {}
    for label, path in sources.items():
        if not path.is_file():
            values[label] = {"status": "UNAVAILABLE"}
            continue
        try:
            if path.stat().st_size > 128_000:
                raise ValueError("oversized status")
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("invalid status")
            values[label] = {key: payload[key] for key in permitted
                             if key in payload and isinstance(payload[key], (str, int, float, bool))}
            values[label]["evidence"] = "REPORTED_NOT_INDEPENDENTLY_VERIFIED"
        except (ValueError, OSError):
            values[label] = {"status": "UNAVAILABLE"}
    return {"sources": values, "note": "reported metadata only; no authenticated owner/control grant assertions"}
